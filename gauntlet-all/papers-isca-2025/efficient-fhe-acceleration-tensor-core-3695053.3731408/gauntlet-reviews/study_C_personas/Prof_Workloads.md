## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Neo is about accelerating Fully Homomorphic Encryption (FHE) on GPUs by creatively exploiting Tensor Cores.

**The Problem Space:**
FHE lets you compute on encrypted data without decryption—magical for privacy, but computationally brutal. The CKKS scheme (for approximate arithmetic) is dominated by one operation: **KeySwitch**, which consumes most of the runtime. KeySwitch breaks down into three expensive kernels: **BConv** (Base Conversion), **NTT** (Number Theoretic Transform), and **IP** (Inner Product).

**The Core Insight:**
Previous GPU work (TensorFHE) used INT8 Tensor Core components to accelerate NTT via matrix multiplication. But here's the thing—when you need 36-bit precision (which SHARP [25] showed is necessary for correctness), splitting into INT8 chunks creates 25 partial matrix multiplications. That's a lot of overhead!

Neo makes two key observations:
1. **FP64 components exist in Tensor Cores and are underutilized.** For 36-bit integers, FP64 (with 53 bits of mantissa) only needs 3 partial multiplications instead of 25. Figure 3 shows FP64 is 1.65× faster for 36-bit and 1.74× faster for 48-bit computations.

2. **BConv and IP have terrible data reuse.** Figure 2 shows these kernels dominate memory transfer (43.4% and 41.8% at l=35). The original algorithms repeatedly fetch the same coefficients because they're doing element-wise operations.

**The Solution:**
- **Transform BConv and IP into matrix multiplications** (Algorithms 2 and 4). Instead of repeated scalar multiplications, you reorder data so coefficients align for batched matrix-matrix operations. This converts memory-bound element-wise ops into compute-bound GEMM.
- **Use FP64 Tensor Core components** for all matrix multiplications—NTT, BConv, and IP—avoiding the INT8 splitting overhead.
- **Adopt KLSS KeySwitch method** instead of Hybrid, which lets you choose a configurable WordSize_T (they pick 48 bits) that balances algorithmic complexity reduction against hardware implementation complexity.

The result: 3.28× speedup over TensorFHE across real FHE applications.

---

## Q2: The Key Insight

The key insight is a **mismatch exploitation**: prior work assumed INT8 Tensor Cores were optimal for FHE because of their raw TFLOPS advantage (624 TFLOPS INT8 vs. 19.5 TFLOPS FP64 on A100). But this ignores the **Booth decomposition overhead** required for large-integer arithmetic.

Specifically, the paper reveals that **the total cost of integer matrix multiplication includes splitting, multiple partial GEMMs, and merging—not just the GEMM itself**. For 36-bit operands:
- INT8 requires splitting each operand into 5 chunks → 25 partial matrix multiplications
- FP64 requires splitting into just 2 chunks → 3-4 partial multiplications

Figure 3 (Section 3.4) is the smoking gun: despite INT8's theoretical throughput advantage, **FP64 achieves 1.65× faster end-to-end performance for 36-bit matrix multiplication** because the split/merge overhead dominates.

The second insight is that **BConv and IP can be reformulated as matrix multiplications**, not just NTT. This wasn't obvious because their natural formulation is element-wise with scalar coefficients. The authors show that by reordering data layouts (Figures 6-8), you can batch these operations into GEMMs, enabling both better data reuse AND Tensor Core acceleration.

This is fundamentally an **algorithm-architecture co-design insight**: the "better" algorithm depends on what hardware can actually execute efficiently, not just theoretical operation counts.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Kernel-to-Application Coverage:**
The evaluation spans multiple granularities—kernel performance (Table 7), operation performance (Table 6), and full application performance (Table 5). This layered approach lets readers understand where speedups originate. Table 7 showing 3.74× on NTT, 2.74× on BConv, and 2.60× on IP directly supports the claimed optimizations.

**2. Honest Incremental Contribution Analysis:**
Figure 14 decomposes the 3.28× speedup into four optimization steps: +KLSS, +dataflow optimization, +Radix-16 NTT, +FP64 TCU. This transparency is commendable—you can see that KLSS alone provides substantial benefit, and FP64 TCU adds the final ~20-30%. This prevents the "black box speedup" problem.

**3. Sensitivity Studies on Key Parameters:**
Table 8 explores the (d_num, α̃) parameter space systematically, showing optimal parameters aren't arbitrary. Figure 16 validates the WordSize_T = 48 choice by comparing against 36 and 64. Figure 17 examines BatchSize effects. These studies address obvious follow-up questions.

**4. Memory Transfer Analysis:**
Figure 2 and Figure 15 provide concrete memory transfer measurements, not just runtime. Figure 15(b) shows application-level memory requirements (e.g., 9.89TB for ResNet-20) and demonstrates reductions from dataflow optimization. This grounds the "data reuse" claims in measurable reality.

**5. Fair Baseline Comparison:**
They compare against TensorFHE (the prior GPU SOTA) AND HEonGPU (a non-TCU GPU approach). Table 5 shows they reimplemented TensorFHE with DS (Double Scaling) integration since "absence of DS in TensorFHE leads to precision loss[25]." This is good practice—comparing against a corrected baseline rather than a broken one.

### Weaknesses

**1. The "Valid Proportion" Threshold is Suspiciously Convenient:**
Section 4.5.3 states: "experimentally, the performance on TCUs surpasses that of the CUDA Core only when the valid proportion of matrix multiplications exceeds 80%." This 80% threshold determines whether IP uses TCU or CUDA cores. But where does 80% come from? Figure 12 shows IP's valid proportion varies wildly with level l. The paper doesn't justify this threshold or explore sensitivity to it. Is this cherry-picked to make their approach work?

