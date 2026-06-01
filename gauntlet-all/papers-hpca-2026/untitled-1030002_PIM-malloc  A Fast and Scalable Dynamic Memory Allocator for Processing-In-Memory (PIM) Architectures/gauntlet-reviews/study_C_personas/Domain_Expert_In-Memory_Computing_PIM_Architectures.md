# PIM-malloc: A Fast and Scalable Dynamic Memory Allocator for Processing-In-Memory (PIM) Architectures

## Q1: Whiteboard Explanation

Alright, let me sketch this out for you. This paper is fundamentally about a **systems software** problem, not a new PIM compute paradigm.

**The Setup:** UPMEM-PIM is a commercial, general-purpose, near-bank PIM system. Each DRAM bank has a wimpy little in-order core (a "DPU") running at 350 MHz, with its own private 64 KB scratchpad (WRAM) and 64 MB of local DRAM bank (MRAM). The key constraint: a DPU can *only* access its own local memory. You have 2,560 of these independent little worlds in a full system.

**The Problem:** UPMEM provides a basic `buddy_alloc()` for the tiny 64KB scratchpad, but *no dynamic memory allocation* for the much larger 32-64MB per-bank DRAM heap. If you want a linked list, a dynamically-growing graph, or a KV cache that expands at runtime, you're stuck doing painful manual memory management or wasteful static pre-allocation. This kills programmability.

**Why is this hard for PIM?**
1.  **Explosion of Address Spaces:** With 2,560 DPUs, you have 2,560 separate heaps to manage, each needing its own metadata. A naive buddy allocator for a 32MB heap needs ~512KB of metadata *per DPU*. That's over 1GB of metadata system-wide just for bookkeeping.
2.  **Wimpy Cores:** The DPUs are slow. A tree-traversal-heavy buddy allocator is expensive. And with 24 threads per DPU, lock contention on a single mutex for the allocator becomes a brutal bottleneck (Figure 8 shows threads spending 75%+ of their time just waiting for the lock).

**The Design Space Exploration (Table I, Figure 6):**
The authors ask: *Where should metadata live (Host CPU or PIM)?* and *Who runs the allocation algorithm (Host CPU or PIM)?*

Their key finding (Figure 6): **"PIM-Metadata/PIM-Executed"** wins. Keeping metadata local to each DPU and having each DPU run its own allocator avoids the massive host↔PIM data transfer overhead required by all other designs. The parallelism of 2,560 independent allocators beats the serial bottleneck of CPU-managed approaches.

