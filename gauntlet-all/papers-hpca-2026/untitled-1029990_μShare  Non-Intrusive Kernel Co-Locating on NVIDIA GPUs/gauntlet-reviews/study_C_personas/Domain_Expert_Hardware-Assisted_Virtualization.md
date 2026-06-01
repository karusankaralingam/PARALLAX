# µShare: Non-Intrusive Kernel Co-Locating on NVIDIA GPUs

## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Imagine an NVIDIA GPU SM (Streaming Multiprocessor) as a kitchen with six specialized cooking stations: FP32 (your general-purpose stovetop), FP64 (precision oven), INT32 (prep counter), Tensor cores (the fancy sous-vide machine), LDST (the refrigerator for loading/storing ingredients), and SFU (special function unit, like a blender).

**The Problem:**
When you launch a CUDA kernel, it gets chopped into "blocks" of threads. NVIDIA's hardware scheduler—which is a black box—has a bad habit. It's like a head chef who, when given 100 orders for steak, assigns *all* steak orders to Chef #1's kitchen before letting Chef #2 handle *anything*. This is **stacked co-location** (Figure 1a).

The result? If your steak kernel (say, a matrix multiply) only hammers the Tensor cores (88.52% utilization, Section II-B), the other five stations sit idle (5.45% average). The authors call this the **"1 more, 5 less" pattern** (Figure 4b)—one resource type is hot, five are cold.

**The Insight:**
The GPU scheduler has one simple rule it *must* obey: a block can only be placed on an SM if the SM has enough *threads* left to accommodate it. This is the "left-over scheduling" strategy (Section II-C).

So, **the trick is to game the thread count**. On an A40, each SM can hold 1,536 threads max. If your kernel's `blocksize` is 512, the scheduler can easily stack *three* identical blocks from the same kernel into one SM (3 × 512 = 1,536). Bad for diversity.

But what if you set `blocksize` to **800** (just over half of 1,536)? Now, the scheduler places one 800-thread block, and only 736 threads remain. A *second* 800-thread block from the same kernel *cannot fit*. The scheduler is *forced* to scatter that block to a *different* SM.

This is **half-plus blocksize shaping** (Figure 5c). You've created a "vacancy" of 736 threads in SM #1. A *different* kernel—say, one using INT32 heavily—whose blocks are *smaller* (e.g., 512 threads) can now slot into that vacancy. Now SM #1 is running *two different* workloads, lighting up *different* hardware stations simultaneously. This is **scattered co-location** (Figure 1b).

**The System (µShare):**
1.  **Profile** each kernel's hardware footprint (which of the 6 units it uses) and its launch timing.
2.  **Intercept** kernel launches using `LD_PRELOAD` and `dlsym`—no code modification needed.
3.  **Shape** the blocksize of "late" kernels (those at risk of violating SLO) to half-plus and launch them immediately.
4.  **Time-shift** other kernels: hold them back until a complementary kernel (different hardware appetite) is running, then release them to fill the "vacancy."

---

## Q2: The Key Insight

The **core intellectual contribution** is the realization that on NVIDIA GPUs, the `blocksize` parameter is the *only* externally-controllable knob that deterministically influences the closed-source hardware block scheduler's placement decisions.

