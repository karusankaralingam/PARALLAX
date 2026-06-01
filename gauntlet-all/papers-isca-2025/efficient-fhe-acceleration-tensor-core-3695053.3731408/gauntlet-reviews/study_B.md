# Study B — Rich Directive
**Paper:** 3695053.3731408  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 08:30

---

Q1: Whiteboard Explanation

Let me walk you through Neo as if explaining it at a whiteboard.

**The Problem Context:**
Fully Homomorphic Encryption (FHE) lets you compute on encrypted data without decryption—critical for privacy in cloud computing. The CKKS scheme supports approximate arithmetic on encrypted data, making it practical for ML inference. But FHE is computationally brutal: a single homomorphic multiplication can be 10,000x slower than plaintext.

**Where the Time Goes:**
The bottleneck is the KeySwitch operation, which appears in both HMULT (ciphertext multiplication) and HROTATE (slot rotation). KeySwitch has several sub-operations: ModUp, NTT, Inner Product (IP), INTT, and ModDown. Within these, three kernels dominate: BConv (base conversion), NTT (number-theoretic transform), and IP (inner product with evaluation keys).

**The Core Problem with Prior Work:**
Previous GPU implementations like TensorFHE had three issues:
1. BConv and IP use element-wise multiplications with terrible data reuse—each coefficient gets read from global memory multiple times
2. They only used INT8 Tensor Cores for NTT, requiring expensive bit-splitting (Booth decomposition) for large integers
3. Other kernels didn't use Tensor Cores at all

**Neo's Key Transformations:**

*Algorithm Transformation:* The insight is that both BConv and IP can be reformulated as matrix multiplications. In BConv, you're multiplying coefficients by base conversion factors and accumulating—this is exactly a matmul if you rearrange the data. Instead of accessing each coefficient α' times, you access it once and let matrix multiplication handle the reuse. Same story for IP: instead of β̃ separate element-wise operations, you restructure into BS×β×β̃ matmuls.

*Data Layout:* To make this work, you rearrange polynomials from "coefficients continuous within a limb" to "same-position coefficients across limbs continuous." This enables coalesced memory access for the matrix operations.

*FP64 Tensor Cores:* Here's the clever hardware insight. A100 Tensor Cores support both INT8 (624 TOPS) and FP64 (19.5 TFLOPS). But for 36-bit integers, INT8 requires splitting into 5 parts with 25 partial products plus complex merging. FP64 has 53 bits of mantissa precision—enough to represent integers up to 2^53. A 36-bit integer times 12 bits times 16 accumulations stays under 2^52. So you split B into three 12-bit chunks and do just 3 matmuls instead of 25. The FP64 path is 1.65x faster despite lower peak throughput.

*Radix-16 NTT:* They also adopt a 10-step NTT that reduces complexity from O(N²) to O(N·16²) by decomposing the transform into smaller 16×16 matrix operations.

**Result:** 3.28× speedup over TensorFHE, with the BConv and IP transformations providing roughly 2.6-2.7× kernel speedups and NTT getting 3.74×.

---

Q2: The Key Insight

The central insight is recognizing that the poor performance of FHE on GPUs stems not from insufficient compute, but from a mismatch between the algorithm's data access patterns and the hardware's strengths—and that this mismatch can be resolved by reformulating element-wise operations as matrix multiplications.

