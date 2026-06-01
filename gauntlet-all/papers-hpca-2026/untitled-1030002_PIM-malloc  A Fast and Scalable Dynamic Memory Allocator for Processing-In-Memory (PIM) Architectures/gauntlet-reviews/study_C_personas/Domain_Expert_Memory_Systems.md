# Paper Deconstruction: PIM-malloc

## Q1: Whiteboard Explanation

Imagine you run a massive warehouse with 2,560 separate storage rooms, each with its own tiny librarian (a wimpy PIM core running at 350 MHz). Each librarian can only access books in their own room—they can't walk to another room. Now, your workers (threads) constantly need to check out and return books (allocate and free memory) from these rooms.

**The Problem:** Current PIM systems like UPMEM don't let these librarians manage their book checkout system properly. The existing solution (`buddy_alloc()`) only works for a tiny 64KB closet (the scratchpad), not the main 64MB library (DRAM bank). If you naively extend it to the full library, every checkout requires the librarian to climb a 20-level ladder (tree traversal) to find a free book slot, and when multiple workers show up at once, they all fight over the single checkout counter (mutex contention).

**PIM-malloc's Solution (Figure 9):**

1. **Two-level hierarchy:** Instead of one slow system, they create a fast "express lane" (thread cache) for small checkouts (16B-2KB) and a slower "main desk" (buddy allocator) for big requests (>2KB). The express lane gives each worker their own private mini-counter—no waiting in line.

2. **How the express lane works:** Each thread gets 8 linked lists, one for each size class (16B, 32B, 64B... up to 2KB). These hold pre-split 4KB blocks from the buddy allocator. Need 128 bytes? The librarian just grabs a pre-cut piece from the 128B pile—O(1) time, no tree climbing.

3. **Shrinking the ladder:** By only using the buddy allocator for 4KB+ requests, the tree depth drops from 20 levels to 13 levels (log₂(32MB/4KB) = 13).

4. **The hardware upgrade (PIM-malloc-HW/SW):** For the remaining buddy allocator accesses, they add a tiny 64-byte "sticky note pad" (buddy cache) that remembers recently-accessed tree metadata. Instead of fetching metadata from slow DRAM every traversal, the librarian checks their sticky notes first. This cache uses proper LRU replacement instead of the crude "flush everything on miss" approach that software alone requires.

## Q2: The Key Insight

**The Real Innovation:** The paper's core insight is recognizing that the "PIM-Metadata/PIM-Executed" design point (Figure 6, Section III-B) is the only scalable approach for PIM memory allocation, and then building a hierarchical allocator specifically tailored to PIM's constraints.

The key observation (Figure 6(a)) is elegant: when you scale from 1 to 512 PIM cores, three of the four design strategies see allocation latency explode (up to 12 seconds!), while "PIM-Metadata/PIM-Executed" stays flat at sub-second latency. Why? Every other approach requires either:
- CPU bottleneck: Brawny but few CPU cores can't parallelize across thousands of DPUs
- Data transfer tax: Moving metadata between host DRAM and PIM DRAM scales linearly with DPU count

The second insight is that *you cannot simply port sophisticated CPU allocators like TCMalloc to PIM*. The constraints are brutal: 24KB instruction memory (Section IV-A), no dynamic thread launch, and a scratchpad-centric programming model where metadata access requires explicit DMA transfers. This forced them to design a minimal two-layer hierarchy (~1,000 lines of code) rather than TCMalloc's four layers (~60K lines).

