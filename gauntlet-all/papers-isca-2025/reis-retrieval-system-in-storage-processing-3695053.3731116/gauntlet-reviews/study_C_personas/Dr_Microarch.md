# REIS: In-Storage Processing for RAG Retrieval

## Q1: Whiteboard Explanation

Let me walk you through what REIS actually does at the hardware level.

**The Problem Setup:**
RAG (Retrieval-Augmented Generation) needs to find documents similar to a user query from a massive database. The bottleneck? Loading that database from the SSD to host memory. Section 3.1's Figure 2 shows dataset loading accounts for **84% of total latency** on the wiki_en dataset (172.82 seconds total).

**The Core Insight:**
Instead of moving terabytes of embedding vectors to the CPU for distance calculations, do the distance computation *inside* the SSD using existing NAND flash circuitry.

**The Wiring (Figure 6):**

```
Host → [Query Embedding] → SSD Controller DRAM
                                    ↓
                            Flash Controller
                                    ↓
            ┌───────────────────────┴───────────────────────┐
            ↓                                               ↓
        Plane #0                                        Plane #1
    ┌─────────────┐                                ┌─────────────┐
    │ Cache Latch │ ← Query (duplicated)          │ Cache Latch │
    │ Sensing L.  │ ← Database embeddings (read)  │ Sensing L.  │
    │ Data Latch  │ ← XOR result                  │ Data Latch  │
    │ Fail-Bit Cnt│ → Distance (popcount)         │ Fail-Bit Cnt│
    └─────────────┘                                └─────────────┘
```

**Step-by-step execution:**

1. **Input Broadcasting (Step 1):** The query embedding (binary quantized to ~128 bytes for 1024 dimensions) is written to the Cache Latch of every plane, duplicated to fill the 16KB page buffer

2. **Page Read (Step 2):** Database embeddings are read from NAND into the Sensing Latch (SL)

3. **XOR (Step 3):** The die's existing XOR logic performs `CL ⊕ SL → DL`. For binary vectors, XOR gives you the Hamming distance bits

4. **Popcount (Step 4):** The **fail-bit counter** (existing ISPP/ISPE circuitry per Section 2.3, Figure 1 item 14) counts the '1's in the Data Latch. This *is* the Hamming distance

5. **Filter & Transfer (Step 5):** The **pass/fail checker** (item 15 in Figure 1) compares distance against a threshold. Only embeddings below threshold are transferred to the controller's DRAM

6. **Selection:** The embedded ARM Cortex-R8 cores run quickselect on the filtered results

**The Algorithm Choice:**
They deliberately chose IVF (Inverted File) over HNSW because IVF has *sequential* access patterns within clusters—you read contiguous pages. HNSW requires pointer-chasing through a graph, causing random reads that kill internal bandwidth utilization (Section 4.2, Figure 5).

**The Hybrid SSD Trick (Section 4.1.2):**
Binary embeddings are stored in **SLC mode with Enhanced SLC Programming (ESP)** which achieves "zero BER" (Section 4.1.2). This eliminates ECC entirely for embeddings, so data never needs to leave the die for error correction. Documents are stored in TLC (3 bits/cell) for density.

---

## Q2: The Key Insight

**The "Magic Trick":**
REIS repurposes the **ISPP fail-bit counter**—circuitry that already exists in every NAND flash die to verify programming success—as a population count (popcount) unit for computing Hamming distances.

This is elegant because:
1. The fail-bit counter is designed to count '1' bits across thousands of cells simultaneously
2. Hamming distance between binary vectors = popcount(A XOR B)
3. The XOR between latches already exists for data randomization (Section 2.3)

**Why this matters structurally:**
Prior ISP-ANNS work like ICE [106] requires storing data in a special error-tolerant format with **32× storage overhead** (Section 3.2) because they compute *inside* unreliable cells. REIS sidesteps this by:
- Using ESP for the SLC partition to achieve 0 BER (no ECC needed)
- Computing in the *latches* (reliable SRAM), not the cells themselves
- Only reading data once into latches, then operating on latch contents

**The structural delta from baseline:**
A standard SSD reads pages → transfers to controller → ECC decode → transfer to host.

REIS reads pages → computes XOR in latches → counts bits with existing logic → filters → transfers only surviving distances (~1% of data per Section 4.3.3) → controller does selection.

The bandwidth reduction is multiplicative: from 16KB per page down to a few bytes (distance + address) per surviving embedding.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Realistic baseline hardware:** They compare against actual hardware—an AMD EPYC 9554 (256 cores, 3.1GHz) with 1.5TB DDR4 (Table 3)—not a weak strawman.

2. **End-to-end RAG measurement (Table 4):** They show the *actual* RAG pipeline breakdown, demonstrating that REIS reduces retrieval from 69.3% of latency down to 0.15% for wiki_en. The new bottleneck becomes LLM generation (92%), which is the correct outcome.

3. **Multiple SSD configurations:** Testing both cost-oriented (PM9A3-based) and performance-oriented (Micron 9400-based) configurations (Table 3) shows generality across real product designs.

