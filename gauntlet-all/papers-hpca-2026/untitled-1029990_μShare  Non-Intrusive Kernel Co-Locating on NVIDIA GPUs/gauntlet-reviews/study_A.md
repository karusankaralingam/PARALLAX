# Study A — Simple Directive
**Paper:** 1029990 μShare  Non Intrusive Kernel Co Locating on NVIDIA GPUs  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:25

---

Q1: Whiteboard Explanation

If I were explaining µShare at a whiteboard, I'd start with the core problem and build up to the solution:

**The Problem: "Stacked Co-location"**

Imagine a GPU with 84 Streaming Multiprocessors (SMs), each containing 6 types of hardware units: FP32 cores, FP64 cores, INT32 cores, Tensor cores, SFU units, and Load/Store units. When you run a matrix multiplication kernel, it might use Tensor cores at 88% utilization while the other 5 hardware types sit at only 5% utilization—what the authors call "1 more, 5 less."

The GPU's hardware scheduler makes this worse. When you launch a kernel with multiple blocks, it stacks identical blocks onto the same SMs. Since all blocks from the same kernel have identical resource demands, you get the same imbalanced hardware usage across all SMs.

**The Key Constraint**

NVIDIA's scheduler is closed-source—we can't modify it. But we discovered the scheduler uses "left-over" scheduling: it places a block on an SM only if remaining threads ≥ blocksize.

**The Solution: Half-Plus Blocksize Shaping**

Here's the trick: If SM thread capacity is 1536, set blocksize to 800 (just over half). Now when one block lands on an SM, only 736 threads remain—not enough for another 800-thread block from the same kernel. This forces identical blocks to scatter across different SMs.

The remaining 736 threads? They can accommodate smaller blocks (256-512 threads) from a *different* kernel with complementary hardware needs. Now an SM runs an FP32-heavy kernel alongside a Tensor-heavy kernel simultaneously.

**Time-Shifted Launching**

For kernels whose blocksize we can't modify (closed-source cuDNN/cuBLAS), we control *when* they launch, waiting until complementary kernels are already running on SMs.

Q2: The Key Insight

The central insight is that **blocksize is an implicit communication channel to the closed-source GPU hardware scheduler**—by setting blocksize to slightly more than half the SM thread capacity, you can indirectly force the scheduler to scatter blocks across SMs rather than stacking them, enabling intra-SM co-location of complementary kernels without any hardware or kernel code modifications.

This is surprising because blocksize was designed simply to specify thread organization, not to influence scheduling topology. The authors reverse-engineered the scheduler's behavior (left-over thread allocation) and exploited it to achieve what would normally require hardware modifications or invasive kernel fusion techniques.

The elegance lies in the constraint exploitation: instead of fighting the black-box scheduler, they shaped kernel parameters so the scheduler's default behavior produces the desired scattered placement. This transforms a seemingly inflexible system into one amenable to software-defined resource optimization.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive baseline comparison**: Compares against both inter-SM systems (INFless, Orion) and intra-SM approaches (Tacker kernel fusion), demonstrating improvements across different co-location paradigms.

2. **Hardware diversity**: Evaluates on both A40 (1536 threads/SM) and A800 (2048 threads/SM) GPUs, showing the technique adapts to different architectures with appropriate modifications (half-plus vs. 1/3-plus).

3. **Production-realistic workloads**: Uses Azure inference traces, 10 diverse models including LLMs (Llama2-7b), and scientific computing workloads from Parboil—not just synthetic benchmarks.

4. **Thorough breakdown analysis**: Figure 22 systematically ablates each component (shaping, batch management), quantifying their individual contributions.

5. **Low-level hardware utilization measurement**: Actually measures the 6 hardware unit utilization rates rather than relying on misleading NVIDIA-SMI metrics (Figure 20).

**Weaknesses:**

1. **Limited training workload evaluation**: The paper focuses on inference; training workloads have different kernel characteristics and the technique's applicability there is unexplored.

2. **Static profiling assumption**: The profiler assumes kernel behavior under max_batch is representative; dynamic workload changes might invalidate profiled complementarity.

3. **51.63% modifiability limitation**: Nearly half of kernels (cuDNN/cuBLAS) have unmodifiable blocksize. The fallback to time-shifted launching may not achieve the same intra-SM co-location quality.

4. **Missing contention analysis**: No systematic study of when co-located kernels compete for the same hardware unit (the "last 4 bars" in Figure 7 showing -10.37% degradation) and how often this occurs in practice.

Q4: What the Authors Didn't Tell You

**Hidden assumptions and limitations:**

1. **Register and shared memory constraints**: The paper briefly mentions these as "limiting factors" but doesn't quantify how often they prevent co-location even when blocksize shaping would otherwise work. On A100/A800, increased register pressure from half-plus blocks could reduce occupancy.

2. **Kernel execution time variance**: The time-shifted launching assumes predictable kernel durations. High variance could cause scheduling misalignment where "complementary" kernels don't actually overlap.

3. **Warp scheduler behavior**: The paper focuses on block placement but ignores intra-SM warp scheduling. Co-located blocks compete for the same warp scheduler slots—the actual instruction-level interleaving may not achieve the theoretical hardware parallelism.

4. **Memory bandwidth contention**: The 6 hardware units are compute-focused; memory bandwidth is shared across all kernels. Memory-bound kernels may not benefit from compute unit complementarity.

**Engineering realities:**

5. **Profiling cost for LLMs**: 7,160 seconds (~2 hours) for Llama2-7b profiling is substantial. Modern LLM deployment involves frequent model updates that would require re-profiling.

6. **PyTorch-specific implementation**: The LD_PRELOAD interception and C10_LAUNCH_BOUNDS modification ties the implementation tightly to PyTorch; other frameworks (TensorRT, TVM in production) would require different approaches.

**What would make this more impactful:**

The technique works because NVIDIA's scheduler uses simple left-over allocation. If NVIDIA changes this behavior in future drivers (e.g., smarter bin-packing), the technique could break. The authors don't discuss robustness to scheduler updates or propose detection mechanisms for when the underlying assumptions fail.