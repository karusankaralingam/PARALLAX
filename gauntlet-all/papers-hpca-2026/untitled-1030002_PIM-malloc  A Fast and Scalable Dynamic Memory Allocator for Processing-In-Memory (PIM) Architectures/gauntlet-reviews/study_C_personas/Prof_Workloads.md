# Evaluation Critique: PIM-malloc

## Q1: Whiteboard Explanation

Let me draw the problem and solution for you.

**The Setup:** UPMEM-PIM is a Processing-In-Memory system where you have thousands of "wimpy" PIM cores (350 MHz, in-order) sitting inside DRAM banks. Each core has its own private 64 MB DRAM bank (MRAM) plus a tiny 64 KB scratchpad (WRAM). The killer constraint: each PIM core can ONLY access its own local DRAM bank. No shared memory. No cross-DPU communication.

**The Problem:** UPMEM currently only supports `buddy_alloc()` for the 64 KB scratchpad—not the 64 MB DRAM bank. Why does this matter? Two case studies:

1. **Dynamic Graphs:** If you want to add an edge to a graph stored in CSR format, you need to shift entire arrays. With a linked list (dynamic allocation), you just allocate a node and update a pointer. But you can't do this without a DRAM allocator.

2. **LLM KV Cache:** The KV cache grows dynamically during inference. Without dynamic allocation, you must statically reserve for worst-case, wasting memory and limiting batch size.

**The Design Space:** The authors systematically explore four quadrants:
- **Metadata location:** Host CPU memory (centralized) vs. PIM DRAM (distributed)
- **Execution location:** Brawny CPU cores vs. wimpy PIM cores

The key finding (Figure 6): "PIM-Metadata/PIM-Executed" is the only approach that scales. Why? Because any design requiring host↔PIM metadata transfers creates a bottleneck that grows with PIM core count.

**PIM-malloc Solution (Figure 9):**
- **Frontend (Thread Cache):** Per-thread private memory pools with 8 linked lists for size classes (16B to 2KB). Lock-free, O(1) allocation. Eliminates mutex contention for small allocations.
- **Backend (Buddy Allocator):** Handles 4KB+ allocations using a reduced-depth buddy tree (13 levels instead of 20).
- **Hardware Enhancement (Buddy Cache):** A tiny 64-byte fully-associative CAM cache that stores recently-accessed buddy tree metadata with LRU replacement, eliminating the software metadata buffer's coarse-grained flushing behavior.

---

## Q2: The Key Insight

The paper's core insight is architectural, not algorithmic: **the "explosion" of independent address spaces in bank-level PIM architectures fundamentally changes the cost structure of memory allocation, making distributed, PIM-local metadata management the only scalable approach.**

This is captured in Section III-B and Figure 6: with 2,560 PIM cores, you have 2,560 *independent* heaps requiring 2,560 sets of metadata. Any centralized approach (host-managed metadata or host-executed allocation) creates serialization points or data movement costs that scale linearly with PIM core count.

The secondary insight is equally important: **PIM's hierarchical memory (scratchpad + DRAM) creates a locality problem for tree-based allocators that cannot be efficiently solved in software**. The buddy allocator's random-walk tree traversal defeats coarse-grained software caching (Figure 13a), but a tiny hardware cache with fine-grained LRU management (64 bytes!) can capture the temporal locality of frequently-revisited parent nodes during tree traversal.

What makes this non-obvious is the paper's Figure 11 revelation: even though 93% of allocations hit the frontend thread cache, **68% of total allocation latency** comes from the 7% handled by the backend buddy allocator. This inverts the optimization priority assumed by prior work like Mallacc (which targets frontend caches).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Real Hardware Characterization (Sections III and IV)**
The design space exploration in Figure 6 is conducted on a real 512-core UPMEM-PIM system, not simulation. This gives the scalability findings (PIM-Metadata/PIM-Executed winning) actual credibility. The authors also open-source their implementation, which is increasingly rare.

**2. Microbenchmark Rigor (Figure 15)**
The allocation latency microbenchmark properly isolates the allocator's performance by varying thread count (1 vs. 16), allocation size (32B, 256B, 4KB), and showing both single-threaded (no contention) and multi-threaded (with contention) scenarios. The 66× speedup claim for PIM-malloc-SW is backed by absolute latency numbers (6.9 µs vs. 630 µs for 32B single-threaded).

**3. Workload Diversity**
The two case studies—dynamic graph updates and LLM attention—represent genuinely different allocation patterns: the former uses repeated small allocations (256B fixed), while the latter involves growing KV caches with 512B blocks. Figure 11 confirms the workloads stress different parts of the allocator hierarchy.

**4. Sensitivity Analysis (Figure 16)**
The buddy cache size sensitivity study shows performance saturating at 64B, with the authors providing a mathematical justification (256 metadata elements at 2 bits each). This is exactly the analysis needed to justify a design parameter.

### Weaknesses

**1. The "66×" Speedup is Against a Strawman They Constructed**

The baseline "straw-man PIM buddy allocator" is the authors' own naive extension of UPMEM's scratchpad allocator to DRAM. This is not a prior work comparison. There is no comparison against any other dynamic memory allocator adapted for PIM—not even a simple free-list or slab allocator. The 66× number is essentially comparing their optimized design against their unoptimized design.

