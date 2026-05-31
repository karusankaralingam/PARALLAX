# Evaluation Methodology Critique: Neo (ISCA '25)

## Q1: Whiteboard Explanation

Let me draw this out for you. FHE (Fully Homomorphic Encryption) lets you compute on encrypted data, but it's computationally brutal. The CKKS scheme is the flavor being accelerated here.

**The Problem:** KeySwitch is the bottleneck operation in FHE. It involves three expensive kernels:
1. **BConv** (Base Conversion) - converts polynomials between different modulus spaces
2. **NTT** (Number Theoretic Transform) - the polynomial multiplication workhorse
3. **IP** (Inner Product) - multiply-accumulate with evaluation keys

**Prior Work's Limitation:** TensorFHE tried using INT8 Tensor Cores for NTT, but:
- Only accelerated NTT, ignored BConv and IP
- INT8 requires splitting 36-bit integers into 5 parts → 25 partial multiplications (Booth complexity explosion)
- Poor data reuse in BConv/IP (element-wise operations)

**Neo's Core Trick:**
1. Transform BConv and IP from element-wise multiplications into matrix multiplications (Algorithm 2 and 4)
2. Use FP64 Tensor Cores instead of INT8 → only 3 partial multiplications for 36-bit integers (Figure 3)
3. Adopt KLSS KeySwitch method over Hybrid method to reduce algorithmic complexity
4. Use Radix-16 NTT to reduce NTT complexity from 2^25 to 2^22

**The Data Layout Magic:** Rearrange polynomial coefficients from α×BatchSize×N to N×BatchSize×α so the α dimension becomes K in GEMM. This enables data reuse during matrix multiplication instead of repeated global memory accesses.

**Result:** 3.28× speedup over TensorFHE on A100 GPU.

---

## Q2: The Key Insight

The fundamental insight is **architectural-algorithmic co-optimization for TCU utilization efficiency**.

Previous work (TensorFHE) made the obvious choice: INT8 Tensor Cores have 624 TFLOPS peak vs. 19.5 TFLOPS for FP64. But this ignores the **Booth complexity** of integer decomposition. Figure 3 is the smoking gun: at WordSize=36, FP64 is 1.65× faster than INT8; at WordSize=48, it's 1.74× faster.

The deeper insight is that **FHE's dominant kernels (BConv, IP) have hidden matrix multiplication structure** that prior work missed. Section 4.2 shows that element-wise multiply-accumulate over limbs can be reshaped into GEMM by clever tensor rearrangement. This simultaneously:
- Improves data reuse (Figure 15 shows >50% reduction in global memory transfer for BConv/IP)
- Enables TCU acceleration for kernels that were previously CUDA-Core-only