4. **Fair comparison to prior ISP work:** The comparison to ICE [106] accounts for ICE's storage overhead, and they even construct an "ICE-ESP" idealized baseline without ECC overhead (Section 6.4). REIS still wins by 2-3× against this idealized competitor.

5. **Sensitivity analysis decomposition (Figure 9):** Breaking down Distance Filtering (DF), Pipelining (PL), and Multi-Plane Input Broadcasting (MPIBC) contributions separately shows which optimizations matter most (DF provides 4.7-5.7× alone).

### Weaknesses

1. **Recall@10 is cherry-picked:** They evaluate at k=10, but don't show how performance scales with k. RAG often uses k=100 or higher for reranking pipelines. The distance filtering (which provides the dominant speedup) may become less effective as k increases.

2. **Binary quantization limits dataset applicability:** BQ achieves 0.97 recall only for high-dimensional text embeddings (768-8192 dims, per Section 2.1). For image embeddings (128-512 dims typical), BQ recall drops significantly. They don't evaluate any multi-modal or image retrieval workloads.

3. **The "No-I/O" comparison is misleading:** In Figure 7, "No-I/O" represents CPU performance with zero storage I/O overhead. But REIS only beats No-I/O by 1.8× on average—meaning most of REIS's benefit (13×) comes from eliminating I/O, not from computational advantages. A CXL-attached memory pool would capture similar benefits.

4. **Write path completely ignored:** Section 7.2 handwaves that "REIS primarily targets read-intensive RAG workloads." But RAG databases need indexing (writes) and updates. They provide no performance numbers for IVF_Deploy() or database updates.

5. **Contiguity requirement is non-trivial:** Section 4.1.4 admits REIS requires "a large contiguous block of storage, which may necessitate defragmentation." For a production SSD serving multiple workloads, this fragmentation overhead could be substantial but is neither measured nor modeled.

6. **Single-query latency vs throughput:** All numbers are QPS (throughput). They never report single-query latency, which matters for interactive RAG applications. The pipeline depth required for high throughput may hurt tail latency.

---

## Q4: What the Authors Didn't Tell You

### The Hidden Hardware Costs

1. **Multi-Plane Input Broadcasting (MPIBC) is not free:** Section 4.3.4 casually states "We assume the plane selection is handled by a dedicated Multiplexer logic within the die periphery" and MPIBC "requires raising the select signal for all planes together." Standard NAND dies don't support this—plane operations are serialized through a shared I/O bus. This is a hardware modification they're hiding behind the word "assume."

2. **The embedded core utilization conflict:** Section 4.3.4 claims "REIS only uses one core for Quicksort and reranking, while the other cores (e.g., 3 out of 4) are still available for regular SSD operations." But Section 5 says they model the cores as "Cortex R8"—a processor with no floating-point unit. Running INT8 reranking on this core involves software integer MAC operations. They don't show the embedded core becomes the bottleneck at high QPS.

3. **The R-IVF DRAM footprint scales poorly:** Section 4.2.1 states R-IVF requires `Number_of_entries × 15B` in SSD DRAM. For the wiki_full dataset (hundreds of millions of entries), this is gigabytes—but SSDs typically have 0.1% DRAM to capacity ratio (1GB per TB, Section 2.3). A 1TB SSD can't hold the R-IVF structure for wiki_full.

4. **ESP doesn't actually achieve "zero BER":** Section 4.1.2 cites Flash-Cosmos [224] for "zero BER without ECC." But [224] achieves this only under specific conditions (1-year retention, 10K P/E cycles, specific temperature). Real SSDs operate across wider conditions. They're assuming best-case reliability.

5. **The OOB area repurposing breaks error correction:** Section 4.1.3 repurposes "0.7% of the OOB area" for embedding-document linkage. But OOB area is sized precisely for ECC parity bits. Stealing bytes means either reducing ECC strength for documents (stored in TLC) or requiring larger pages. They don't discuss this tradeoff.

6. **Distance filtering threshold selection is hand-wavy:** Section 4.3.3 says they "perform filtering experiments on 4 BEIR datasets" and found they "can filter out 99% of documents." But the threshold depends on embedding distribution, which varies by embedding model and domain. They provide no mechanism for adaptive threshold selection.

7. **The "Mini-Page" addressing adds FTL complexity:** Section 4.3.2 introduces Mini-Pages with "7-bit offset" appended to physical addresses. This is a non-standard addressing mode that requires firmware changes to the FTL—but they claim "no hardware modifications."

### What They're Actually Selling

The paper's central claim is "no hardware modifications" (Abstract, Section 1, multiple times). But they require:
- Modified plane select logic (MPIBC)
- New NAND command set (Table 2: IBC, XOR, GEN_DIST, RD_TTL)
- ESP programming mode (firmware change)
- OOB area repurposing (format change)
- Mini-Page addressing (FTL change)

What they mean is "no additional silicon." The logic is repurposed, but the die control FSM, firmware, and command protocol all need modification. The "no hardware modifications" framing is technically true but misleading—Samsung or Micron would need to design REIS into their next-gen controller ASIC.