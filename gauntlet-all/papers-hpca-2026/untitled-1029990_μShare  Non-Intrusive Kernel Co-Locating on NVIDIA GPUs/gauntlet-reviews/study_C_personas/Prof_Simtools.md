# µShare: Non-Intrusive Kernel Co-Locating on NVIDIA GPUs

## Q1: Whiteboard Explanation

Let me walk you through what this paper is actually doing, because it's clever in a "working around NVIDIA's locked door" kind of way.

**The Core Problem:**
NVIDIA's hardware scheduler does something frustrating: when you launch a kernel, it packs all that kernel's blocks into SMs (Streaming Multiprocessors) before moving to the next kernel. The authors call this "stacked co-location." Here's why it matters:

Each SM has six types of functional units: FP32 cores, FP64 cores, INT32 cores, Tensor cores, SFU units, and load/store units. But any given kernel typically hammers only ONE of these. The paper shows (Figure 4(b)) that kernels exhibit a "1 more, 5 less" pattern—average primary resource utilization is 30.19%, while the other five resources sit at 5.07%.

**The Trick:**
NVIDIA's scheduler uses a "leftover" policy: it places a block into an SM if the remaining thread capacity exceeds the block's thread count. So if you shape a kernel's blocksize to be *slightly more than half* of the SM's thread capacity (e.g., 800 threads on an A40 with 1,536 threads per SM), you guarantee that only ONE block from that kernel can fit per SM. The remaining ~700 threads can then accommodate blocks from a *different* kernel with complementary resource demands.

**The System:**
1. **Profile** each kernel offline to capture its hardware utilization signature (6-tuple for resources) and launch timing
2. **Intercept** kernel launches using LD_PRELOAD to hijack CUDA library calls
3. **Reshape** blocksizes for latency-critical kernels to "half-plus" (e.g., 768 + 32 = 800)
4. **Time-shift** non-critical kernels to co-launch only when their resource profile complements what's currently running

The beauty is this happens entirely in userspace—no hardware mods, no kernel code changes, no cuDNN internals needed.

---

## Q2: The Key Insight

**The fundamental insight is that blocksize is an *implicit scheduling directive* to NVIDIA's closed-source hardware scheduler.**

The authors reverse-engineered (through extensive profiling, Section II-C) that the GPU's dispatch unit uses a leftover scheduling strategy. By forcing blocksize to exceed half the SM thread capacity, you're essentially telling the scheduler: "You can't fit two of my blocks in one SM." This creates *mandatory scattering* of blocks across SMs, leaving thread "slots" for blocks from other kernels.

**Why this matters:**
This transforms a parameter designed for computational efficiency (blocksize) into a *resource isolation primitive*. Prior work required either:
- Intrusive kernel fusion (Tacker [62], Rammer [29])—not possible with closed-source cuDNN/cuBLAS
- Hardware modifications (CCWS [43], Prema [11])—not possible on commercial GPUs
- Simulator-only validation—no real deployment

µShare achieves intra-SM co-location on *actual NVIDIA hardware* by exploiting the semantic gap between what the scheduler sees (thread counts) and what it *should* optimize for (functional unit utilization).

The half-plus heuristic is particularly elegant: it's the smallest modification that guarantees scattering while minimizing thread waste. On A40 (1,536 threads/SM), blocksize 800 wastes only ~47% of one block's "slot" while ensuring co-location.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Real Hardware, Not Simulation**
This is refreshing. All experiments run on actual NVIDIA A40 and A800 GPUs (Section V-A). They measure wall-clock throughput (QPS), real latencies, and actual hardware utilization via Nsight Compute. The evaluation on A800 (Section V-F) shows the method generalizes across GPU generations with different SM thread limits.

**S2: Comprehensive Profiling Methodology**
The authors don't just claim "low utilization"—they quantify it meticulously. Figure 4(b) shows per-kernel utilization for the top 20 most-executed kernels (6,063 executions total). The distinction between NVIDIA-SMI (81.16% utilization) and Nsight Compute (9.28% actual hardware utilization) in Section II-B is critical context that most papers ignore.

**S3: Realistic Workloads and Traces**
They use Azure production traces from INFless [55] (Figure 11), 10 diverse models including LLMs (Llama2-7b, GPT-2), CNNs, and Transformers (Table III). The SLO constraint (200ms, 400ms for LLMs) reflects production requirements.

**S4: Thorough Breakdown Analysis**
Figure 22 isolates the contribution of each component: removing blocksize shaping drops throughput by 30.95%, fixing blocksize at 1024 drops it by 3.36%, removing batch management increases SLO violations by 21.90%. This is proper ablation methodology.

**S5: Honest Treatment of Limitations**
Table I and II explicitly categorize modifiable vs. unmodifiable kernels (51.63% vs. 48.37%). Figure 13 shows performance degradation as unmodifiable kernel proportion increases. They don't hide that cuDNN and tiled kernels can't be reshaped.

### Weaknesses

**W1: Limited Validation of Scheduler Assumptions**
The paper claims NVIDIA uses "leftover scheduling" (Section II-C) but cites only two 2012 papers [51, 52] about memory defragmentation, not scheduling. Figure 5's demonstration of scattered vs. stacked placement is compelling, but they read block placement via inline PTX assembly (SM_ID register)—they never *directly observe* the scheduler's decision logic. This is reverse-engineering, not documentation.

**Critical question:** Does the scheduler always use leftover? What about priority scheduling, or the "greedy then leftover" heuristic mentioned in CUDA programming guides? The paper doesn't address corner cases.