**The Solution: PIM-malloc (Software-Only)**
A two-level hierarchical allocator, like a simplified TCMalloc, but tailored for the severe resource constraints of PIM (only ~1000 lines of code vs. TCMalloc's 60K):

*   **Frontend (Thread Cache):** Each of the 24 threads per DPU gets its own private cache of small memory blocks (16B to 2KB). This is **lock-free** for small allocations—you just grab a block from your private pool. This kills the lock contention problem.
*   **Backend (Buddy Allocator):** Handles large allocations (>2KB) and refills the thread caches. The tree depth is reduced from 20 levels (for 32B granularity) to 13 levels (for 4KB granularity), dramatically cutting traversal overhead (Figure 9).

**The Bonus: PIM-malloc (HW/SW Co-Design)**
They find that 68% of allocation *latency* still comes from the backend buddy allocator, even though the frontend handles 93% of *requests* (Figure 11). The software-managed metadata buffer for the buddy tree is inefficient—it uses coarse-grained caching and burns cycles on software-based replacement decisions.

The fix: A tiny, per-DPU hardware **Buddy Cache**. It's a 16-entry, fully-associative CAM (Content-Addressable Memory) that caches recently-accessed buddy tree metadata. It uses a hardware-based LRU policy for fine-grained, efficient caching. This costs a minuscule 0.019 mm² and <1 cycle latency, but it boosts the buddy allocator's hit rate to 99% and provides an additional 31% speedup over the software-only version.

---

## Q2: The Key Insight

The core, non-obvious insight of this paper is the **recognition and systematic exploitation of the unique architectural properties of bank-level PIM for system software design.**

Specifically:

1.  **The "PIM-Metadata/PIM-Executed" principle (Section III-B):** The paper's design space exploration (Table I, Figure 6) demonstrates that the intuition from traditional systems—where a powerful, centralized host would manage resources—is wrong for PIM. The explosion of independent address spaces (2,560 heaps) makes centralized management a data-movement bottleneck. The *distributed* nature of PIM is a feature, not a bug. Letting each wimpy DPU manage its own heap locally achieves perfect scalability because the allocators run in parallel with zero inter-DPU communication.

2.  **The hierarchical allocator addresses PIM-specific bottlenecks (Section IV-A):** The two-layer design (lock-free per-thread caches + a shared buddy allocator) is not novel in the CPU world (TCMalloc does this). The insight is *adapting* this pattern to PIM's constraints. The per-thread cache eliminates the devastating lock contention shown in Figure 8, which arises because UPMEM's fine-grained multithreading (24 threads on a single in-order core) creates high contention for a single mutex. Simultaneously, it reduces the buddy tree depth, cutting the number of expensive DRAM-to-scratchpad metadata fetches.

3.  **The Buddy Cache targets the *right* bottleneck (Section IV-B, Figure 11):** The authors perform a characterization showing that, unlike TCMalloc where the frontend dominates latency, in PIM-malloc the backend buddy allocator is the latency bottleneck (68% of time) even though the frontend handles most requests (93%). This justifies a hardware assist for the *backend*, not the frontend, which is a departure from prior work like Mallacc [68]. The tiny 64-byte CAM is surgically targeted at caching the frequently-traversed upper levels of the buddy tree.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1.  **Grounded in Real Hardware (Sections III, IV, V):** The design space exploration and many characterizations (Figures 6, 7, 8) are performed on actual UPMEM-PIM hardware. This lends significant credibility. They are not simulating an idealized PIM system; they are solving a problem on a machine you can buy. The software-only PIM-malloc-SW is deployable today.

2.  **Systematic Design Space Exploration (Section III-B, Table I):** The paper doesn't just propose a solution; it justifies *why* this solution is correct by exhaustively evaluating the alternatives. The clear conclusion that "PIM-Metadata/PIM-Executed" is optimal (Figure 6(a)) provides a valuable design principle for future PIM system software.

3.  **End-to-End Application Case Studies (Section VI-B, VI-C):** The evaluation is not limited to microbenchmarks. They demonstrate real-world impact with dynamic graph updates (7.1x to 32x speedup over static data structures, Figure 17(a)) and LLM inference (1.7x throughput improvement, Figure 18). This shows the work has practical value beyond accelerating `malloc` calls.

4.  **Honest Analysis of Limitations (Section VI-A, Figure 15, Figure 17(c)):** The paper clearly shows when the hardware cache provides benefit (backend-heavy workloads) and when performance can spike (thread cache misses, Figure 17(c)). They don't hide the fact that PIM-malloc-HW/SW is an incremental (31%) improvement over PIM-malloc-SW.

5.  **Low Hardware Overhead (Section VI-F):** The buddy cache is tiny (0.019 mm², 5 mW, <1 cycle latency). For a paper proposing a HW/SW co-design, this is critical. It's a reasonable addition, not a massive silicon investment.

### Weaknesses

1.  **Simulation Dependency for Final Performance Comparisons (Section V):** While characterization uses real hardware, the final performance comparisons in Section VI between the straw-man, PIM-malloc-SW, and PIM-malloc-HW/SW are performed using the `uPIMulator` cycle-level simulator [64]. This introduces a layer of abstraction. The fidelity of the simulator, particularly for modeling the software overhead of the buddy allocator's tree traversal and the proposed ISA extensions, is crucial but cannot be fully validated against the real hardware for the SW vs. HW/SW comparison.

2.  **Fragmentation Analysis is an Afterthought (Section VI-D):** Table III reveals a significant fragmentation problem (up to 1.95x). The pre-population of thread caches—an "optimization" described in Section IV-A—is a major cause. The proposed fix, "PIM-malloc-lazy," is mentioned briefly but not integrated into the main evaluation. The paper acknowledges this is a "trade-off" left for "future work," but a memory allocator with nearly 2x memory bloat for certain workloads is a significant limitation that deserves more attention. A good allocator must balance speed *and* space efficiency.

3.  **Limited Workload Diversity:** The evaluation uses two main workloads: dynamic graph updates and LLM attention. While representative, both are characterized by relatively predictable, append-heavy allocation patterns (adding edges, growing KV caches). The paper does not evaluate adversarial workloads with interleaved, random-sized allocations and deallocations that stress fragmentation and the free-list management of the buddy allocator (the "free" path seems under-evaluated compared to "malloc").

4.  **Buddy Cache Justification Could Be Stronger (Figure 16):** The sensitivity study shows that performance saturates at a 64-byte cache. The explanation—that this holds ~256 tree node elements—is plausible. However, a deeper analysis of *why* this is sufficient (e.g., showing the working set of metadata for a typical allocation sequence) would be more convincing than just presenting the saturation curve.

---

## Q4: What the Authors Didn't Tell You

1.  **The `pimFree()` Path is a Black Box:** The paper focuses almost entirely on `pimMalloc()`. The workflow for `pimFree()` is dismissed with "it follows a similar logic" (Section IV-A). But deallocation is often *harder* than allocation. Coalescing buddies, returning blocks from thread caches to the shared buddy allocator, and managing fragmentation over long-running programs are complex. How does `pimFree()` interact with the buddy cache? What is its latency? The 66x speedup claim is for allocation, but the system's overall behavior depends on both operations.

2.  **Thread Cache Sizing and Configuration:** The paper states the thread cache uses eight linked lists for sizes 16B to 2KB (power-of-two size classes, Section IV-A). But it doesn't discuss how much memory each thread cache consumes, how many 4KB blocks each linked list can hold, or if this is configurable. For a resource-constrained DPU with only 64KB of scratchpad, the memory overhead of 24 thread caches per DPU could be substantial and could compete with application data.

3.  **What Happens When the Heap Fills Up?** The paper assumes a 32 MB heap per DPU. There's no discussion of what happens when the heap is exhausted. Does `pimMalloc()` return NULL? Does it trigger an out-of-memory error? For a robust system, these edge cases matter.

4.  **The Pre-allocation Strategy is a Hidden Footgun (Section VI-D, Table III):** The `initAllocator()` function pre-populates *all 8 linked lists* in *all 24 thread caches* with a 4KB block each. That's `24 threads * 8 size_classes * 4 KB = 768 KB` of pre-allocated memory per DPU *before any application allocation*. This is a massive upfront cost and the root cause of the fragmentation problem. The paper buries this detail in Section IV-A ("pre-populates the thread caches with free memory blocks") and only reveals its impact in Table III. For workloads that don't use all size classes, this is pure waste.

5.  **The Comparison to TCMalloc/Mallacc is Somewhat Misleading:** The paper argues that deep hierarchies like TCMalloc are "impractical" for PIM due to IRAM size (Section IV-A). This is fair. But it then compares its HW/SW co-design to Mallacc, arguing that Mallacc accelerates the frontend while PIM-malloc accelerates the backend (Section IV-B, Figure 11). This comparison is slightly apples-to-oranges. Mallacc accelerates a *sophisticated* frontend (TCMalloc's per-CPU cache), while PIM-malloc's HW assist accelerates a *simple* backend (a basic buddy allocator) because PIM-malloc's frontend is *too simple* to absorb most of the latency. The need for HW acceleration on the backend is partly a consequence of the software design's limitations.