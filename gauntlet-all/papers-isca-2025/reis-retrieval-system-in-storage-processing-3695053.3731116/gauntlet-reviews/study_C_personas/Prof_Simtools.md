# REIS: In-Storage Processing for RAG Retrieval

## Q1: Whiteboard Explanation

Let me walk you through what REIS actually does, and more importantly, *how* it was simulated.

**The Problem Being Solved:**
RAG (Retrieval-Augmented Generation) pipelines have a brutal I/O bottleneck. When you query an LLM with RAG, the system must load massive embedding databases from storage to find relevant documents. The paper's Figure 2 shows this clearly: for the wiki_en dataset (41.5M entries), **84% of RAG pipeline latency is just loading the dataset from SSD to host memory**. That's 145 seconds out of 172 seconds spent just moving bytes.

**The Core Idea:**
Instead of moving all that data to the CPU for processing, do the similarity search *inside the SSD* using hardware that already exists there. The key insight is that NAND flash dies already have:
- Page buffers (sensing latches, cache latches, data latches)
- XOR logic between latches
- Fail-bit counters in peripheral logic

REIS repurposes these existing components to compute Hamming distances on binary-quantized embeddings *without* transferring data out of the flash dies.

**The Execution Flow (Figure 6):**
1. Query embedding is broadcast to all planes via "Input Broadcasting"
2. Database page is read into sensing latch
3. XOR between query (cache latch) and database embeddings (sensing latch) → stored in data latch
4. Fail-bit counter counts the 1s (this IS the Hamming distance)
5. Only embeddings passing a distance threshold are transferred to the SSD controller
6. Controller's embedded cores run quickselect to find top-k
7. INT8 reranking on the narrowed candidates
8. Document chunks retrieved using OOB-stored addresses

**Simulation Infrastructure:**
The evaluation combines multiple simulators:
- **Flash-Cosmos** [224] for NAND flash operation modeling and timing
- **CACTI7** [18] for internal SSD DRAM modeling
- **Zsim** [252] + **Ramulator** [57, 150] for embedded SSD controller cores
- Power consumption derived from commodity SSD specs [249] and Flash-Cosmos characterization

The baseline CPU system (CPU-Real) uses actual hardware measurements: AMD EPYC 9554 with AMD µProf for power, Samsung PM9A3 SSD (Table 3).

---

## Q2: The Key Insight

The crucial insight isn't just "do computation in storage" — that's been proposed before. **The key insight is that the IVF (Inverted File) algorithm's access patterns are fundamentally ISP-friendly, while graph-based algorithms (HNSW, DiskANN) are not.**

Graph-based ANNS algorithms like HNSW perform sequential graph traversal where each hop depends on analyzing the current vertex. This creates **irregular, data-dependent access patterns** (Sec. 3.2 explicitly cites [75, 76] on this). These patterns cause channel and flash chip conflicts, killing the parallelism that makes ISP worthwhile.

IVF, in contrast, organizes embeddings into clusters that can be scanned *contiguously*. Once you identify which clusters to search (coarse-grained phase), you can blast through all embeddings in those clusters in parallel across all planes and dies. This is a streaming access pattern — exactly what flash memory is optimized for.

The paper validates this in Figure 5: while HNSW beats IVF in raw CPU throughput (3× faster), when you add Binary Quantization, IVF throughput jumps dramatically while HNSW stays flat. This is because BQ reduces IVF to simple Hamming distance (XOR + popcount), which maps perfectly to existing flash die logic.

**The deeper insight:** Prior ISP-ANNS work (NDSearch [299], ICE [106]) either used the wrong algorithm for ISP or required hardware modifications. REIS shows you can get 13× speedup over a 256-core CPU system using *zero hardware changes* if you pick the right algorithm.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Hybrid simulation + real measurements approach:**
The methodology (Section 5) uses real hardware for the baseline (AMD EPYC 9554, Samsung PM9A3) with AMD µProf for CPU power measurements. This grounds the comparison in reality. The SSD modeling uses Flash-Cosmos [224], which is based on real flash chip characterization, not synthetic parameters.

**2. Multiple SSD configurations:**
They evaluate two configurations (REIS-SSD1 based on Samsung PM9A3, REIS-SSD2 based on Micron 9400) with different channel counts (8 vs 16), planes (2 vs 4), and bandwidths (1.2 vs 2.0 GB/s). Table 3 specifies all parameters. This isn't just one magical configuration.

**3. Comparison against prior ISP work:**
Figure 10 shows REIS vs ICE [106], and Figure 11 shows REIS vs NDSearch [299]. They even create ICE-ESP (Section 6.3.1), an idealized ICE without ECC overhead, and still show REIS wins by 2-3×. This is rigorous comparative analysis.

**4. Sensitivity analysis with ablations:**
Figure 9 breaks down the contribution of each optimization (DF, PL, MPIBC). Distance Filtering contributes 4.7-5.7× speedup alone. This tells you where the wins actually come from.

