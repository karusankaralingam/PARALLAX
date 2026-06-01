## Q1: Whiteboard Explanation

Let me walk you through what PIM-malloc actually does at the hardware level.

**The Problem:** UPMEM-PIM has 2,560 independent DPUs (DRAM Processing Units), each a wimpy 350 MHz in-order core with only 64 KB of scratchpad (WRAM) and access to its own 64 MB DRAM bank. Currently, `buddy_alloc()` only works on the tiny scratchpad. If you want to dynamically allocate from the DRAM bank, you're out of luck.

**The Straw-Man Approach:** Naively extend the buddy allocator to manage a 32 MB heap in DRAM. This creates a 20-level tree (log₂(32MB/32B) = 20), requiring 512 KB of metadata *per DPU*. The metadata doesn't fit in the 64 KB scratchpad, so you need software-managed caching—fetching metadata blocks from DRAM to scratchpad on every tree traversal. This is brutally slow (see Figure 7: 12× slowdown from small to large heaps).

**PIM-malloc-SW's Trick (Figure 9):** A two-tier hierarchical allocator:

1. **Frontend (Thread Cache):** Each of the 24 hardware threads gets a private pool of pre-subdivided 4 KB blocks. Eight linked lists handle size classes from 16B to 2KB. Allocation is O(1)—just flip a bit in a bitmap and return the sub-block address. *No mutex required* because it's per-thread private.

2. **Backend (Buddy Allocator):** Only handles requests >2KB or refills empty thread caches. Tree depth drops from 20 to 13 levels (log₂(32MB/4KB) = 13) because the minimum allocation unit is now 4KB, not 32B.

**PIM-malloc-HW/SW's Addition (Figure 12):** A 16-entry fully-associative CAM-based "buddy cache" per DPU. Each entry: 1-bit valid, 4-byte DRAM address (tag), 4-byte metadata value. Total: 64 bytes of SRAM per DPU. This replaces the coarse-grained software metadata buffer with fine-grained LRU caching. Four new ISA instructions: `init_bc`, `lookup_bc`, `read_bc`, `write_bc`.

