# Paper Deconstruction: µShare: Non-Intrusive Kernel Co-Locating on NVIDIA GPUs

## Q1: Whiteboard Explanation

Imagine you're running a factory with 84 assembly lines (SMs), and each line has six different specialized workstations: welders (FP32), precision welders (FP64), counters (INT32), forklifts (LDST), special tools (SFU), and AI robots (Tensor cores).

**The Problem:** When NVIDIA's factory manager (the hardware block scheduler) assigns work orders (blocks), it does something dumb. If you send in an order for 100 "matrix multiply" jobs, the manager stacks them all onto the same assembly lines first. So assembly line #1 gets jobs 1-5, line #2 gets jobs 6-10, etc. The catch? Matrix multiply jobs *only* use the AI robots (Tensor cores at 88% utilization) while the welders, forklifts, and everything else sit idle (5.45% average for the other five hardware types—see Section I, Introduction).

This is what the authors call **"stacked co-location"** (Figure 1(a)). Even if you simultaneously submit a second batch of "layer normalization" jobs that desperately need forklifts (LDST), the manager won't mix them onto the same line until the first batch is nearly done (Figure 3(a) shows this serialization empirically).

**The Trick:** The authors discovered a clever hack. The factory manager uses a simple rule: "If the remaining worker capacity on a line can fit a work order, schedule it there." On NVIDIA A40, each line can hold 1,536 workers (threads).

So here's the exploit: If you artificially resize your matrix multiply work orders to require *slightly more than half* the line's capacity (e.g., 800 workers instead of 512), then **no two matrix multiply orders can fit on the same line**. Order 1 takes 800 slots on line #1, leaving only 736 slots—too few for another 800-worker order. But a layer normalization order using only 256 workers? That fits perfectly in the leftover 736 slots!

This is **"half-plus blocksize shaping"** (Section III-D). By reshaping the blocksize parameter before launch (intercepted via `LD_PRELOAD` on the CUDA runtime—Section III-C), they force the closed-source hardware scheduler to scatter blocks across SMs and create gaps that complementary kernels can fill.

**The Result:** Instead of AI robots running at 88% while forklifts idle, you now have AI robots and forklifts working simultaneously on the same assembly line. Hardware utilization jumps from ~9-11% to ~15% (Figure 20), and throughput improves 27-54% over baselines (Section V-B).

---

## Q2: The Key Insight

**The Delta:** This paper's genuine contribution is the discovery that **blocksize is the singular configurable parameter** that can indirectly influence NVIDIA's closed-source block-to-SM scheduler to achieve intra-SM co-location of heterogeneous kernels—without modifying kernel source code, GPU hardware, or CUDA libraries.

Prior work fell into two camps:
1. **Kernel fusion** (Tacker [62], Rammer [29], COMBO [4]): Requires source-level access to merge kernels—infeasible in cloud environments with cuDNN/cuBLAS black boxes.
2. **Hardware redesign** (CCWS [43], Prema [11]): Proposes new scheduling interfaces validated only in simulators—irrelevant for production NVIDIA silicon.