Section III-C acknowledges the straw-man has "limitations" but the framing throughout claims "66× improvement in memory allocation performance" (Abstract) without qualification.

**2. Benchmark Cherry-Picking: Only Two Workloads**

The evaluation uses exactly two workloads: dynamic graph update (one dataset: loc-gowalla) and LLM attention (one model: Llama-2 7B). Section V admits "Due to the limited availability of open-source datasets that model dynamic graph update behaviors in real-world applications, these prior studies perform experiments using synthetic datasets."

Critical missing workloads:
- Workloads with high deallocation frequency (the paper heavily emphasizes `pimMalloc()` but barely discusses `pimFree()` behavior)
- Workloads with variable allocation size distributions (not just power-of-two)
- Long-running workloads that would expose fragmentation over time

**3. Fragmentation Analysis is Weak (Table III)**

Table III shows fragmentation ratios of 1.66–1.95× for the default PIM-malloc and admits the "pre-allocation optimization" causes this. The "PIM-malloc-lazy" numbers (1.0–1.49×) are presented without any performance comparison. The paper states "We leave the optimization of this balance... as future work"—but fragmentation is a core allocator metric, not an afterthought.

**4. Simulation vs. Real Hardware Inconsistency**

Section V states: "We use the open-source UPMEM-PIM cycle-level simulator, uPIMulator [64], for all our evaluation in Section VI." This means the headline results (Figures 15, 17, 18) are simulated, despite the paper heavily emphasizing access to real hardware in Sections III-IV. The simulator was validated by the original authors [64], but the paper never discusses how accurately it models the specific behaviors PIM-malloc depends on (MRAM-to-WRAM transfers, mutex contention timing).

**5. Hardware Overhead Understated**

The buddy cache evaluation (Section VI-F) uses CACTI with 32nm logic process, then applies scaling factors for DRAM process. But the paper claims buddy cache access latency of "less than one PIM core logic cycle" without explaining how a fully-associative CAM lookup with LRU update completes in <3ns (at 350 MHz). The 0.019 mm² area and 5 mW power are presented without context—what fraction of the DPU is this?

**6. LLM Evaluation Uses a System Simulator, Not End-to-End Measurement**

Figure 18's throughput/TPOT results come from "LLMServingSim [24]" fed with traces from uPIMulator. This is simulation of simulation. The paper doesn't discuss how scheduling decisions in the serving layer interact with PIM-malloc's allocation patterns.

---

## Q4: What the Authors Didn't Tell You

**1. The Thread Cache Pre-allocation Strategy is a Loaded Gun**

Section IV-A describes pre-populating "a single 4 KB block for each linked list within the thread cache" during `initAllocator()`. With 24 threads × 8 size classes × 4 KB = 768 KB per PIM core, this consumes ~2.3% of each core's 32 MB heap before any user allocation. With 2,560 PIM cores, that's nearly 2 GB of pre-allocated memory system-wide—memory that may never be used if the workload only needs specific size classes.

**2. The 24-Thread Configuration is UPMEM-Specific and May Not Generalize**

The entire thread cache design assumes 24 threads per PIM core (UPMEM's hardware limit). Future PIM architectures with different thread counts would require redesigning the frontend, yet the paper presents PIM-malloc as a general "PIM memory allocator" (title, abstract).

**3. The "Dynamic Graph Update" Workload is Artificial**

Section V admits using synthetic sampling: "nodes or edges of a static graph dataset are randomly sampled; the sampled portion represents the newly added graph dataset." Real streaming graph workloads have non-uniform update patterns (power-law edge arrivals, temporal locality in modifications). The uniform random sampling likely favors PIM-malloc's size-class design.

**4. No Discussion of Allocation Failure Handling**

What happens when the 32 MB heap is exhausted? The paper never mentions out-of-memory conditions, allocation failure returns, or heap expansion. For the LLM workload, which dynamically grows KV caches, this is a production-critical concern.

**5. The "Variable-Sized Array" Graph Structure Has Terrible Locality**

Figure 17(a) shows the variable-sized array implementation with PIM-malloc achieving only 3.3 million edges/sec vs. 351 million edges/sec for the array-of-linked-lists version. That's a 100× difference between two PIM-malloc-enabled implementations. The paper doesn't adequately explain this—likely due to the variable-sized array requiring frequent reallocations that hit the buddy allocator.

**6. The Buddy Cache Hit Rate Numbers Don't Match the Text**

Section VI-A claims "PIM-malloc-HW/SW achieved a 99% hit rate" for 4KB allocations, but Figure 16 shows hit rates around 75-80% for the 64B cache configuration. The 99% figure may come from a different experimental setup that isn't clearly specified.

**7. PIM-malloc-SW's Software Buffer is Worse Than No Buffer for Some Configurations**

Section IV-B admits: "fine-grained buffer management shows a 29% performance degradation in the microbenchmark... where 16 threads request 4 KB allocations." This means PIM-malloc-SW's metadata buffer strategy is actively harmful for large-allocation workloads—the very workloads that bypass the thread cache and must use the buddy allocator.