**W2: Simulation Infrastructure is Zero**
This is a *real-system paper*, which is good, but it means they can't validate fundamental architectural assumptions. They claim improved "hardware utilization" but measure it via Nsight Compute *profiling*, not actual functional unit occupancy. The 15.10% average utilization in Figure 20 aggregates kernel-level profiles—it doesn't prove those kernels actually executed *simultaneously* on the same SM.

**The validation gap:** They show that half-plus blocks scatter across SMs (Figure 5), but the leap to "complementary kernels execute in parallel on same SM" requires either cycle-accurate simulation or hardware counters that NVIDIA doesn't expose.

**W3: Nsight Compute Overhead Not Addressed**
Section V-E describes measuring utilization by profiling "all concurrently running kernels" with Nsight Compute, then "individually measur[ing] the utilization of six SM hardware resources for each kernel." Nsight Compute serializes and perturbs execution—how do they ensure the *measured* co-location matches the *actual* co-location during inference serving?

**W4: Memory Bandwidth Contention Ignored**
The paper focuses exclusively on compute resource sharing (FP32, Tensor, etc.) but never models DRAM bandwidth or L2 cache contention. Section II mentions each SM has "32 load/store units" but treats LDST as equivalent to other compute units. Memory-bound kernels don't scale with thread count—they scale with bandwidth.

Figure 4(b) shows LDST utilization varies wildly (2.39% to 58.02% in Table I). What happens when two high-LDST kernels get co-located? The time-shifted launching (Section III-D) checks that "combined utilization does not exceed 100%," but LDST isn't bandwidth—it's issue slots.

**W5: A800 Results Are Weaker, Unexplained**
Section V-F shows µShare improves throughput by 16.45%-52.29% on A800, vs. 26.90%-54.09% on A40. The explanation ("1/3-plus shaping...may lead to slightly unbalanced SM thread allocation") is hand-wavy. With 2,048 threads per SM, the optimal blocksize is 704 (2048/3 + 32), meaning *two* 1/3-plus blocks fit per SM. But then you're back to partial stacked co-location! This deserved more analysis.

**W6: Scientific Computing Results are Superficial**
Section V-H shows co-location with Parboil benchmarks (Figure 23), claiming scientific computing uses FP64 while inference uses FP32/Tensor. But they only test 5 benchmarks briefly. What about mixed-precision scientific codes? What about memory-bound stencil codes? This feels like a cherry-picked positive result.

---

## Q4: What the Authors Didn't Tell You

**1. They Didn't Validate the Scheduling Model**
The entire paper rests on the assumption that NVIDIA's hardware scheduler uses leftover scheduling. But citations [51, 52] are from 2012 and discuss memory defragmentation, not block scheduling. The actual CUDA scheduling documentation is deliberately vague. On newer architectures (Ampere, Hopper), NVIDIA has added features like asynchronous memory copy engines and independent thread scheduling that may alter dispatch behavior.

**The risk:** If NVIDIA changes their scheduler in a driver update, µShare's heuristics break silently. There's no robustness analysis.

**2. Nsight Compute Cannot Measure True Co-Location**
Nsight Compute is a *profiling* tool that instruments and serializes kernel execution. When they claim to measure "hardware utilization under co-location" (Section V-E), they're actually profiling each kernel *individually* and then *aggregating* the results over time intervals. This is fundamentally different from measuring actual concurrent execution.

**What they needed:** CUPTI's activity API or raw GPU performance counters to confirm blocks from different kernels execute in the same SM cycle. They don't have this.

**3. The "Half-Plus" Magic Number is Fragile**
For A40: blocksize = 800 = 768 + 32. For A800: blocksize = 704 = 682 + 22 (rounded). But Section IV-B admits that on A800, *two* 1/3-plus blocks can fit per SM. This means:
- 2 blocks × 704 threads = 1,408 threads, leaving 640 threads
- A small block (≤640) from another kernel can still fit

So on A800, you get *partial* stacking (2 of same kernel) plus co-location. The paper doesn't analyze how this affects the resource complementarity they claim to achieve.

**4. They Ignore Register and Shared Memory Pressure**
Section III-D mentions checking "available shared memory, registers" before launching, but the profiler only records consumption (Formula 1), not *how* these resources affect occupancy. On modern GPUs, register pressure often limits occupancy more than thread count. A kernel with 128 registers per thread can't achieve >50% occupancy regardless of blocksize.

**The gap:** They assume blocksize is the binding constraint for co-location, but for compute-intensive kernels (like GEMM), register pressure dominates.

**5. Offline Profiling Cost is Buried**
Section V-J mentions profiling takes "105 to 393 seconds" per model, and "*7,160 seconds*" for Llama2-7b (nearly 2 hours). This is significant operational overhead that's mentioned once in a single sentence. What triggers re-profiling? Driver updates? Batch size changes? New model versions?

**6. They Don't Show Kernel Timing Diagrams Under Load**
Figure 5 shows block placement for *two* kernels in isolation. Figure 24 shows kernels at runtime, but it's aggregated utilization, not a timeline of individual block executions. What happens when 10 models co-exist and hundreds of kernels compete for SMs? Do you get oscillation? Starvation?

**7. The Comparison with Tacker is Unfair**
Section V-B compares µShare to Tacker [62] but disables µShare's "latency management mechanism" for "fairness." But latency management is a *core component* of µShare! A fair comparison would keep both systems' SLO mechanisms enabled and compare throughput at equivalent SLO violation rates.

**8. No Discussion of Multi-Tenant Isolation**
The paper targets public cloud deployment (Section I), but never discusses isolation guarantees. If User A's kernels co-locate with User B's kernels:
- Can User A observe User B's resource patterns?
- Can malicious blocksize choices starve other users?
- How does the batch manager handle competing SLOs?

This is a systems paper, but the security/fairness implications are unaddressed.