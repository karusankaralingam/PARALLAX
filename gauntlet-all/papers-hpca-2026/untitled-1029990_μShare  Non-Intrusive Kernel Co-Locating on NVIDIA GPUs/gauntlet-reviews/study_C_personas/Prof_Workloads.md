# Paper Analysis: µShare - Non-Intrusive Kernel Co-Locating on NVIDIA GPUs

## Q1: Whiteboard Explanation

Let me walk you through what this paper is actually doing, step by step.

**The Problem Setup:**
Imagine an NVIDIA GPU SM (Streaming Multiprocessor) as a workshop with 6 different tool stations: FP32 cores, FP64 cores, INT32 cores, Tensor cores, SFU units, and Load/Store units. When you run a matrix multiplication kernel, it's like sending 100 workers who ALL want to use the Tensor core station. The other 5 stations sit idle. The authors call this the "1 more, 5 less" pattern (Section II-B, Figure 4b).

**Why This Happens:**
NVIDIA's hardware scheduler is a greedy, block-oriented beast. When Kernel A launches with 4 blocks, the scheduler stuffs ALL 4 blocks into SMs before even looking at Kernel B. This is "stacked co-location" (Figure 1a). Even if Kernel B uses completely different hardware (say, INT32), it has to wait.

**The Trick (Half-Plus Shaping):**
Here's the clever hack. Each SM on an A40 has capacity for 1,536 threads. If you set your blocksize to exactly 800 threads (slightly MORE than half of 1,536), then:
- One block takes 800 threads → 736 threads remain
- A second block from the SAME kernel needs 800 threads → doesn't fit!
- But a SMALLER block (say, 512 threads) from a DIFFERENT kernel → fits perfectly!

This forces the scheduler to scatter blocks across SMs and interleave different kernels within the same SM (Figure 1b, Figure 5c).

**The System Flow:**
1. **Profiler** (offline): Measures each kernel's hardware utilization profile and launch timing
2. **Kernel Interceptor** (runtime): Hijacks CUDA launch calls via LD_PRELOAD
3. **Shaper** (runtime): Modifies blocksize to half-plus for "late" kernels, delays "normal" kernels for time-shifted launching
4. **Batch Manager**: Adjusts batch sizes based on SLO feedback

---

## Q2: The Key Insight

**The Core Insight:** On closed-source NVIDIA GPUs, you cannot modify the hardware scheduler, but you CAN manipulate its *inputs* to achieve a desired *output*. By setting blocksize to "half-plus" (slightly more than 50% of SM thread capacity), you exploit the scheduler's leftover-based allocation policy to force block scattering without touching any proprietary code.

**Why This Matters:**
Previous work required either (1) invasive kernel code modifications (kernel fusion like Tacker, persistent kernels like ISPA) or (2) hardware redesign validated only on simulators. Both approaches are impractical in public cloud environments where you cannot modify cuDNN/cuBLAS binaries or access GPU RTL.

**The Intellectual Contribution:**
This is essentially *adversarial scheduling* — treating the closed-source scheduler as a black box and crafting inputs (blocksize, launch timing) that steer it toward better resource utilization. The authors reverse-engineered the scheduling behavior through profiling (Section II-C, Observations 1-4) to discover that blocksize is the *only* general-purpose parameter that achieves this scattering effect (shared memory does not work, as noted in Section II-C).

**The Formula That Captures It:**
For GPUs with 1,536 threads/SM: `blocksize ∈ {b | 768 < b ≤ 1024, b ≡ 0 (mod 32)}`
Minimum viable: 800 threads (Section IV-B).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Comprehensive Hardware Utilization Measurement (Figure 20)**
The authors don't just report NVIDIA-SMI utilization (which they correctly call out as misleading — Section II-B shows SMI reports 81.16% while actual Nsight Compute shows 9.28%). They instrument 6 individual hardware unit utilizations over time. This is the right way to measure this problem.

**S2: Honest Breakdown of Modifiable vs. Unmodifiable Kernels (Tables I & II)**
They explicitly acknowledge that 48.37% of kernels are unmodifiable (cuDNN, cuBLAS wrappers). Figure 13 shows graceful degradation as unmodifiable kernel proportion increases. This is refreshingly honest compared to papers that hide such limitations.

**S3: SLO-Throughput Tradeoff Exploration (Figures 17-18)**
They don't just pick one configuration. They sweep hyperparameters (k, λ) and show the Pareto frontier between throughput (58.91 → 53.64 normalized) and SLO violation (3.35% → 0.63%). µShare_v7 achieves lower SLO violation than baselines while still improving throughput.

**S4: Comparison Against Intra-SM Co-location (Figure 14)**
The Tacker comparison is valuable because it's the most relevant prior work. The 20.38% improvement over kernel fusion demonstrates the benefit of *cross-task* co-location over *intra-model* fusion.