The key mechanism insight is articulated in Section II-C (Observation #3): Under NVIDIA's "leftover scheduling" strategy, where blocks are placed on SMs with sufficient remaining thread capacity, setting blocksize to `thread_capacity/2 + α` (e.g., 800 on A40 with 1,536 threads) creates a **pigeonhole constraint**. The scheduler cannot place two blocks from the same kernel on one SM, forcing distribution across SMs and leaving ~736 threads as "leftover slots" that small blocks from other kernels can occupy.

The critical empirical validation is in Figure 5: When both kernels use blocksize 1024 (Figure 5(a)) or both use 512 (Figure 5(b)), blocks execute serially. Only the asymmetric configuration—one kernel at 1024, one at 512 (Figure 5(c))—achieves parallel execution within the SM.

**What Makes This Non-Obvious:** The paper explicitly states (Section II-C) that shared memory configuration *does not* achieve this effect—only blocksize works. The authors also quantify that 51.63% of kernel invocations across their 10-model benchmark are "modifiable" (Table I), meaning half of production inference traffic can benefit directly.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real Hardware, Production-Relevant Workloads:** Unlike much GPU architecture work validated on simulators (GPGPU-Sim), this runs on actual NVIDIA A40 and A800 GPUs (Section V-A). The 10-model benchmark (Table III) includes genuine production models: Llama2-7b, BERT, ResNet50, ViT—not synthetic microbenchmarks. The Azure inference traces (Figure 11) add realism to the evaluation.

2. **Comprehensive Baseline Comparison:** They compare against INFless (ASPLOS '22) for inter-SM sharing and Orion (EuroSys '24) for kernel-level scheduling—both recent, peer-reviewed systems. The comparison with Tacker (HPCA '22) in Figure 14 directly addresses the kernel fusion alternative.

3. **Honest SLO Trade-off Analysis:** Figure 17-18 transparently show that µShare's throughput gains (58.91 normalized) come with higher SLO violations (3.35% vs. 2.05% for INFless). They provide nine configurations (v1-v9) demonstrating the tunability, and µShare_v7 achieves *lower* violations than baselines (0.84%) while maintaining 19-44% throughput improvement.

4. **Hardware Utilization Ground Truth:** Figure 20's timeline visualization using Nsight Compute provides per-hardware-unit (FP32, FP64, INT32, LDST, SFU, Tensor) utilization—the actual metric that matters, not the misleading nvidia-smi "GPU utilization" that reports 81% when Nsight shows 9% (Section II-B).

5. **Overhead Quantification:** They report 60.35ns per-kernel control overhead (Figure 25), 6.85% CPU overhead (Figure 26), and offline profiling costs (105-393s per model). This is the level of detail reviewers expect.

### Weaknesses

1. **Modifiable Kernel Percentage Is Workload-Dependent:** The 51.63% "modifiable" figure (Section III-C) comes from their specific model selection. Production LLM inference is increasingly dominated by cuBLAS/cuDNN GEMM calls (Table II shows CUTLASS GEMM alone accounts for 1,293 invocations/223ms). If your workload is 80% unmodifiable kernels, the benefit degrades significantly—Figure 13(a) shows throughput dropping from 58.81 to 47.59 as unmodifiable percentage rises from 48% to 100%.

2. **"Half-Plus" Doesn't Scale to A800's 2048-Thread SMs:** Section IV-B admits that on A100/A800 GPUs (2048 threads/SM), "half-plus" fails because two 1024-block threads still fit. They pivot to "1/3-plus" (704+ threads), which allows *two* blocks from the same kernel per SM before the leftover constraint kicks in. This fundamentally weakens the isolation property. Figure 21(a) shows A800 improvements are only 16.45% vs. 26.90% on A40—the mechanism is degraded on more modern hardware.

3. **Latency Distribution Hides Tail Behavior:** Figure 19 shows CDF plots, but the 99th percentile reduction (25-31%) masks whether there are 99.9th percentile outliers. For SLO-bound inference, P99.9 matters enormously. The paper never reports beyond P99.

4. **No Memory Bandwidth Contention Analysis:** The six intra-SM hardware units are execution resources, but GPU memory bandwidth is a shared off-chip resource. Co-locating two LDST-heavy kernels could saturate memory even if intra-SM resources have complementary profiles. The authors cite SGDRC [60] as "orthogonal" (Section VI) but never measure memory bandwidth interference empirically.

5. **Static Profiling Assumes Batch-Independent Resource Profiles:** Formula 1 profiles each kernel once at `max_batch`. But kernels like attention have batch-dependent memory access patterns (quadratic in sequence length). The paper never validates that half-plus shaping remains effective as batch size fluctuates dynamically via the Batch Manager (Section III-E).

6. **Limited LLM Evaluation:** Llama2-7b is constrained to batch=14, 10-token I/O (Section V-A). Modern LLM serving involves continuous batching, KV-cache management, and speculative decoding—none of which are evaluated. The 7160s profiling time for Llama2-7b (Section V-J) suggests scalability concerns for larger models.

---

## Q4: What the Authors Didn't Tell You

1. **The 51.63% Modifiable Kernel Claim Is Generous:** Tables I and II reveal that "modifiable" includes kernels like `RNN Cell` (1,002 invocations) and `Vec Element` (971 invocations)—these are open-source PyTorch kernels. But the *execution time* story is different: unmodifiable kernels account for 417,314µs vs. 370,709µs for modifiable (Tables I-II totals). By time, unmodifiable kernels dominate 53%, not 48%. The throughput impact is more pessimistic than the invocation count suggests.

2. **The "Time-Shifted Launch" Mechanism Is Underspecified:** Section III-D mentions that kernels in set Y wait `β microseconds` before retrying launch. They set β=10µs (Section V-A) but never justify this magic number. For kernels with execution times of 20-200µs (Figure 3), a 10µs polling interval could introduce 5-50% overhead per kernel. The interaction between β and the exponential batch decay (λ=-0.1 to -0.2) is never analyzed.

3. **The Hardware Utilization "Improvement" Is Still Embarrassingly Low:** Figure 20 celebrates µShare achieving 15.1% average utilization vs. INFless's 10.9%. But 15% utilization of six hardware units is still catastrophic. The paper implicitly acknowledges this—the "1 more, 5 less" pattern (Figure 4(b)) means that even with co-location, you're typically running one kernel at ~30% on its dominant resource and another at ~10-20%, yielding blended averages around 15%. True intra-SM efficiency would require co-locating 3-4 kernels with perfectly orthogonal profiles, which the half-plus constraint prevents (only ~1.9 blocks fit per SM).

4. **Blocksize Modification Can Break Correctness:** Section III-C notes that "kernels that produce incorrect results after blocksize modification are also unmodifiable (e.g., tiling-based kernels like Conv2d, which trigger a CUDA internal error when modified)." This is a significant correctness concern. The paper never quantifies how many kernels silently produce wrong results vs. fail loudly. A production system deploying µShare would need exhaustive correctness validation per model.

5. **The Comparison with Tacker Isn't Apples-to-Apples:** Section V-B states they "disable µShare's latency management mechanism" for fairness when comparing to Tacker (Figure 14). But Tacker operates on fused kernels (lower launch overhead, better cache locality), while µShare operates on unfused kernels with interception overhead. The 20% improvement over Tacker may reflect Tacker's suboptimal fusion decisions rather than µShare's superiority.

6. **The Batch Manager's Exponential Decay Is Aggressive:** Section III-E's batch reduction formula `bj+1 = max{bj - e^(λ×slack), 1}` with λ=-0.1 means a slack of -20ms triggers `e^2 ≈ 7.4` batch reduction. For models with max_batch=14 (Llama2-7b), this could oscillate to batch=1 within 2 time windows. The "rapid decline" shown in Figure 10 is presented as a feature, but it could cause severe throughput collapse under bursty traffic.

7. **No Analysis of Warp-Level Effects:** Half-plus blocksize (e.g., 800 threads = 25 warps) creates a non-power-of-2 warp count per block. This can cause sub-warp scheduling inefficiencies on NVIDIA's 4-warp-scheduler-per-SM architecture. The paper never profiles warp-level occupancy or scheduler stalls after blocksize modification.