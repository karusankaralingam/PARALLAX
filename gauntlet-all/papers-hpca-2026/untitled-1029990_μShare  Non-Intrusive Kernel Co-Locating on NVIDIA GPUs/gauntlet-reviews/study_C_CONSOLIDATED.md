# Study C — Multi-Persona Synthesis
**Paper:** 1029990 μShare  Non Intrusive Kernel Co Locating on NVIDIA GPUs  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 07:30

---

# Q1: Whiteboard Explanation

Let me walk you through what's actually happening inside an NVIDIA GPU when you run inference workloads, and why µShare's trick works.

**The Problem Setup:**
Picture an SM (Streaming Multiprocessor) on an A40 GPU. It has 1,536 thread slots and six different functional units: FP32 cores, FP64 cores, INT32 cores, Tensor cores, SFU units, and LDST units. When PyTorch launches a matrix multiplication kernel, the hardware scheduler grabs all available SMs and stuffs them with blocks from that *same* kernel.

Here's the issue: a GEMM kernel hammers Tensor cores at 88.52% utilization while the other five units sit at ~5.45% average (Section II-B, Figure 4(b)). The authors call this the "1 more, 5 less" pattern—one resource type is hot, five are cold. All blocks from the same kernel have *identical* resource profiles, so stacking them together wastes 5 out of 6 functional units.

**The Scheduling Reality:**
NVIDIA's dispatch unit uses what the authors call "left-over scheduling" (Section II-C, citing [36], [51], [52]). The rule is simple: if an SM has enough free threads to accommodate a block's `blocksize`, schedule it there. The scheduler doesn't care about functional unit diversity—it only counts threads.

**The Half-Plus Trick:**
Here's the clever exploit. The A40 has 1,536 threads per SM. If you set a kernel's blocksize to `768 + 32 = 800` (slightly more than half), then:
- One block occupies 800 threads
- Remaining capacity: 736 threads
- Can another 800-thread block fit? **No** (736 < 800)
- Can a smaller block from a *different* kernel fit? **Yes**

Look at Figure 5(c): when kernel A has blocksize=1024 and kernel B has blocksize=512, the scheduler *must* interleave them within the same SM because two 1024-blocks won't fit together. This forces "scattered co-location" of blocks with complementary resource demands.

**The Full System Pipeline:**
1. **Profiler** (offline): Records each kernel's 9-tuple including utilization of all six hardware types (Formula 1, Section III-B)
2. **Kernel Interceptor**: Uses `LD_PRELOAD` to hijack `cudaLaunchKernel` calls, extracts `blockDim` parameters (Listing 1, Section III-C)
3. **Shaper**: Computes launch slack `s^k = t^k_launch - t^k_intercept` (Formula 2). Kernels running late get their blocksize shaped to half-plus and launched immediately. Normal kernels wait for complementary partners.
4. **Time-shifted Launch**: Non-urgent kernels are held in a queue until the GPU has blocks with different dominant resources executing
5. **Batch Manager**: Adjusts batch sizes based on SLO feedback using exponential decay (Section III-E)

# Q2: The Key Insight

**The Core Intellectual Contribution:**
The GPU's closed-source hardware scheduler only looks at one thing when placing blocks: *thread count*. It doesn't consider functional unit diversity, memory bandwidth, or register pressure for placement decisions. µShare exploits this by manipulating the *one* parameter the scheduler does respect—blocksize—to indirectly force the scheduling outcome the authors want.

This is essentially *adversarial scheduling*—treating the closed-source scheduler as a black box and crafting inputs (blocksize, launch timing) that steer it toward better resource utilization.

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

**The Structural Delta vs. Baselines:**

| Aspect | INFless/Orion | µShare |
|--------|---------------|--------|
| Scheduling unit | Model/Kernel | Block |
| Co-location scope | Inter-SM | Intra-SM |
| Hardware modification | None | None |
| Kernel code modification | None | None (parameter only) |
| What's changed | Launch timing | Launch timing + blocksize |

# Q3: Evaluation Critique

## Strengths

**1. Real Hardware with Production-Relevant Workloads:**
All experiments run on actual NVIDIA A40 and A800 GPUs (Section V-A), not simulators. The 10-model benchmark (Table III) includes genuine production models: Llama2-7b, BERT, ResNet50, ViT. The Azure inference traces from INFless [55] (Figure 11) add realism with bursty arrival patterns. The 8-server deployment with 40 model replicas is a credible scale.

**2. Comprehensive Breakdown Analysis (Section V-G):**
Figure 22 isolates each component's contribution:
- Without shaping: throughput drops 30.95%, SLO violations increase 6.33%
- With fixed blocksize=1024: throughput drops 3.36%
- Without batch management: throughput increases but SLO violations spike to 21.90%

This demonstrates the shaping mechanism is the primary driver, not noise.

**3. Hardware Utilization Ground Truth (Figure 20):**
The authors use Nsight Compute to measure actual functional unit utilization—the right metric—not the misleading nvidia-smi "GPU utilization" that reports 81.16% when actual hardware utilization is 9.28% (Section II-B). The timeline visualization shows µShare achieving 15.1% average utilization across six hardware types vs. 10.9% (INFless) and 9.37% (Orion).

**4. Honest Treatment of Limitations:**
Tables I and II explicitly categorize modifiable vs. unmodifiable kernels (51.63% vs. 48.37%). Figure 13 shows graceful degradation as unmodifiable kernel proportion increases. They don't hide that cuDNN and tiled kernels can't be reshaped.

**5. Direct Comparison with Intra-SM Fusion (Figure 14):**
The Tacker comparison is valuable because it's the most relevant prior work. The 20.38% improvement over kernel fusion demonstrates the benefit of *cross-task* co-location over *intra-model* fusion.