### Weaknesses

**W1: The "Cherry-Pick" Concern — Workload Selection Bias**
The 10 models (Table III) are all inference workloads with batch sizes tuned to stay under 200ms SLO. This is convenient because:
- Inference kernels tend to be shorter-duration, making co-location opportunities more frequent
- The "1 more, 5 less" pattern is strongest for specialized inference kernels (CUTLASS GEMM using Tensor cores at 80%+)

**Missing:** Training workloads, pointer-chasing graph algorithms, sparse matrix operations. The Parboil experiment (Section V-H, Figure 23) is a step in the right direction but uses only 5 scientific applications.

**W2: Baseline Validity — Is Orion a Strawman?**
Orion [46] is from EuroSys 2024, so it's recent. But the paper claims Orion uses a "conservative co-location strategy, allowing at most one compute-intensive kernel and one memory-intensive kernel" (Section V-B). If this is Orion's *design choice* (for correctness/QoS), then µShare might be achieving higher throughput by accepting more interference risk. The 54.09% improvement over Orion (Figure 12) may partly reflect different design philosophies rather than pure technique superiority.

**W3: The A800 Results Reveal a Generalization Problem**
On A800 GPUs (2,048 threads/SM), the half-plus strategy fails because even blocksize=1024 allows two large blocks per SM. The authors pivot to "1/3-plus" shaping (Section IV-B), but:
- The improvement drops from 26.90% (A40) to 16.45% (A800)
- They admit this "may lead to slightly unbalanced SM thread allocation" (Section V-F)

This suggests the technique is brittle across GPU generations. What happens on H100 with different SM configurations?

**W4: The Profiling Cost is Buried**
Section V-J mentions offline profiling costs of "105 to 393 seconds" per model, but Llama2-7b requires **7,160 seconds** (nearly 2 hours). For production systems with hundreds of models, this profiling overhead is significant and not adequately discussed.

**W5: Figure 20's Y-Axis is Logarithmic**
The hardware utilization comparison uses a log scale. µShare's "15.1%" vs. INFless's "10.9%" looks dramatic visually, but in absolute terms, this is still extremely low utilization. The paper doesn't adequately explain why scattered co-location only achieves 15% average utilization when the theoretical ceiling should be much higher.

---

## Q4: What the Authors Didn't Tell You

**1. The "Half-Plus" Magic Number is Fragile**
The technique fundamentally depends on the SM thread capacity being exactly 1,536 or 2,048. NVIDIA could change this in the next GPU generation (e.g., Blackwell), and the entire approach would need recalibration. The paper presents this as a general technique, but it's really a reverse-engineered exploit of specific hardware parameters.

**2. What Happens When All Kernels Need the Same Resource?**
The "1 more, 5 less" observation (Figure 4b) assumes kernels have *complementary* resource demands. But consider co-locating two Transformer models that BOTH primarily use Tensor cores (see Table II: CUTLASS Gemm at 80.49% Tensor, MatMul at 92.39% Tensor). Figure 7 shows throughput *decreases* by 10.37% when dominant resources match (the last 4 bars). The paper doesn't quantify how often real workload mixes have complementary demands.

**3. The LD_PRELOAD Approach Has Security Implications**
The kernel interceptor uses LD_PRELOAD to hijack CUDA calls (Section III-C, IV-A). In multi-tenant cloud environments, this requires the cloud provider to trust and deploy µShare's shared library. This is a significant deployment barrier that the paper glosses over.

**4. Unmodifiable Kernels Dominate Execution Time**
Tables I and II show that while modifiable kernels account for 51.63% of *invocations*, the execution time breakdown tells a different story:
- Modifiable kernels: 370,709 µs total
- Unmodifiable kernels: 417,314 µs total

Unmodifiable kernels actually dominate the critical path. The paper's improvements come primarily from optimizing the *less important* (by time) kernel category.

**5. The SLO Violation Rate Tradeoff is Real**
µShare's default configuration has 3.35% SLO violation vs. INFless's 2.05% and Orion's 1.12% (Section V-C). For production systems where SLO compliance is contractually mandated (e.g., 99.9% target), a 3.35% violation rate is unacceptable. The authors do show µShare_v7 can achieve 0.84% violation, but at the cost of reducing throughput improvement from 26.90% to 19.28%.

**6. The "Time-Shifted Launching" is Essentially Queuing Delay**
Section III-D describes how kernels in set Y "wait for β microseconds" before rechecking launch conditions. This is adding artificial latency to achieve co-location. The paper presents this as a feature, but it's fundamentally trading latency for throughput — the same tradeoff every scheduling system makes.