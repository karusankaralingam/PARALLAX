Q1: Whiteboard Explanation

Let me draw out how RAGX actually works at the hardware level.

**The Problem They're Solving:**
RAG (Retrieval-Augmented Generation) has three phases: (1) Search & Retrieval, (2) Query Augmentation, (3) LLM Generation. The paper's AWS measurements (Figure 5b) show Search & Retrieval consumes 61% of runtime with local NVMe, and 88% with networked EBS storage. Within Search & Retrieval itself (Figure 5c), 74% of time is spent on storage access latency, even with local NVMe.

**The Architecture (Figure 6):**
RAGX places a "RAGX Unit" inside the SSD, alongside the existing flash controller. This unit contains:
1. **Metamorphic Accelerator** - A 32×32 array of "XEs" (metamorphic execution engines) plus 32 "SEs" (scalar engines)
2. **Metadata Navigation Unit (MNU)** - Handles dynamic data fetching and kernel configuration

**The "Magic Trick" - Shape-Shifting (Figure 7):**
The metamorphic accelerator has two modes controlled by multiplexers:

*Systolic Mode:* The 32×32 XE array operates as a conventional systolic array for matrix multiplication. Data flows horizontally (inputs) and vertically (partial sums). This runs the embedding models (ColBERT, GTR) that convert queries into vectors.

*Vector Mode:* The same hardware reconfigures so each *column* becomes an independent vector processor. The 32 XEs in a column form a pipeline, with the front-end at top feeding instructions. The SEs at the bottom handle division/sqrt/log operations. This executes similarity scoring (cosine similarity, L2-norm, BM25).

**The MNU (Figure 8):**
The MNU solves the "data-dependent shape" problem. Since HNSW graph traversal or posting list sizes aren't known until runtime, the MNU:
1. Reads metadata from DRAM to determine what embeddings/posting lists to fetch
2. Uses "parametric kernel templates" stored in a template cache
3. Fills in runtime parameters (embedding dimension, neighbor count) via "parameter insertion" logic
4. Generates NVMe commands directly to the NAND arrays, bypassing PCIe

**Data Path:**
Query arrives → MNU reads HNSW graph metadata from DRAM → determines which embeddings to fetch → DMA engine pulls embeddings directly from NAND arrays into XE scratchpads → Metamorphic accelerator computes similarity scores → Results update priority queue in DRAM → repeat until top-k found.

---

Q2: The Key Insight

**The Core Insight:** The paper observes that RAG workloads decompose into two *disjoint computational phases* that can time-multiplex the same hardware:

1. **GeMM-heavy phase** (query embedding via transformer models) → needs 2D systolic dataflow
2. **Vector-heavy phase** (distance/similarity computation) → needs parallel SIMD lanes with special functions

The "aha moment" (Section 4.1, page 456): *"These computations are performed in disjoint phases, providing an opportunity to reconfigure the same architecture for different forms of execution."*

**What Makes This Non-Obvious:**
Prior in-storage accelerators for approximate nearest neighbor (citations [37, 89] in Section 6) were fixed-function—they did graph traversal or bitonic sorting, but couldn't run the embedding model. The authors realized that if you're already inside the SSD, you *must* also run the embedding model there, otherwise you'd need to send the query embedding over the network (Section 2.4 shows this "Disag-DRAM" setup loses 28% throughput from network overhead alone).

**The Structural "Trick":**
Looking at Figure 7(c), each XE contains a MAC unit (for systolic mode) plus additional multiplexers controlled by `mode_sel` and `in_sel`/`out_sel` bits. In systolic mode, data flows from left XE → through MAC → to bottom XE. In vector mode, data flows from top XE → through the same MAC (repurposed) → to bottom XE. The local weight buffer becomes a scratchpad. The key is that the MAC unit is reused—they're not duplicating compute resources, just rerouting data paths.

The SEs add the "missing" operations (div, sqrt) that distance metrics need but systolic arrays don't provide. In systolic mode, SEs form a horizontal vector unit for non-GeMM parts of neural networks; in vector mode, each SE pairs with its column as a pipeline tail.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Real AWS Measurements for Baselines (Section 5.1):** The paper runs 3,800 BioASQ queries through actual AWS deployments (Table 2), measuring real network latencies (86ms average between EC2 instances in the same zone, Section 2.4). This grounds the simulation results in realistic datacenter conditions.

2. **Comprehensive Benchmark Coverage (Table 1):** They evaluate five different retrievers spanning both embedding-based (ColBERT, GTR, Doc2Vec with HNSW) and keyword-based (BM25, SPLADEv2 with inverted indices), using different embedding dimensions (128, 300, 768, variable) and distance functions (L1, L2, dot product, BM25).

3. **Scaling Analysis (Figure 9):** Results across 0.5M, 5M, 50M, and 500M passages show benefits *increase* with database size (1.6× → 5.7× geomean improvement), which is the right trend for a storage-centric optimization.