The authors exploit the **left-over scheduling invariant**: a block is placed on an SM if and only if `remaining_threads >= blocksize`. By setting `blocksize` to `(SM_thread_capacity / 2) + ε` (the "half-plus" value, e.g., 800 for A40's 1536-thread SMs), they guarantee that **at most one block from any single kernel can reside on a given SM at any time**. This breaks the "stacked co-location" pattern without touching the hardware or kernel source code.

This is distinct from prior work:
*   **Kernel fusion (Tacker, Rammer, COMBO):** Requires intrusive source-level merging of kernels. Not possible for closed-source libraries like cuDNN/cuBLAS.
*   **Hardware modifications (CCWS, Prema):** Require redesigning the GPU, validated only in simulation. Not deployable.
*   **Temporal/spatial sharing (Orion, INFless):** Control *which* kernels run concurrently or partition SMs, but don't address the *intra-SM* stacking of identical blocks. Orion, for instance, pairs SM-intensive and memory-intensive kernels but doesn't ensure blocks from complementary kernels land in the *same* SM.

µShare is the first system to achieve **intra-SM, kernel-level, scattered co-location non-intrusively**. The mechanism is surprisingly simple—a single parameter change—but the insight that this is *sufficient* to guide the black-box scheduler is the paper's key delta.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1.  **The "Modifiable vs. Unmodifiable" Breakdown is Honest and Critical (Tables I & II, Section III-C):** The authors explicitly acknowledge that 48.37% of kernel invocations (e.g., cuDNN, cuBLAS GEMMs) have blocksizes baked into closed-source code and *cannot* be reshaped. This is a significant limitation they don't hide. Figure 13 shows the sensitivity: as unmodifiable kernels increase from 48% to 100%, throughput drops from 58.91 to 47.59 (a 19% degradation). This honesty strengthens the paper.

2.  **Head-to-Head with Intra-SM Fusion (Tacker, Figure 14):** They directly compare against Tacker, a kernel-fusion approach that *does* achieve intra-SM co-location. µShare wins (20.38% overall throughput improvement) because Tacker can only fuse *adjacent, intra-model* kernels, whereas µShare can co-locate kernels *across different inference requests/models*. This is a fair and informative comparison.

3.  **Low-Level Hardware Utilization Measurement (Figure 20, Section V-E):** They don't just report throughput; they measure *actual* utilization of all six hardware unit types over time using Nsight Compute. µShare achieves 15.1% average utilization vs. 10.9% (INFless) and 9.37% (Orion). This directly validates the core hypothesis about the "1 more, 5 less" problem.

4.  **Overhead is Minimal (Section V-J):** 60.35 ns per kernel interception is negligible compared to kernel execution times (tens to hundreds of µs). CPU overhead is 6.85% of a single core. This is credible and doesn't hand-wave system costs.

### Weaknesses

1.  **The "Half-Plus" Heuristic is Fragile and Architecture-Specific:** The magic number (800 for A40, 704 for A800's "1/3-plus") is directly tied to `SM_thread_capacity`. The A800 results (Section V-F, Figure 21) show a *smaller* improvement (16.45% vs. 26.90% over INFless) precisely because 2048-thread SMs require "1/3-plus" shaping, which allows *two* same-kernel blocks per SM (not one). The paper states this "may lead to slightly unbalanced SM thread allocation" (p. 10-11). This isn't just "slightly"—it's a fundamental weakening of the scattering guarantee. Future GPUs with different thread counts (e.g., 3072) will require entirely new heuristics.

2.  **Workload Selection is Favorable (Table III):** The benchmark models are overwhelmingly CNNs and Transformers. These have highly regular, predictable kernel sequences. The paper evaluates LLMs (Llama2-7b, GPT-2), but with fixed input/output lengths of 10 tokens (Section V-A). Real LLM inference involves dynamic, variable-length sequences where kernel launch patterns are far less predictable. The profiled `t_launch` values (Equation 1) may not generalize.

3.  **The Comparison Baselines Have Different Goals:** INFless and Orion are designed for *inter-SM* sharing and interference mitigation. Orion explicitly limits co-location to "at most one compute-intensive and one memory-intensive kernel" (Section V-B) for QoS reasons. Beating them on throughput when they're being deliberately conservative is somewhat expected. A fairer comparison would be against a "naïve" MPS+concurrent streams baseline that also attempts maximum throughput without any co-location awareness.

4.  **The SLO Violation Trade-off is Under-Explored:** µShare's default configuration has a 3.35% SLO violation rate vs. 2.05% (INFless) and 1.12% (Orion) (Figure 16). This is a 63-199% *increase* in violations. Section V-C shows tuning `k` and `λ` can reduce this to 0.84% (µShare v7), but at the cost of reducing the throughput advantage to 19.28-44.83%. The paper doesn't clearly state which configuration is the "default" recommendation or under what conditions the more aggressive vs. conservative settings should be used.

---

## Q4: What the Authors Didn't Tell You

1.  **Register and Shared Memory Pressure Are Hand-Waved:** The profiler records `r_mem` and `r_reg` (Equation 1), and Section III-D mentions checking "available shared memory, registers" before time-shifted launch. But the paper *never* evaluates what happens when half-plus shaping *increases* register pressure per block. Larger blocks can hit the SM's 65,536 register limit, causing register spilling to local memory—a performance cliff the paper doesn't characterize. The evaluation implicitly benefits from workloads that don't stress this limit.

2.  **What Happens When Shaping Changes Kernel Correctness/Performance?** Section III-C admits some kernels (e.g., `Conv2d` which uses tiling) "trigger a CUDA internal error when modified." The paper classifies these as "unmodifiable." But there's a grey zone: kernels that *run* with a modified blocksize but produce *suboptimal* memory access patterns (e.g., breaking coalescing). The paper doesn't profile whether shaping degrades the *per-kernel* efficiency of modifiable kernels; it only measures aggregate throughput.

3.  **The "Profiled Launch Time" Assumption is Brittle:** The kernel launch slack `s^k = t^k_launch - t^k_intercept` (Equation 2) relies on `t^k_launch` being stable from the offline profiling phase. But in a real multi-tenant, multi-model system, launch times shift based on load, batching, and interference. The paper doesn't discuss how often re-profiling is needed or what happens when the profiled timing deviates significantly from runtime reality.

4.  **Memory Bandwidth Contention is Ignored:** The six intra-SM hardware units are only part of the story. If two co-located kernels both have high LDST utilization (even if they use *different* compute units), they'll contend for shared L2 cache and memory bandwidth. Figure 20 shows LDST utilization, but the paper doesn't isolate memory bandwidth as a potential bottleneck. The related work (Section VI) mentions SGDRC for memory channel isolation, but µShare doesn't integrate or evaluate against such techniques.

5.  **The "Time-Shifted Launch" Delay (`β`) is a Magic Number:** Section III-D states kernels wait "β microseconds" before rechecking launch conditions, with β=10µs chosen empirically (Section V-A). This is a polling interval that trades latency for scheduling quality. A shorter β increases CPU overhead; a longer β increases queuing delay. The sensitivity analysis for β is absent.