**5. End-to-end RAG evaluation:**
Table 4 shows latency breakdown across the full RAG pipeline (encoding, search, generation), not just the isolated ANNS kernel. They demonstrate that generation becomes the new bottleneck at 92% — proving they've actually shifted the bottleneck.

### Weaknesses

**1. No cycle-accurate SSD controller simulation:**
The embedded cores are modeled with Zsim+Ramulator, but the paper doesn't specify whether this is full-system or trace-driven. More critically, the SSD controller is a complex system with firmware overhead, DMA engines, and interrupt handling. The claim that "a single core can efficiently run Quicksort and reranking without stalling the pipeline" (Section 4.3.4) needs validation against real controller constraints.

**2. ESP (Enhanced SLC-mode Programming) assumptions:**
The entire REIS design hinges on ESP [224] achieving "zero BER without ECC" (Section 4.1.2). This is a strong claim from a single paper. What happens if ESP doesn't achieve zero BER under real workloads? The comparison to REIS-ASIC (Section 6.3.1) shows a 4-6× slowdown if ECC is needed — this is a significant vulnerability.

**3. Missing FTL overhead analysis:**
Section 4.1.4 claims coarse-grained access "eliminates the need to maintain page-level FTL" after deployment. But they retain FTL metadata for writes during initialization and periodic maintenance. What's the actual latency impact of the "defragmentation operations" needed during DB_Deploy()? This is handwaved as "an initial upfront overhead that can be amortized over time."

**4. NAND command set modifications:**
Table 2 introduces four new NAND flash commands (IBC, XOR, GEN_DIST, RD_TTL). The paper claims this is "without hardware modifications" but modifying the die control FSM to accept new commands IS a hardware change. They're just arguing it's a small one.

**5. Limited workload diversity:**
The evaluation uses primarily Wikipedia-based datasets (wiki_en, wiki_full, HotpotQA, NQ). These are all text retrieval. Section 2.1 mentions multi-modal RAG but never evaluates it. Image/audio embeddings have different dimensionality and access patterns.

**6. No wear leveling analysis:**
Section 7.2 claims ESP achieves 0 BER at "10k Program/Erase cycles" but doesn't analyze what happens to the SLC partition over time under RAG workloads. If embeddings are updated, wear leveling becomes critical.

---

## Q4: What the Authors Didn't Tell You

**1. The "no hardware modifications" claim is misleading:**
REIS requires:
- New NAND flash commands (Table 2)
- Modified die control FSM to handle these commands
- Multi-Plane IBC (MPIBC) requiring "raising the select signal for all planes together" (Section 4.3.4) — this needs a multiplexer modification
- Soft partitioning for hybrid SLC/TLC (Section 4.1.2)

These are firmware AND hardware changes. The claim should be "no *additional computational logic* in flash dies" — they're repurposing existing logic, but the control path is definitely modified.

**2. The storage overhead is significant:**
Binary quantization with ESP-SLC gives error-free operation but at a cost. SLC has 3× lower density than TLC. Section 4.1.2 says binary embeddings go in SLC, INT8 embeddings and documents in TLC. For wiki_en, the binary embeddings alone would consume significant SLC capacity. They never quantify this overhead.

**3. What happens when the database doesn't fit?**
All evaluated datasets fit within the SSD configurations. Wiki_full has 180M entries, but at 1024-dimension embeddings with BQ, that's ~23GB of binary embeddings. What about trillion-entry databases mentioned in Section 2.2 [8, 114]? The paper never discusses sharding across multiple SSDs.

**4. The baseline is generous to REIS:**
CPU-Real uses FAISS with flat indexes (Section 3.1), not optimized hierarchical indexes. A properly tuned CPU baseline with HNSW and mmap would show different results. The paper argues HNSW indexes "take up significantly more space than IVF ones" (Section 5) so IVF wins when loading time is included — but this is a configuration choice, not a fundamental limitation.

**5. Interference with normal SSD operations:**
Section 7.2 says REIS "operates exclusively in either RAG-mode or normal SSD mode." This means during RAG queries, the SSD can't serve normal I/O requests. For a production system serving both RAG and other workloads, this is a major limitation. They claim "write operations are expected to be infrequent" but don't quantify acceptable QoS degradation.

**6. The distance filtering threshold is workload-specific:**
Section 4.3.3 says filtering "99% of documents" for HotpotQA still retrieves the k=10 most relevant. But they admit "the threshold would only be 1.6% higher for FEVER vs Quora" — this is for the same domain (fact-checking, QA). What about out-of-distribution queries? The threshold is hardcoded based on offline profiling.

**7. No artifact availability mentioned:**
The paper doesn't link to a GitHub repository or provide any reproducibility artifacts. Given the complexity of the simulation infrastructure (Flash-Cosmos + Zsim + Ramulator + CACTI7 + custom power models), this is a significant reproducibility concern.