**The Key Delta:** Instead of flushing/refilling entire metadata blocks on every miss (PIM-malloc-SW's approach in Figure 13(a)), the buddy cache evicts only the single LRU entry and fetches only the requested 4-byte metadata (Figure 13(b)). This exploits the temporal locality in tree traversals—parent nodes in upper levels get revisited frequently.

---

## Q2: The Key Insight

The paper identifies that **"PIM-Metadata/PIM-Executed" is the only scalable design point** (Section III-B, Figure 6). The insight comes from their 2×2 design space exploration:

| Design | Problem |
|--------|---------|
| Host-Metadata/Host-Executed | CPU parallelism bottleneck (only tens of cores) |
| Host-Metadata/PIM-Executed | Massive metadata transfer (HOST→PIM) per allocation |
| PIM-Metadata/Host-Executed | Massive metadata transfer (PIM→HOST) per allocation |
| **PIM-Metadata/PIM-Executed** | **Local, parallel, no data movement** |

The fundamental insight is: *keep metadata where the executor lives*. With 2,560 DPUs, transferring metadata to/from the host becomes the dominant cost. The "PIM-Metadata/PIM-Executed" approach lets each DPU manage its own heap independently and in parallel—the allocation latency doesn't increase as DPU count grows (red line in Figure 6(a) stays flat).

The *second* insight, which enables PIM-malloc-SW's 66× speedup, is recognizing that **most allocations are small** (93% satisfied by frontend per Figure 11(a)), but the **buddy allocator dominates latency** (68% of time per Figure 11(b)). This is the opposite of TCMalloc's profile, which spends 53% of time in its frontend. Therefore, unlike Mallacc [68] which accelerates the frontend, PIM-malloc-HW/SW accelerates the *backend* buddy allocator with the buddy cache.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real Hardware Validation (Sections III, IV):** Characterization in Section III and validation of PIM-malloc-SW used actual UPMEM-PIM hardware (512 DPUs). This grounds the design space exploration in reality—latency breakdowns in Figures 6(b) and 8(b) reflect actual DPU behavior, not simulator artifacts.

2. **Comprehensive Design Space Exploration (Table I, Figure 6):** The systematic 2×2 exploration with measured results (Figure 6(a)) convincingly justifies the "PIM-Metadata/PIM-Executed" choice. The latency breakdown (Figure 6(b)) clearly shows data transfer dominates other approaches.

3. **Appropriate Baselines for Case Studies:** For dynamic graph updates, they compare against static CSR (the status quo for PIM graph analytics) and show PIM-malloc enables dynamic data structures that outperform static allocation (Figure 17(a): 7.1× and 32× speedup). For LLM attention, they compare static KV cache allocation against dynamic, showing 1.7× throughput improvement (Figure 18).

4. **Sensitivity Analysis (Figure 16):** They justify the 64B buddy cache size by showing hit rate saturates beyond this point (~99% hit rate). The calculation (256 metadata elements fit in 64B) provides hardware intuition.

### Weaknesses

1. **Simulation-Based Comparative Evaluation (Section V):** The head-to-head comparison of straw-man vs. PIM-malloc-SW vs. PIM-malloc-HW/SW uses uPIMulator, not real hardware. While they validate the simulator matches their real-system measurements, the 66× and 31% improvement numbers (Figure 15) come from simulation. The authors acknowledge this indirectly by noting uPIMulator is "cycle-level."

2. **Limited Workload Diversity:** Only two application case studies (dynamic graph update, LLM attention). Both have favorable allocation patterns—graph update uses constant 256B allocations (array of linked lists) or power-of-2 arrays, and LLM uses fixed 512B KV cache blocks. Adversarial allocation patterns (e.g., random sizes, heavy fragmentation) are not evaluated.

3. **Fragmentation Analysis is Superficial (Section VI-D, Table III):** They acknowledge pre-allocation causes fragmentation (1.95× for array of linked lists) and propose "PIM-malloc-lazy" as a fix, but don't evaluate its performance impact. The trade-off between allocation latency and fragmentation remains uncharacterized.

4. **Hardware Cost Underspecified:** The buddy cache area (0.019 mm²) and power (5 mW) from CACTI are scaled 10× for DRAM process, but this yields ~0.19 mm² per DPU. Across 2,560 DPUs, that's ~487 mm² of additional silicon. They don't discuss whether this is feasible within DRAM die constraints or how it compares to existing DPU area.

---

## Q4: What the Authors Didn't Tell You

**1. The "64B Buddy Cache" is Hiding Significant CAM Complexity:**

Section IV-B claims a 16-entry fully-associative cache with 1-cycle access latency. But fully-associative CAMs scale poorly—16 entries with 4-byte tags means 16 parallel comparators per lookup. They use CACTI 7.0 with a *32nm logic process*, then "scale accordingly" for DRAM process (Section VI-F). The 3× timing penalty they apply may not capture the reality that CAMs are notoriously difficult to implement in DRAM technology, which lacks the dense logic transistors needed for parallel comparison logic. A 16-entry CAM may actually require multiple cycles or significantly more area than reported.

**2. The Scratchpad Pressure is Glossed Over:**

The 64 KB WRAM must now hold: (1) the application's working set, (2) PIM-malloc-SW's thread cache metadata (8 bitmaps per thread × 24 threads), (3) the software-managed metadata buffer for the buddy allocator. They never quantify the WRAM footprint of PIM-malloc-SW. Section VI-E mentions "maximum overhead is 5.2 KB" but this appears to be *heap* metadata, not WRAM usage. For memory-intensive PIM workloads, this WRAM consumption could be problematic.

**3. The Thread Cache Pre-Population Creates Cold-Start Latency:**

Section IV-A states `initAllocator()` pre-populates all 24 thread caches with 8 linked lists each, requiring 24 × 8 = 192 buddy allocator calls (one 4KB block per list). This initialization cost is never measured. For short-running PIM kernels or frequently re-launched workloads, this could be significant.

**4. Lock Contention at Backend Remains:**

PIM-malloc-SW's thread cache is lock-free, but the buddy allocator still uses a shared mutex (Section IV-A: "PIM-malloc-SW's buddy allocator still uses a shared mutex"). Figure 10(b) shows thread cache misses fall through to the buddy allocator. Under workloads with frequent large allocations or thread cache exhaustion, the backend becomes a serialization bottleneck. Figure 8's busy-waiting problem is *reduced*, not eliminated.

**5. The LLM Case Study Uses Contrived Setup:**

The LLM evaluation (Section VI-C) uses a trace with 128 input tokens and 256 output tokens—extremely short sequences. Real LLM serving involves context lengths in the thousands to tens of thousands. The KV cache allocation pressure and fragmentation characteristics would differ dramatically at scale. Additionally, they evaluate with only 512 DPUs, but claim "2,560 PIM cores" throughout the paper—the LLM evaluation doesn't test the full system scale.