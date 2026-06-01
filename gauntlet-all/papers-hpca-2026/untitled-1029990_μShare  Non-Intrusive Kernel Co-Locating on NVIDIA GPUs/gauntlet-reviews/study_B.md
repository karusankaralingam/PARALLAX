# Study B — Rich Directive
**Paper:** 1029990 μShare  Non Intrusive Kernel Co Locating on NVIDIA GPUs  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:25

---

Q1: Whiteboard Explanation

Let me explain µShare as if presenting at a whiteboard to a colleague.

**The Problem Setup:**
NVIDIA GPUs have Streaming Multiprocessors (SMs), each containing six types of functional units: FP32 cores, FP64 cores, INT32 cores, Tensor cores, SFU units, and Load/Store units. When you run inference workloads, NVIDIA's hardware scheduler places thread blocks onto SMs, but it does so in a way that creates a "stacking" problem.

Here's what happens: When kernel A launches with 200 blocks, the scheduler fills SM0 with blocks from A, SM1 with blocks from A, and so on. All blocks from the same kernel have identical resource demands—if kernel A is a matrix multiply using Tensor cores, every block saturates Tensor cores while leaving FP32, INT32, SFU largely idle. The authors measured this empirically and found what they call a "1 more, 5 less" pattern: one hardware unit at ~30% utilization, the other five at ~5%.

**The Key Constraint:**
NVIDIA's hardware scheduler is closed-source. You cannot modify block placement logic. The only knobs available before launching are kernel parameters like blocksize (threads per block) and launch timing.

**The Half-Plus Trick:**
The insight is to exploit how the scheduler performs "leftover" scheduling. The scheduler assigns a block to an SM if that SM has enough remaining thread capacity. On A40, each SM supports 1,536 threads.

If you set blocksize to 800 (slightly more than half of 1,536), then after placing one block on SM0, only 736 threads remain—not enough for another 800-thread block from the same kernel. The scheduler must look elsewhere. This forces blocks from the same kernel to scatter across different SMs.

Now when kernel B launches with a smaller blocksize (say 256), those blocks can fit in the leftover capacity of SMs already hosting kernel A blocks. Result: different kernels co-locate within the same SM, enabling parallel use of different functional units.

**System Architecture:**
µShare intercepts CUDA kernel launches via LD_PRELOAD (no source modification needed), reshapes blocksize for kernels that are running late relative to their profiled launch times, and uses time-shifted launching for kernels whose parameters cannot be modified (closed-source cuDNN/cuBLAS). A feedback-controlled batch manager adjusts batch sizes to maintain SLO compliance.

---

Q2: The Key Insight

The central insight is that **the GPU hardware scheduler's block placement decisions can be indirectly controlled by manipulating blocksize parameters, without any access to the closed-source scheduler itself**.

