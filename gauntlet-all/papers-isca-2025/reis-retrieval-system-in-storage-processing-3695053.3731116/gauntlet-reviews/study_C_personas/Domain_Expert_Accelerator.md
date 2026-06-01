# REIS: A High-Performance and Energy-Efficient Retrieval System with In-Storage Processing

## Q1: Whiteboard Explanation

Imagine you're building a system where an LLM needs to answer questions using a massive external knowledge base—like a Wikipedia dump with 41.5 million document chunks. The problem is painfully simple: **before the LLM can generate anything, you need to find the relevant documents.** This is Retrieval-Augmented Generation (RAG).

Here's the current disaster: You have documents stored as embedding vectors on an SSD. When a query comes in, you must:
1. Load all those embeddings from the SSD into host DRAM
2. Compute similarity (distance) between query and each embedding
3. Pick the top-k most similar ones
4. Fetch the actual document text for those k winners
5. Feed them to the LLM

The authors measured this and found that **Step 1—just loading the data—takes 84% of the entire pipeline time** for the wiki_en dataset (Figure 2, Section 3.1). You're moving terabytes over PCIe just to do some dot products and throw most of the data away.

**REIS's Core Idea:** Don't move the data. Move the *computation* into the SSD.

Think of a modern SSD as not just a dumb storage device, but a system with:
- Multiple NAND flash chips with internal parallelism (channels, dies, planes)
- Page buffers with latches that can do XOR operations
- Fail-bit counters that can count 1s in a bitstring
- Embedded ARM cores in the controller

REIS exploits these existing components to perform similarity search *inside* the SSD:

1. **Binary Quantization:** Compress each embedding from FP32 to 1-bit per dimension. A 1024-dimension embedding becomes 128 bytes (Section 4.3).

2. **XOR + Popcount = Hamming Distance:** To compute similarity between a query and stored embedding, XOR them (using existing latch circuitry, Step 3 in Figure 6) and count the 1s (using the fail-bit counter normally used for NAND programming verification, Step 4). No MAC units needed.

3. **IVF Algorithm:** Instead of graph-based search (HNSW), which has irregular access patterns that kill SSD parallelism, they use the cluster-based Inverted File algorithm. Coarse search finds nearest cluster centroids, fine search scans within those clusters—all sequential, all parallel across planes (Section 4.2).

4. **Embedding-Document Linkage:** Store the address of each document chunk in the Out-Of-Band (OOB) area of the flash page alongside its embedding. When you find the winning embedding, the document address is already in the page buffer—no separate lookup (Section 4.1.3).

5. **Hybrid SSD:** Use reliable SLC (Enhanced SLC Programming) for embeddings that need error-free in-die computation, and dense TLC for document storage (Section 4.1.2).

The result: the SSD receives a query embedding, computes similarity search internally, and returns only the top-k document chunks. Data movement drops from gigabytes to kilobytes per query.

---

## Q2: The Key Insight

**The Real Innovation:** The authors recognized that the **computational primitives required for binary vector similarity search (XOR and popcount) already exist inside NAND flash dies** as infrastructure for programming verification and data randomization—they just needed to repurpose them.

Specifically:
- The **latch-to-latch XOR logic** (used for on-chip data randomization, reference [106] in Section 2.3) computes the bitwise difference between query and stored embeddings
- The **fail-bit counter** (used during ISPP to check if cells reached target voltage, Section 2.3, references [48, 52, 203]) counts the Hamming distance

This is the magic trick: **they're getting similarity computation for "free" from circuitry that's already there for reliability purposes.**

The second critical insight is **algorithmic fit.** Prior ISP-ANNS work used graph-based algorithms (HNSW, DiskANN) which exhibit "irregular data access patterns that underutilize the internal bandwidth of the SSD due to costly channel and NAND Flash chip conflicts" (Section 3.2). The Inverted File (IVF) algorithm, in contrast, organizes data into clusters that can be scanned sequentially—perfectly matching the streaming access patterns that SSDs are optimized for.

