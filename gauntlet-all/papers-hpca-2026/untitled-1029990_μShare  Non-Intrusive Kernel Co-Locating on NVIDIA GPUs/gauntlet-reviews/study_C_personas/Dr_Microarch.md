# µShare: Non-Intrusive Kernel Co-Locating on NVIDIA GPUs

## Q1: Whiteboard Explanation

Let me walk you through what's actually happening inside an NVIDIA GPU when you run inference workloads, and why µShare's trick works.

**The Problem Setup:**

Picture an SM (Streaming Multiprocessor) on an A40 GPU. It has 1,536 thread slots and six different functional units: FP32 cores, FP64 cores, INT32 cores, Tensor cores, SFU units, and LDST units. When PyTorch launches a matrix multiplication kernel, the hardware scheduler grabs all available SMs and stuffs them with blocks from that *same* kernel.

Here's the issue: a GEMM kernel hammers Tensor cores at 88.52% utilization while the other five units sit at ~5.45% average (Section II-B, Figure 4(b)). The authors call this "1 more, 5 less." All blocks from the same kernel have *identical* resource profiles, so stacking them together means you're wasting 5 out of 6 functional units.

**The Scheduling Reality:**

NVIDIA's dispatch unit uses what the authors call "left-over scheduling" (Section II-C, citing [36], [51], [52]). The rule is simple: if an SM has enough free threads to accommodate a block's `blocksize`, schedule it there. The scheduler doesn't care about functional unit diversity—it only counts threads.

**The Half-Plus Trick:**

Here's the clever bit. The A40 has 1,536 threads per SM. If you set a kernel's blocksize to `768 + 32 = 800` (slightly more than half), then:
- One block occupies 800 threads
- Remaining capacity: 736 threads  
- Can another 800-thread block fit? **No** (736 < 800)
- Can a smaller block from a *different* kernel fit? **Yes**

Look at Figure 5(c): when kernel A has blocksize=1024 and kernel B has blocksize=512, the scheduler *must* interleave them within the same SM because two 1024-blocks won't fit together. This forces "scattered co-location" of blocks with complementary resource demands.

**The Full Pipeline:**

1. **Profiler** (offline): Records each kernel's 9-tuple including utilization of all six hardware types (Formula 1, Section III-B)
2. **Kernel Interceptor**: Uses `LD_PRELOAD` to hijack `cudaLaunchKernel` calls, extracts `blockDim` parameters (Listing 1, Section III-C)
3. **Shaper**: Computes launch slack `s^k = t^k_launch - t^k_intercept` (Formula 2). Kernels running late get their blocksize shaped to half-plus and launched immediately. Normal kernels wait for complementary partners.
4. **Time-shifted Launch**: Non-urgent kernels are held in a queue until the GPU has blocks with different dominant resources executing

---

## Q2: The Key Insight

**The Single Hardware Insight:**

The GPU's closed-source hardware scheduler only looks at one thing when placing blocks: *thread count*. It doesn't consider functional unit diversity, memory bandwidth, or register pressure for placement decisions. µShare exploits this by manipulating the *one* parameter the scheduler does respect—blocksize—to indirectly force the scheduling outcome the authors want.

**Why Half-Plus Specifically:**

The magic number isn't arbitrary. On a 1,536-thread SM:
- Blocksize ≤ 768: Two or more blocks from the same kernel can stack (bad)
- Blocksize > 768 but ≤ 1024: Only one block fits, leaving room for a smaller complementary block (good)
- The "+32" (making it 800) ensures alignment with warp granularity (32 threads) to avoid thread fragmentation

**What Makes This Non-Intrusive:**

Previous approaches required either:
1. **Kernel fusion** (Tacker [62], Rammer [29]): Rewriting CUDA code to merge kernels—requires source access
2. **Hardware modification** (CCWS [43], Prema [11]): Redesigning the dispatch unit—validated only in simulators

µShare works entirely at the library interposition layer. The `LD_PRELOAD` mechanism captures kernel launches *after* PyTorch compiles them but *before* they hit the GPU driver. The authors intercept the function address via `dlsym()`, modify the `blockDim` parameter in-place, then call the original function (Section III-C, IV-A).

**The Structural Delta vs. Baseline:**

| Aspect | INFless/Orion | µShare |
|--------|---------------|--------|
| Scheduling unit | Model/Kernel | Block |
| Co-location scope | Inter-SM | Intra-SM |
| Hardware modification | None | None |
| Kernel code modification | None | None (parameter only) |
| What's changed | Launch timing | Launch timing + blocksize |

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Real Hardware, Production Traces (Section V-A)**

The experiments run on actual A40 and A800 GPUs using Azure inference traces from INFless [55]. This isn't a simulator study. Figure 11 shows realistic bursty arrival patterns across 10 models. The 8-server deployment with 40 model replicas (Table III) is a credible scale.

**2. Comprehensive Breakdown Analysis (Section V-G)**

Figure 22 isolates each component's contribution:
- Without shaping: throughput drops 30.95%, SLO violations increase 6.33%
- With fixed blocksize=1024: throughput drops 3.36%
- Without batch management: throughput increases but SLO violations spike to 21.90%

This demonstrates the shaping mechanism isn't just noise—it's the primary driver.

**3. Kernel-Level Utilization Measurement (Figure 20)**

The authors use Nsight Compute to measure actual functional unit utilization, not just nvidia-smi's misleading "active time" metric. The timeline visualization in Figure 20 shows µShare achieving 15.1% average utilization across six hardware types vs. 10.9% (INFless) and 9.37% (Orion). This is the right metric.