**2. Limited Workload Diversity:**
All three benchmark applications (PackBootstrap, HELR, ResNet-20/32/56) are dominated by KeySwitch operations. Table 6 shows PMULT, HADD, PADD, and Rescale have essentially identical performance to TensorFHE (82.3μs vs 81.7μs for PMULT). If an application had different operation mixes—say, many PADDs and few HMULTs—Neo's advantage would evaporate. The paper doesn't characterize when Neo helps vs. when it doesn't.

**3. Single GPU Architecture:**
All experiments use NVIDIA A100 (Table 3). The FP64:INT8 Tensor Core throughput ratio varies across GPU generations. On H100, for example, FP64 TC throughput is 67 TFLOPS while INT8 is 3958 TFLOPS—a much larger gap. Would Neo's design decisions still hold? The "generality" claim in Section 3.1 is undermined by single-architecture evaluation.

**4. BatchSize = 128 Assumption:**
Table 4 shows all parameter sets use BatchSize = 128 (except Set-E/H without batching). Figure 17 shows performance degrades significantly at smaller batch sizes—nearly 2× worse at BS=8. Real FHE deployments might not always have 128 ciphertexts ready for batching. The paper doesn't discuss latency for single-ciphertext operations.

**5. Missing Energy/Power Analysis:**
The paper claims GPGPUs are "cost-effective" (Section 3.1, Section 8) but provides no power consumption, energy efficiency, or cost-per-operation metrics. Tensor Cores may be fast but power-hungry. For cloud deployment scenarios they motivate in Section 1, $/operation or J/operation matters.

**6. The HEonGPU Comparison is Incomplete:**
Table 5 shows Neo beats HEonGPU by ~20% on average, but HEonGPU uses different parameters (Set-E with WordSize=60, dnum=36). Are these fair parameters? The paper doesn't explain why HEonGPU uses different settings or whether Neo could run with Set-E parameters.

**7. Figure 3's Experimental Setup is Underspecified:**
Figure 3 compares INT8 vs FP64 for matrix multiplication with "M×N×K parameters corresponding to 2^19×16×16." But this is a specific shape favorable to FP64's 8×8×4 fragment size. What about other shapes? The claim that FP64 is universally better for >36-bit needs more shape diversity.

---

## Q4: What the Authors Didn't Tell You

**1. The Precision-Performance Tradeoff is Unquantified:**
The paper claims FP64 Tensor Cores provide sufficient precision for 36-bit integers because "FP64 format offers 53 bits of precision" (Section 3.4). But FHE error analysis is subtle. When you accumulate K=16 products of 36×12 bit multiplications, intermediate results approach 2^52. The paper never validates numerical correctness against a reference implementation. Do accumulated floating-point rounding errors affect FHE decryption accuracy? CKKS is approximate anyway—but how much additional approximation does FP64 introduce?

**2. The KLSS Method Increases Evaluation Key Storage:**
Section 2.2 mentions IP requires "two sets of β̃×β×α' polynomial keys" for KLSS versus smaller keys for Hybrid. Table 2 shows IP complexity increases from 2β(l+α) to β̃βα' for KLSS. But what does this mean for memory footprint? The paper discusses memory transfer (Figure 2) but not total storage requirements. With N=2^16 and large key counts, evaluation keys could exceed GPU memory for some parameter choices.

**3. The "First Implementation" Claims Need Qualification:**
The paper claims "the first implementation of [BConv/IP] acceleration through TCU" (Section 1). But the transformation of element-wise operations to matrix multiplication is orthogonal to TCU usage—you could do this optimization on CUDA cores alone. The novelty is specifically FP64 TCU utilization, not the algorithmic transformation itself. Algorithm 2's matrix form of BConv may have appeared in prior ASIC work (e.g., Taiyi [11] "concentrating on optimizations at the architectural level").

**4. Multi-GPU Scaling is Absent:**
Section 7 mentions HE-Booster[45] proposed "multi-GPGPU parallelization." Neo's evaluation is entirely single-GPU. For real datacenter FHE services processing many independent client requests, multi-GPU scaling matters. Do the data layout transformations (Section 4.3) impede multi-GPU parallelization?

**5. The "Real-World Workload" Story is Incomplete:**
Section 1 motivates FHE with "personal financial transactions and health records" and "privacy data protection." But the evaluated applications are:
- PackBootstrap: A cryptographic primitive, not an application
- HELR: Binary classification on MNIST (14×14 images distinguishing 3 vs 8)
- ResNet-20: CNN inference on CIFAR-10

None of these are actual financial or health applications. What operations dominate in real encrypted database queries or privacy-preserving analytics? The paper assumes KeySwitch-dominated workloads are representative but doesn't validate this.

**6. Kernel Fusion Boundary Choices are Unexplained:**
Section 4.6 mentions "kernel fusion" combining split/reorder, matrix multiplication, and merge/reorder into single kernels. But the decision boundaries—which operations to fuse—aren't justified. Why not fuse NTT with subsequent BConv? The paper doesn't discuss fusion exploration or why their choices are optimal.

**7. The Comparison Against CPU is Misleading:**
Table 5 and Table 6 include CPU baseline numbers from Craterlake[40] and 100x[22]. But these are from different papers with potentially different CPU architectures and optimization levels. A 17.2s CPU vs 0.24s Neo PackBootstrap (71×) sounds impressive, but without specifying the CPU (is it single-threaded? What instruction set?) this comparison is meaningless for deployment decisions.

**8. Error Bars and Variance are Missing:**
No figure or table includes error bars, confidence intervals, or standard deviations. FHE performance can vary with parameter-dependent branching (e.g., the IP TCU vs CUDA decision at 80% threshold). How stable are these measurements across runs?