**What distinguishes this from prior work:**

| Prior Work | Limitation | REIS Solution |
|------------|------------|---------------|
| NDSearch [299] | Graph algorithms → irregular access | IVF → sequential streaming |
| ICE [106] | 8-32× storage overhead for error tolerance | ESP + SLC → 0 BER without overhead |
| DeepStore [192] | Custom systolic arrays | Existing SSD circuitry only |
| All prior ISP-ANNS | Search only, not document retrieval | OOB linkage for end-to-end RAG |

The paper explicitly claims: "REIS does not introduce any hardware modifications to the storage system" (Abstract). This is the key differentiator—they're achieving acceleration purely through firmware/algorithm changes on existing SSD architectures.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. The Bottleneck Analysis is Convincing (Section 3.1, Figures 2-3)**

The authors properly characterize the end-to-end RAG pipeline before proposing solutions. Figure 2 shows dataset loading is 49-84% of execution time across two datasets. Even with Binary Quantization (Figure 3), it's still 20-67%. This isn't cherry-picked kernel profiling—it's the full application stack with real models (RoBERTa, Llama 3.2 1B).

**2. Multiple Baselines with Appropriate Systems (Table 3)**

They compare against:
- CPU-Real: A genuine high-end server with 256 cores (AMD EPYC 9554, 1.5TB DRAM)
- Two SSD configurations (cost-optimized PM9A3, performance-optimized Micron 9400)
- Prior ISP work (ICE, NDSearch) with methodology adjustments for fairness

The 13× average speedup and 55× energy efficiency improvement (Section 6.1, Figures 7-8) are measured against this legitimate baseline.

**3. End-to-End RAG Evaluation (Table 4)**

Table 4 breaks down the *entire* RAG pipeline, showing REIS reduces the combined Dataset Loading + Search contribution from 20-69% down to 0.02-0.15%. Overall end-to-end latency improves 1.25-3.24×. This is honest—the speedup isn't 13× end-to-end because generation still dominates. They explicitly state "Generation accounts for 92% of the total time" after REIS, acknowledging they've shifted the bottleneck.

**4. Sensitivity Study Properly Isolates Contributions (Figure 9)**

They progressively add Distance Filtering, Pipelining, and Multi-Plane IBC, showing each contribution independently. DF provides 4.7-5.7× of the total speedup.

### Weaknesses

**1. The "No Hardware Modifications" Claim Needs Scrutiny**

While they don't add new transistors, they require:
- Extended NAND flash command set (Table 2): IBC, XOR, GEN_DIST, RD_TTL commands
- New API extensions to NVM command set (Table 1)
- Firmware changes to flash die control logic state machines (Section 4.4.2)
- Enhanced SLC Programming (ESP) support

A more accurate claim would be "no *silicon* modifications." The firmware complexity is non-trivial. SSD manufacturers would need to implement these custom commands—this isn't a drop-in solution.

**2. ESP Reliability Assumption is Critical but Under-Explored**

The entire design hinges on ESP achieving "zero BER without ECC" (Section 4.1.2). They cite Flash-Cosmos [224] for this claim. But:
- The evaluation uses *simulation* based on Flash-Cosmos parameters, not real silicon
- Section 7.2 states "worst-case scenario (1-year retention, 10k P/E cycles)"—what about 3-year retention at high temperatures?
- If ESP fails, you need ECC, which requires data transfer to the controller, which defeats the purpose

The comparison to REIS-ASIC (Section 6.3.1) shows 4.1-6.5× slowdown without ESP—meaning ESP is not an optimization, it's load-bearing.

**3. The Recall@10 Target May Be Insufficient for Production RAG**

All IVF results sweep recall from 0.90-0.98 (Section 6.1). But:
- 0.90 Recall@10 means 10% of relevant documents are missed on average
- For medical, legal, or financial RAG applications (cited in Section 1), this could be problematic
- The paper doesn't evaluate Recall@100 or other metrics common in RAG literature