The authors also identify a **sweet spot trade-off** in Section 6.3 and Figure 16: WordSize_T = 48 outperforms both 36 (too high algorithmic complexity via large α') and 64 (too high Booth complexity in NTT).

This is genuinely novel: it's not just "use Tensor Cores" but "reshape algorithms to match what Tensor Cores are actually good at."

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Baseline Comparisons**
Table 5 and Table 6 compare against TensorFHE (the state-of-the-art GPU work) and HEonGPU under multiple parameter configurations. They don't just pick the configuration where they win most—Set-A, Set-B, Set-C for TensorFHE are all shown. The 3.28× claim is against TensorFHE's *best* configuration, not a strawman.

**2. Multi-Level Validation**
They validate at three granularities:
- Application level (Table 5): PackBootstrap, HELR, ResNet-20/32/56
- Operation level (Table 6): HMult, HRotate, PMult, etc.
- Kernel level (Table 7): BConv, IP, NTT throughput
This makes it hard to hide performance issues in specific components.

**3. Incremental Ablation Study**
Figure 14 shows performance gains from each optimization step (+KLSS, +dataflow opted, +Radix-16 NTT, +FP64 TCU). This is proper methodology—you can see each contribution independently. The gains stack multiplicatively and none of them individually dominates.

**4. Sensitivity Analysis with Honest Trade-offs**
Table 8 and Figure 16 show parameter sweeps for d_num, α̃, and WordSize_T. They acknowledge that WordSize_T=64 is *worse* than 48 due to Booth overhead. Figure 12 shows the "valid proportion" dropping for IP at low levels—honest disclosure of when their mapping works less well.

### Weaknesses

**1. The Cherry-Pick Check: Benchmark Selection**
The application benchmarks are limited to:
- PackBootstrap (fundamental operation)
- HELR (logistic regression on MNIST 3 vs 8)
- ResNet-20/32/56 (CNN inference)

**Missing:** Any workloads with irregular access patterns, sparse operations, or varying polynomial degrees. All benchmarks use N=2^16 (Section 5, Table 4). Real-world FHE applications might use different N values. The paper doesn't explore sensitivity to N at all.

**2. Baseline Validity Concerns**
- **TensorFHE Reimplementation:** Table 5 footnote ‡ states "We reimplemented TensorFHE with DS integrated since the absence of DS in TensorFHE leads to precision loss." This raises questions—are they comparing against their own modified version or the original? How much did their modifications affect TensorFHE's performance?
- **HEonGPU Comparison:** Only appears in Tables 5 and 6 with Set-E parameters. They show 19.9% average improvement, but HEonGPU uses different parameters (WordSize=60 vs. Neo's 36 for Set-C). This isn't an apples-to-apples comparison.

**3. The BatchSize Dependency Problem**
Figure 17 shows that reducing BatchSize from 128 to 8 approximately doubles execution time. But real-world latency-sensitive applications can't always batch 128 ciphertexts. The paper doesn't evaluate single-ciphertext latency, which matters for interactive applications.

**4. Memory Transfer Accounting**
Figure 15 shows reduced memory transfer requirements, but no actual measured bandwidth utilization is presented. They compute theoretical requirements (9.43GB at l=35) but don't show whether the GPU's memory subsystem is actually the bottleneck or what utilization they achieve.

**5. No Power or Energy Efficiency Comparison**
For a GPGPU paper, the absence of power measurements is notable. TCU efficiency claims (Section 3.4) are based solely on throughput, not FLOPS/watt.

**6. Limited Hardware Generalization**
All experiments are on A100 only. The FP64 Tensor Core capability is relatively unique to A100/H100 (Hopper). The technique may not generalize to consumer GPUs or older datacenter GPUs without FP64 TCU support.

**7. Figure 2 Y-Axis Presentation**
Figure 2 uses normalized memory transfer with raw values in parentheses. The "lower bar" vs "upper bar" representation makes visual comparison awkward—absolute numbers would be clearer for assessing actual impact.

---

## Q4: What the Authors Didn't Tell You

**1. The KLSS Method Isn't Theirs**
Section 2.2 describes KLSS [28] as "advanced KeySwitch method" but the algorithmic contribution of adopting KLSS is essentially implementation, not invention. The complexity reduction (Table 2) comes from prior work; Neo's contribution is making it work on GPU.

**2. The "Unused FP64 Components" Narrative is Misleading**
Section 1 claims prior work left FP64 TCU components "unused." But TensorFHE (2023) chose INT8 deliberately for higher peak TFLOPS. The comparison in Figure 3 showing FP64 winning is only valid *for the specific integer widths in FHE*. For actual low-precision workloads, INT8 is still correct. The paper frames this as prior work's oversight when it's actually a workload-specific insight.

**3. The Valid Proportion Threshold is Empirical, Not Principled**
Section 4.5.3 states: "experimentally, the performance on TCUs surpasses that of the CUDA Core only when the valid proportion of matrix multiplications exceeds 80%." This 80% threshold appears without justification. How was it determined? Does it vary with problem size?

**4. Double Rescale (DS) Requirements**
Section 2.1 notes DS is "essential when WordSize is smaller than 36 bits" and "consumes two ciphertext levels." This affects multiplicative depth budgets significantly, but the paper doesn't discuss how this constraint interacts with practical application requirements.

**5. Evaluation Key Memory Footprint**
Section 2.3 mentions IP requires "two sets of ββ̃α' polynomial keys, which significantly impact overall performance." Section 6 uses BatchSize=128 by default. But what's the actual memory consumption? For Set-C with L=35, evaluation keys alone could be tens of GB. The 40GB A100 memory bound isn't discussed as a potential limitation.

**6. The WordSize_T=48 Sweet Spot May Be Hardware-Specific**
Figure 16 shows 48-bit is optimal. But this depends on A100's FP64 TCU characteristics. On H100 with different ratios, or on AMD MI300 with different architectures, this sweet spot would shift. The paper presents it as a general finding.

**7. No Numerical Accuracy Analysis**
FP64 TCU operations on integers introduce floating-point rounding concerns. Section 3.4 claims "53 bits of precision" is "sufficient to represent integers up to 2^53 without loss of precision." But the accumulation in GEMM (K dimension up to 16) could introduce errors. They mention results stay under 2^52 < 2^53, but don't empirically validate no precision loss in outputs.

**8. Multi-GPU Scaling Absent**
Section 3.1 mentions "GPGPUs are already widely deployed in data centers" but only single-GPU results are shown. HE-Booster (their reference [45]) addressed multi-GPU scaling. For datacenter deployment claims, this is a significant gap.

**9. The Radix-16 NTT Adoption**
Section 4.4 says "We have employed a Radix-16 NTT method from SHARP[25]." SHARP is an ASIC paper. The contribution here is porting it to GPU, not designing it. The complexity reduction from 2^25 to 2^22 (8× reduction) is presented as their optimization but it's borrowed.

**10. Comparison Metrics Inconsistency**
Table 5 uses "seconds" for applications, Table 6 uses "microseconds" for operations. The speedup claims mix these levels: "3.28× over TensorFHE" is application-level, but kernel-level (Table 7) shows varying speedups (2.74×–3.74×). The headline number is chosen from where they look best.