4. **Cost Analysis with Real Pricing (Figure 11):** Using actual AWS instance pricing (Table 2), they show RAGX achieves lowest $/query while CPU-DRAM is 119-391% more expensive—this addresses the practical deployment concern.

5. **Recall/Accuracy Maintained (Figure 13):** Their data placement strategy (private HNSW per drive) either maintains or slightly improves recall@100, with 2-3% accuracy improvement for embedding-based retrievers at 8 drives.

**Weaknesses:**

1. **Simulated Accelerator, Not Real Hardware:** The metamorphic accelerator is RTL-synthesized (45nm, 1GHz) but evaluated via cycle-level simulation (Section 5.1). The paper acknowledges "Real deployment of a research chip in the cloud is not currently feasible." RAGX cost is estimated using AWS ra3.xlplus pricing as a "proxy" (Section 5.2.1)—this is a weak assumption since ra3.xlplus is a Redshift node, not a computational storage device.

2. **15W Power Budget Claim Lacks Validation:** They cite the 15W SSD constraint (Section 3, citations [23, 42, 55, 56]) and claim their design fits, but Table 2 only lists "13W TDP" without showing the breakdown or how this was validated for sustained workloads. The power synthesis results aren't detailed.

3. **NAND Latency Model Simplification:** Storage access latencies are "modeled using an open-source simulator [58]" (Section 5.1), but the paper doesn't discuss NAND endurance implications of the repeated reads during HNSW traversal, nor garbage collection interference.

4. **Limited LLM Generation Sensitivity:** The paper shows that with longer output tokens (512), benefits drop to 1.2× (Figure 15). This is because Referenced Generation dominates. The paper's sweet spot (54 output tokens) may not represent all RAG use cases.

5. **Multi-Device Communication Overhead Hidden:** For embedding-based multi-device (Section 3.2), they broadcast the embedded query via "peer2peer PCIe connection" but don't quantify this latency. For 8 drives, this broadcast must serialize somewhere.

---

Q4: What the Authors Didn't Tell You

**The Hidden Hardware Costs:**

1. **MNU Complexity is Substantial:** The Metadata Navigation Unit (Figure 8) contains: a template cache, parameter insertion logic, instruction buffer, dimension registers, stride calculators, address generation units, LBA translator, DMA engine, and NVMe command generator. This is essentially a small programmable processor. They provide no area breakdown—how much of the 15W budget does the MNU consume vs. the metamorphic array?

2. **DRAM Still Required:** The HNSW metadata graph *stays in DRAM* (Section 2.1: "metadata...typically stored in DRAM for fast navigation"). The RAGX storage drive has its own DRAM (Table 2 shows "4 GB (16 MB)" which I interpret as 4GB DRAM with 16MB on-chip SRAM). They're not eliminating DRAM—they're just eliminating PCIe crossings for the embeddings themselves.

3. **Query Serialization Issue:** Each query must traverse HNSW iteratively—"each iteration depends on the results of the previous, limiting opportunities for parallelization or prefetching" (Section 2.2). RAGX doesn't solve this dependency chain; it just makes each iteration faster. With batch size 1, you can't overlap queries across the accelerator.

4. **The "Multiple Private HNSWs" Trade-off:** Section 3.2 admits their data placement strategy increases "cumulative computation across all devices" compared to a single shared HNSW. They argue the parallelism outweighs this, and recall improves because smaller graphs are searched more thoroughly—but this means each drive is doing redundant work.

5. **Vector Mode Utilization Mystery:** In vector mode, the paper says each column "operates independently, maintaining its own program counter" (Section 4.2). But similarity computation for a single query's neighbors should be parallelizable across columns. They don't show how well the 32 vector engines stay occupied—what's the utilization during the graph traversal phase?

6. **The Flash Controller Sharing:** Figure 6(b) shows the RAGX Unit sharing the flash controllers with the existing SSD controller. The paper says accelerator data transfers "bypass the SSD's main DRAM buffer when appropriate" but doesn't discuss contention when normal SSD I/O is happening concurrently. In a real datacenter, these drives would also serve other workloads.

7. **500M Dataset Caveat:** For GTR-LLAMA2 at 500M passages, results are marked with "X" in Figure 9 because "500M analysis for GTR-LLAMA2 was infeasible due to memory requirements." GTR has 768-dimension embeddings, so 500M passages = 1.5TB (Section 5.1)—this exceeds what their simulation infrastructure could handle, yet they claim benefits at this scale for other retrievers.

8. **The Referenced Generation Still Dominates at Scale:** Figure 15 shows that with batch size 256, Referenced Generation takes 73% of runtime, limiting RAGX benefits to 4.4× (Figure 17). The paper positions this as "RAGX still provides gains," but the fundamental insight that Search & Retrieval is the bottleneck becomes less true as batching increases.