**4. Document Retrieval Overhead is Partially Hidden**

Section 4.1.3 elegantly links embeddings to documents via OOB. But:
- Documents are stored in TLC with ECC (not ESP)
- Document retrieval still requires transfer over the PCIe bus
- For top-10 retrieval with 4KB chunks, this is only 40KB—negligible. But for larger context windows (e.g., top-100 with 16KB chunks), it becomes 1.6MB per query

**5. Comparison to NDSearch Uses Different Datasets (Figure 11)**

For NDSearch comparison, they switch to SIFT-1B and DEEP-1B instead of the RAG-relevant BEIR datasets used elsewhere. These are computer vision embedding datasets with different dimensionality and distribution characteristics. The 1.7× average speedup over NDSearch is less compelling without same-dataset comparison.

---

## Q4: What the Authors Didn't Tell You

**1. The FTL Bypass Has Sharp Edges**

Section 4.1.4 describes "coarse-grained access" that eliminates page-level FTL lookups. But this requires:
- Contiguous physical space allocation at deployment time
- "Defragmentation operations" (their words) if space isn't available
- Database isolation from normal storage operations

They handwave this as "an initial upfront overhead that can be amortized over time" but don't quantify it. For a production system serving multiple RAG databases simultaneously (the stated use case in Section 7.2), this could be a significant operational burden.

**2. Multi-Tenancy is Essentially Unsupported**

REIS operates "exclusively in either RAG-mode or normal SSD mode at any given time" (Section 7.2). You cannot run normal storage I/O while serving RAG queries. For cloud deployment, this means dedicated SSDs per RAG workload—significant TCO implications they don't discuss.

**3. The Embedded Core is More Loaded Than They Suggest**

They claim "one core can efficiently run Quicksort and reranking without stalling the pipeline" (Section 4.3.4). But the embedded core (Cortex R8, [13]) also must:
- Coordinate all IBC (Input Broadcasting) operations
- Manage the Temporal Top Lists in DRAM
- Handle API command translation
- Execute the IVF cluster selection logic

The paper provides no CPU utilization analysis or breakdown of what happens when multiple queries arrive concurrently.

**4. The INT8 Reranking Step is Quietly Expensive**

The design uses Binary Quantization for initial search, then "reranking performs using INT8 embeddings" (Section 4.3.2, Step 7). This means:
- INT8 embeddings must also be stored (doubling embedding storage in the embedding region)
- The top-10k candidates must be re-read from TLC (with ECC overhead)
- The embedded core must compute INT8 distances

Section 4.2.1 confirms "two other regions for storing embeddings in binary and INT8 precision, respectively." The storage overhead is real but not highlighted in the headline numbers.

**5. The 13× Speedup Drops Significantly for High Recall**

Looking carefully at Figure 7:
- At BF (brute force), REIS-SSD2 achieves ~100× speedup
- At IVF Recall@10=0.98, it drops to ~20-30× depending on dataset
- At IVF Recall@10=0.90, it's ~10-15×

The "13× average" headline number mixes brute force and IVF results. For the operationally relevant IVF-with-high-recall scenario, gains are more modest.

**6. They Compared Against a Strawman CPU Configuration**

Table 3 shows the CPU baseline has 128 cores with 3.1GHz frequency. But:
- They use FAISS for CPU evaluation, which is highly optimized
- No mention of AVX-512 or VNNI utilization
- No comparison against optimized Binary Quantization on CPU (which FAISS supports)

The No-I/O comparison (Figure 7) shows REIS only beats the CPU by 1.8× average when I/O is eliminated—suggesting the CPU compute baseline is actually quite strong, and most of REIS's benefit really is just avoiding data movement.

**7. Amdahl's Law Coverage for Full Applications**

Even with REIS, Table 4 shows 92% of latency is in Generation. If you improve retrieval infinitely, you can only get ~1.09× total speedup. The paper is honest about this, but it raises the question: **is optimizing retrieval the right priority compared to LLM inference optimization?** The paper doesn't engage with this tradeoff.