**The PIM-malloc-HW/SW insight is particularly clever:** Unlike Mallacc [68] which accelerates the *frontend* of TCMalloc (because that's where CPU allocators spend most time), Figure 11(b) shows PIM-malloc spends 68% of allocation time in the *backend* buddy allocator. This happens because PIM lacks the prefetching and deep hierarchies that let TCMalloc's frontend dominate. So the buddy cache targets the backend—the opposite of conventional wisdom.

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real hardware validation (Section V):** The design space exploration (Section III) and PIM-malloc-SW characterization (Section IV) run on actual UPMEM-PIM hardware with 512 DPUs. This isn't simulated fantasy—they demonstrate `buddy_alloc()` actually can't handle DRAM-scale heaps today.

2. **Honest microbenchmark design:** Figure 15 shows both single-threaded (no contention) and 16-threaded (realistic contention) results. The straw-man allocator's 13K μs latency under contention (Figure 15(b)) versus 630 μs single-threaded (Figure 15(a)) exposes the mutex bottleneck directly.

3. **End-to-end workload evaluation:** The LLM attention case study (Section VI-C, Figure 18) goes beyond microbenchmarks to show real-world impact: 1.7× throughput improvement and evaluation using LLMServingSim with realistic request patterns (100 requests, 10 req/sec).

4. **Hardware overhead is reasonable:** Section VI-F reports 0.019 mm², 5 mW, and sub-cycle latency for the buddy cache, scaled appropriately for DRAM process technology.

### Weaknesses

1. **Simulator fidelity concerns:** The main comparative evaluation (Section VI) uses uPIMulator [64], not real hardware. While they validate design decisions on real UPMEM-PIM, the 66× and 31% improvement numbers come from simulation. The paper notes Figure 8(b) required simulation "due to the lack of profiling tools"—this hints at broader measurement limitations.

2. **Limited workload diversity:** Only three workloads: a microbenchmark, dynamic graph updates (one dataset: loc-gowalla), and LLM attention. Graph analytics is admittedly PIM's sweet spot, but what about pointer-chasing workloads, hash tables, or tree-based data structures that stress allocators differently? The allocation patterns shown (Figure 11(a)) are heavily frontend-dominated (93% thread cache hits)—what happens with pathological patterns?

3. **Fragmentation analysis is defensive:** Table III shows fragmentation ratios of 1.21-1.95× with the "lazy" variant fixing most issues. But they acknowledge this creates a "trade-off between initial allocation performance and overall memory efficiency" and punt optimization to "future work." For a memory allocator paper, this feels incomplete.

4. **The hardware baseline is a straw-man:** PIM-malloc-HW/SW compares against a software-managed metadata buffer that uses *coarse-grained flush-everything-on-miss* semantics (Section IV-B). A more sophisticated software cache with partial eviction might narrow the 31% gap. The fine-grained LRU software attempt "shows 29% performance degradation," but we don't see detailed analysis of *why* or whether other software policies could work.

5. **Missing scalability evaluation:** Figure 6(a) shows scalability *of the design space* up to 512 DPUs, but PIM-malloc-SW/HW/SW evaluation (Section VI) doesn't show how performance scales as DPU count increases. Does the 66× improvement hold at 2,560 DPUs?

## Q4: What the Authors Didn't Tell You

1. **The "66× improvement" baseline is deliberately weak:** The straw-man PIM buddy allocator (Section III-C) uses a 20-level tree with 32MB heap and 32B minimum allocation. UPMEM's actual `buddy_alloc()` uses a 10-level tree with 32KB heap—a much easier problem. The paper argues extending to DRAM-scale heaps is necessary, but the massive speedup comes partly from choosing to compare against an unoptimized extension rather than exploring intermediate designs.

2. **Thread cache pre-population creates hidden startup costs:** Section IV-A admits initialization "pre-populates the thread caches with free memory blocks (a single 4 KB block for each linked list)." For 24 threads × 8 size classes = 192 blocks × 4KB = 768KB per DPU just for thread caches. With 2,560 DPUs, that's ~2GB of memory committed at startup before any application allocation. The paper doesn't quantify initialization latency.

3. **The LLM case study has significant simplifications:** The attention layer implementation "extends the PrIM GEMV benchmark" (Section V) rather than running actual transformer attention. They use simulation traces fed to LLMServingSim, not real end-to-end inference. The "1.7× throughput improvement" (Figure 18) is for attention layers only, not full LLM inference where FC layers dominate compute.

4. **Inter-DPU allocation isn't addressed:** PIM-malloc gives each DPU independent heap management. But what if a workload needs to allocate data structures spanning multiple DPUs? The paper's programming model assumes perfect data partitioning upfront. Real dynamic workloads (like graph updates causing load imbalance) might need cross-DPU memory migration—completely out of scope.

5. **The buddy cache size sensitivity (Figure 16) raises questions:** Performance saturates at 64B (16 entries × 4B metadata). But the paper claims "256 elements" can be stored—this is metadata bits, not complete node entries. The relationship between cache size, tree depth, and access patterns deserves more analysis. Why does 99% hit rate (Section VI-A) for 4KB allocations require only 64B when the tree has 8,192 nodes at level 13?

6. **No comparison to alternative allocator designs:** Why a buddy allocator backend at all? Slab allocators, bitmap allocators, or TLSF (Two-Level Segregated Fit) might be more suitable for PIM's constraints. The paper inherits UPMEM's buddy allocator choice without exploring alternatives.