## Weaknesses

**1. Modifiable Kernel Coverage is Limited and Time-Biased:**
Table I and II reveal only 51.63% of kernel *invocations* have modifiable blocksize. Critically, the *time-dominant* kernels—CUTLASS GEMM (223,538 µs) and MatMul (33,665 µs)—are unmodifiable (Table II). By execution time, unmodifiable kernels actually dominate at 53% (417,314µs vs. 370,709µs). Figure 13(a) shows throughput improvement drops from 58.91 to 47.59 as unmodifiable kernels increase to 100%.

**2. The "Half-Plus" Heuristic is Architecture-Fragile:**
On A800 GPUs (2,048 threads/SM), the half-plus strategy fails because even blocksize=1024 allows two large blocks per SM. The authors pivot to "1/3-plus" shaping (Section IV-B), but improvements drop from 26.90% (A40) to 16.45% (A800). The paper admits this "may lead to slightly unbalanced SM thread allocation"—but this fundamentally weakens the scattering guarantee. Future GPUs with different thread counts will require entirely new heuristics.

**3. SLO Violation Increases Are Non-Trivial:**
Figure 16 shows µShare's 3.35% average SLO violation vs. INFless's 2.05% and Orion's 1.12%—a 63-199% increase. For latency-sensitive inference services, this may be unacceptable. While hyperparameter tuning (Figures 17-18) can reduce violations to 0.84% (µShare_v7), this comes at the cost of reducing throughput improvement from 26.90% to 19.28%.

**4. Profiling Overhead is Substantial:**
Section V-J mentions offline profiling costs of 105-393 seconds per model, and **7,160 seconds (~2 hours) for Llama2-7b**. For production deployments with frequent model updates, this is a significant operational burden. The paper doesn't discuss what triggers re-profiling or how often it's needed.

**5. Limited LLM Evaluation:**
Llama2-7b is tested with fixed input/output length of 10 tokens (Section V-A). Real LLM inference involves variable sequence lengths, KV-cache management, and continuous batching—none of which are addressed. The 400ms SLO for an LLM is also unrealistically generous.

**6. Validation of Scheduler Assumptions is Indirect:**
The paper claims NVIDIA uses "leftover scheduling" but cites only 2012 papers about memory defragmentation. Figure 5's demonstration of scattered vs. stacked placement is compelling, but they read block placement via inline PTX assembly—they never *directly observe* the scheduler's decision logic. This is reverse-engineering, not documentation.

# Q4: What the Authors Didn't Tell You

**1. Register and Shared Memory Pressure Are Hand-Waved:**
Formula 1 profiles `r^k_mem` (shared memory) and `r^k_reg` (registers), and Section III-D mentions these as "limiting factors." However, the paper never quantifies how often these constraints *actually block* co-location. If a half-plus block uses 48KB of shared memory and the SM only has 100KB total (A40), there's only 52KB left—potentially insufficient for the "complementary" kernel. Larger blocks can also hit the SM's 65,536 register limit, causing register spilling—a performance cliff the paper doesn't characterize.

**2. Blocksize Modification Correctness is Empirically Determined:**
Section III-C admits some kernels (e.g., `Conv2d` with tiling) "trigger a CUDA internal error when modified." The paper classifies these as "unmodifiable," but there's no formal analysis of *which* CUDA kernel patterns are safe to reshape. There's also a grey zone: kernels that *run* with modified blocksize but produce suboptimal memory access patterns (breaking coalescing). The paper doesn't profile whether shaping degrades per-kernel efficiency.

**3. The 60ns Overhead Claim is Incomplete:**
Figure 25 shows 60.35ns average processing time per kernel, but this measures only the shaper's decision logic in shared memory. It excludes: `LD_PRELOAD` interception overhead, `dlsym()` function pointer resolution, the actual kernel launch API call, and any synchronization between control and inference processes. The true end-to-end overhead per kernel launch is likely significantly higher.

**4. Memory Bandwidth Contention is Ignored:**
The paper focuses exclusively on compute resource sharing but never models DRAM bandwidth or L2 cache contention. If two co-located kernels both have high LDST utilization (even if they use different compute units), they'll contend for shared memory bandwidth. The related work mentions SGDRC for memory channel isolation, but µShare doesn't integrate or evaluate against such techniques.

**5. The "Complementary Resources" Assumption May Not Hold:**
The scattered co-location benefit only materializes when co-located kernels use *different* dominant hardware. Figure 7 shows that when kernels share the same dominant resource (last 4 bars), throughput actually *decreases* by 10.37%. The paper doesn't quantify how often real workload mixes have complementary demands—consider co-locating two Transformer models that BOTH primarily use Tensor cores.

**6. The Time-Shifted Launch Queue Has Hidden Risks:**
When kernels wait for complementary partners (Section III-D), they sit in a queue for `β microseconds` (β=10, Section V-A) before rechecking. But what if no complementary kernel arrives? The queue can grow unbounded. The exponential decay batch adjustment (λ=-0.1 to -0.2) can cause severe throughput collapse under bursty traffic—a slack of -20ms triggers batch reduction of ~7.4, potentially oscillating to batch=1 within 2 time windows.

**7. Multi-Tenant Security Implications Unaddressed:**
The `LD_PRELOAD` approach requires the cloud provider to deploy µShare's shared library. In multi-tenant environments: Can User A observe User B's resource patterns through co-location timing? Can malicious blocksize choices starve other users? How does the batch manager handle competing SLOs? These isolation and fairness implications are completely unaddressed for a paper targeting "public cloud deployment."