**4. Portability Evidence (Section IV-B)**

The authors acknowledge A800's 2,048 threads/SM breaks the half-plus assumption and adapt to "1/3-plus" (blocksize > 682). Figure 21 shows this still delivers 16.45%-52.29% improvement, validating the approach generalizes.

### Weaknesses

**1. Modifiable Kernel Coverage is Limited**

Table I and II reveal only 51.63% of kernel *executions* have modifiable blocksize. Critically, the *time-dominant* kernels—CUTLASS GEMM (223,538 µs) and MatMul (33,665 µs)—are unmodifiable (Table II). These closed-source cuDNN/cuBLAS kernels account for the majority of execution time. Figure 13(a) shows throughput improvement drops from 58.91 to 47.59 as unmodifiable kernels increase to 100%. The "worst case falls back to INFless" admission (Section V-B) is honest but sobering.

**2. SLO Violation Increases Are Non-Trivial**

Figure 16 shows µShare's 3.35% average SLO violation vs. INFless's 2.05% and Orion's 1.12%. For latency-sensitive inference services, a 63%+ increase in SLO violations may be unacceptable. The authors offer hyperparameter tuning (Figure 17-18) to trade throughput for SLO compliance, but the default configuration prioritizes throughput.

**3. Profiling Overhead is Substantial**

Section V-J mentions offline profiling costs of 105-393 seconds per model, and **7,160 seconds (~2 hours) for Llama2-7b**. For production deployments with frequent model updates, this is a significant operational burden. The profiler must re-characterize every time batch size or model weights change.

**4. The "Complementary Resources" Assumption**

The scattered co-location benefit only materializes when co-located kernels use *different* dominant hardware. Figure 7 shows that when kernels share the same dominant resource (last 4 bars), throughput actually *decreases* by 10.37%. The system relies on workload diversity that may not always exist.

**5. Limited LLM Evaluation**

Llama2-7b is tested with fixed input/output length of 10 tokens (Section V-A). Real LLM inference involves variable sequence lengths, KV-cache management, and continuous batching—none of which are addressed. The 400ms SLO for an LLM is also unrealistically generous.

---

## Q4: What the Authors Didn't Tell You

**1. The Register and Shared Memory Elephant**

Formula 1 profiles `r^k_mem` (shared memory) and `r^k_reg` (registers) for each kernel. Section III-D mentions these as "limiting factors for GPU scheduling other than blocksize." However, the paper never quantifies how often these constraints *actually block* co-location. If a half-plus block uses 48KB of shared memory and the SM only has 100KB total (A40), there's only 52KB left—potentially insufficient for the "complementary" kernel. The authors hand-wave this with "available shared memory, registers...are sufficient" (Section III-D) without showing how frequently this condition fails.

**2. The Time-Shifted Launch Queue Depth Problem**

When kernels wait for complementary partners (time-shifted launch, Section III-D), they sit in a queue. The paper specifies waiting "β microseconds" before rechecking, with β=10 (Section V-A). But what if no complementary kernel arrives? The queue can grow unbounded, and the exponential decay batch adjustment (Section III-E) can't help if the problem is kernel-level starvation, not request-level overload.

**3. The Blocksize Modification Correctness Assumption**

The paper categorizes kernels as "modifiable" if changing blocksize doesn't break correctness (Section III-C). But how was this determined? The authors mention "kernels that produce incorrect results after blocksize modification are also unmodifiable (e.g., tiling-based kernels like Conv2d, which trigger a CUDA internal error)". This suggests they discovered correctness issues empirically. There's no formal analysis of *which* CUDA kernel patterns are safe to reshape—a significant gap for production deployment.

**4. The 60ns Overhead Claim is Misleading**

Figure 25 shows 60.35ns average processing time per kernel. However, this measures only the shaper's decision logic in shared memory. It excludes:
- `LD_PRELOAD` interception overhead
- `dlsym()` function pointer resolution
- The actual kernel launch API call
- Any synchronization between control and inference processes

The true end-to-end overhead per kernel launch is likely 10-100x higher.

**5. The A800 "1/3-Plus" Penalty**

Section V-F admits "the improvement in µShare on the A800 GPU is slightly smaller than on the A40 GPU" because 1/3-plus allows "two 1/3-plus blocks per SM" from the same kernel. This means on A800, you get 2/3 of an SM's threads running identical blocks (stacked) plus 1/3 running a different kernel—a less favorable ratio than A40's 1/2 + 1/2 split. The paper doesn't analyze whether future GPUs with even more threads per SM (e.g., H100's 2,048) will further degrade this approach.

**6. cuDNN/cuBLAS Version Sensitivity**

The unmodifiable kernels (Table II) come from cuDNN and cuBLAS. These libraries are versioned and NVIDIA regularly changes their internal kernel implementations. A kernel that's unmodifiable in CUDA 11.8 might have different blocksize behavior in CUDA 12.x. The paper uses CUDA 11.8 on A40 and CUDA 12.1 on A800 but doesn't discuss version sensitivity.

**7. No Discussion of SM Partitioning Interference**

MIG and MPS (mentioned in Section I) physically partition SMs. If a user enables MPS with 50% SM allocation, half-plus shaping targets the wrong thread count (it assumes full SM access). The paper evaluates µShare against INFless and Orion but doesn't test *combined* deployments with MIG/MPS active.