Specifically, BConv and IP kernels perform repeated scalar/element-wise multiplications where the same data is fetched from global memory multiple times (α' times for BConv, β̃ times for IP). This is fundamentally wasteful on GPUs where global memory latency is the bottleneck. By recognizing that "multiply each element by a set of factors and accumulate" is mathematically equivalent to matrix multiplication, Neo transforms memory-bound element-wise operations into compute-bound matmuls that exploit data reuse inherent in the GEMM dataflow.

The secondary insight is that FP64 Tensor Cores, despite having 32× lower peak throughput than INT8 Tensor Cores, are actually faster for FHE's large-integer arithmetic because they avoid the Booth decomposition overhead. A 36-bit integer requires splitting into 5 INT8 parts (25 partial products), but fits in a single FP64 with room to spare for accumulation. This is a counterintuitive result that emerges from understanding both the cryptographic constraints (WordSize requirements) and the hardware microarchitecture.

The novelty lies not in any single technique but in the co-optimization: choosing the KLSS KeySwitch method (which introduces a tunable WordSize_T parameter), finding the optimal WordSize_T=48 that balances algorithmic complexity against Booth complexity, and mapping the transformed algorithms onto the appropriate Tensor Core datatype.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. *Comprehensive baseline comparison:* The paper compares against TensorFHE (prior TCU-based work), HEonGPU (recent non-TCU work), and CPU baselines across multiple parameter sets. This establishes the contribution clearly.

2. *Ablation study is well-structured:* Figure 14 incrementally applies each optimization (+KLSS, +dataflow, +Radix-16 NTT, +FP64 TCU), showing that each contributes meaningfully. This isolates the value of individual techniques.

3. *Memory transfer analysis grounds the motivation:* Figure 2 and Figure 15 quantify global memory transfer requirements before/after optimization, providing concrete evidence that the data reuse improvements are real and substantial.

4. *Parameter sensitivity analysis:* Table 8 explores the d_num × α̃ design space, and Figure 16 justifies the WordSize_T=48 choice empirically. This demonstrates engineering rigor.

5. *Real applications:* Testing on PackBootstrap, HELR, and ResNet-20/32/56 provides practical relevance beyond microbenchmarks.

**Weaknesses:**

1. *Comparison fairness concerns:* The comparison with TensorFHE requires reimplementation with Double Scaling (DS) "since the absence of DS in TensorFHE leads to precision loss." This raises questions: is the 3.28× speedup comparing equivalent functionality, or is Neo faster partly because DS changes the workload? The paper should clarify DS's performance impact.

2. *Limited HEonGPU comparison context:* Neo shows only 19.9% improvement over HEonGPU, which doesn't use Tensor Cores at all. For a paper whose main contribution is Tensor Core utilization, this modest gap is concerning. The paper doesn't adequately explain why the advantage isn't larger.

3. *BatchSize dependency is underexplored:* Figure 17 shows 2x performance variation from BS=8 to BS=128, but real applications may not always have 128 ciphertexts available. The paper defaults to BS=128 without discussing the practicality of this assumption.

4. *IP kernel mapping heuristic is ad-hoc:* The 80% valid proportion threshold for TCU vs. CUDA Core mapping is stated without justification. How sensitive is performance to this threshold? What happens at 75% or 85%?

5. *No power/energy analysis:* Tensor Cores have different power characteristics than CUDA cores. For datacenter deployment, energy efficiency matters as much as throughput.

6. *Missing comparison with ASIC baselines:* While the paper argues GPUs are more practical than ASICs, including even rough comparisons with Craterlake or SHARP would contextualize where GPU-based FHE stands in absolute terms.

7. *Weak claim about "first FP64 TCU usage":* The paper claims to be "the first engagement of floating-point components within TCUs" for FHE, but this contribution is architectural application rather than fundamental innovation.

---

Q4: What the Authors Didn't Tell You

**Implementation Complexity and Portability:**
The paper doesn't discuss how much engineering effort the data layout transformations require or how the approach ports to other GPU architectures. The FP64 TCU advantage is specific to A100's 8×8×4 fragment shape and the ratio between FP64 and INT8 throughput. On H100 or future architectures with different ratios, the optimal strategy may differ entirely.

**The KLSS Method Isn't Free:**
The paper adopts KLSS over Hybrid KeySwitch but glosses over KLSS's additional requirements: it needs Recover Limbs operations and has security constraints (Equation 4) that limit parameter choices. The 48-bit WordSize_T choice is presented as optimal, but this depends on the specific A100 hardware. The design space exploration in Table 8 only covers one parameter set.

**Precision and Correctness:**
Using FP64 to emulate integer arithmetic is risky. The paper claims "53 bits of precision" is sufficient for 36-bit × 12-bit × 16 accumulations, but doesn't discuss: (1) rounding behavior at the boundaries, (2) whether any test cases approach the precision limit, or (3) formal verification of correctness. FHE applications are precision-sensitive—noise accumulation is the fundamental limit.

**Memory Capacity Constraints:**
The evaluation keys in KLSS require β×β̃×α' polynomials—potentially gigabytes of data. The paper mentions "due to limitations of GPGPU memory capacity, BatchSize cannot be increased indefinitely" but doesn't quantify this constraint. For large L values, memory may become the bottleneck before compute.

**The Real Competition:**
The paper positions against TensorFHE and HEonGPU, but the serious competition for practical FHE is ASIC accelerators like Craterlake and BTS. The 3.28× GPU speedup is impressive, but ASICs claim 1000×+ over CPUs. The paper's argument for GPU practicality (cost, flexibility) is valid but understates the performance gap.

**Multi-GPU Scaling:**
The evaluation uses a single A100. Real datacenter deployments would use multiple GPUs. The paper doesn't discuss whether the optimizations introduce synchronization overhead or how they compose with multi-GPU parallelization strategies like HE-Booster's.

**The Preprocessing/Postprocessing Tax:**
Figure 13 shows preprocessing and postprocessing consume non-trivial time in the optimized kernels. As matrix multiplication gets faster, these become a larger fraction. The paper doesn't discuss whether these steps are fundamentally sequential or could be overlapped with computation.