Specifically, by setting blocksize to "half-plus" of the SM thread capacity, the authors exploit the scheduler's leftover-based placement policy: once one large block occupies an SM, only smaller blocks from *different* kernels can fit in the remaining capacity. This transforms what would be homogeneous "stacked" co-location (same kernel's blocks sharing an SM) into heterogeneous "scattered" co-location (different kernels' blocks sharing an SM).

This is genuinely novel because prior work on intra-SM co-location required either (1) kernel fusion, which demands source code access and is complex, or (2) hardware modifications, which are infeasible on production GPUs. µShare achieves the same end goal—multiple kernels concurrently using different functional units within one SM—through a non-intrusive parameter manipulation that works on commodity NVIDIA hardware.

The insight's validity rests on an empirical observation: the default blocksizes chosen by frameworks like PyTorch (32-512 threads) are optimized for single-kernel execution and are always below half the SM capacity. This creates the opportunity for complementary shaping.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive hardware measurement methodology**: The authors correctly distinguish between NVIDIA-SMI's misleading "GPU active time" metric (81.16%) and Nsight Compute's actual functional unit utilization (9.28%). This is a valuable contribution to the community's understanding of GPU efficiency.

2. **Real trace-driven evaluation**: Using Azure inference traces from INFless provides realistic workload patterns rather than synthetic stress tests.

3. **Broad model coverage**: 10 models spanning CNNs, Transformers, RNNs, and LLMs (including Llama2-7b) with different architectural characteristics.

4. **Honest accounting of limitations**: The authors explicitly measure that 48.37% of kernel invocations are unmodifiable (cuDNN, cuBLAS), and show how throughput degrades as this fraction increases (Figure 13).

5. **Overhead characterization**: 60ns average per-kernel overhead is impressively low; 6.85% CPU overhead is negligible.

**Weaknesses:**

1. **Limited baseline comparison for intra-SM techniques**: The comparison with Tacker is limited to just two models (ResNet50, BERT) with SLO management disabled. This is a significant gap since Tacker is the most directly comparable intra-SM approach.

2. **A800 results are weaker and under-explained**: The 1/3-plus strategy on A800 yields smaller improvements (16.45% vs 26.90% on A40). The explanation that "2/3 of threads may use the same resources" is hand-wavy. This suggests the technique's benefits are architecture-dependent in ways not fully understood.

3. **SLO violation increases are non-trivial**: SLO violations increase from 1.12%-2.05% (baselines) to 3.35% (µShare). For latency-sensitive production services, this 1.3-2.2 percentage point increase may be unacceptable. The paper downplays this by emphasizing throughput gains.

4. **Kernel interference not deeply analyzed**: When two kernels share an SM, they compete for shared resources (shared memory, registers, L1 cache, warp schedulers). The paper doesn't measure or model this interference beyond aggregate utilization numbers.

5. **Profiling assumptions**: The system assumes kernel behavior is deterministic across runs and that offline profiling accurately predicts runtime behavior. No sensitivity analysis to profiling errors is provided.

6. **Missing comparison to MIG/MPS**: While mentioned, there's no direct head-to-head comparison showing why kernel-level co-location beats SM-level partitioning in practice.

---

Q4: What the Authors Didn't Tell You

**Engineering Challenges Glossed Over:**

1. **Blocksize modification correctness**: The paper mentions some kernels produce "incorrect results after blocksize modification" (e.g., tiling-based Conv2d) but provides no systematic characterization of which kernels are safe to modify. In practice, determining this requires careful validation for each kernel.

2. **The 51.63% modifiable figure is fragile**: This percentage depends heavily on the specific models chosen. cuDNN-heavy workloads (most production vision models) would have far fewer modifiable kernels.

3. **Register pressure effects**: Increasing blocksize can increase register pressure per SM, potentially reducing occupancy. The paper doesn't discuss how half-plus sizing interacts with register allocation.

**Scalability Concerns:**

4. **Multi-tenant isolation**: In cloud environments, co-locating kernels from different users raises security/fairness concerns. The paper focuses purely on performance.

5. **Profiling cost at scale**: 7,160 seconds to profile Llama2-7b is nearly 2 hours. For organizations with hundreds of model variants, this becomes a significant operational burden.

**What Would Break This:**

6. **Future GPU architectures**: If NVIDIA changes their scheduler's leftover policy or increases SM thread capacity beyond 2×1024, the half-plus trick may need redesign or become infeasible.

7. **Memory bandwidth saturation**: Many inference workloads are memory-bound, not compute-bound. Co-locating two memory-intensive kernels provides no benefit—the paper's "resource complementarity" assumption doesn't always hold.

8. **Dynamic blocksizes**: Some kernels (dynamic batching, variable sequence length) determine blocksize at runtime. µShare's interception-based approach cannot handle these cases cleanly.

**Unanswered Questions:**

9. **Why not shared memory shaping?** The paper claims shared memory "doesn't achieve the same effect" but provides no experimental evidence or explanation why the scheduler treats thread count differently from shared memory in placement decisions.

10. **Warp scheduler contention**: Even with complementary functional units, two kernels share the four warp schedulers per SM. At high occupancy, this becomes a bottleneck not addressed in the paper.