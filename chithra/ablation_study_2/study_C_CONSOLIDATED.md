# Study C — Multi-Persona Synthesis
**Paper:** 3695053.3731408  
**Model:** claude-opus-4-5  
**Generated:** 2026-04-21 11:20

---

# Q1: Whiteboard Explanation

**The Problem Being Solved:**
Fully Homomorphic Encryption (FHE) enables computation on encrypted data without decryption—critical for privacy-preserving applications—but imposes catastrophic computational overhead (1000x-10000x slowdown). The CKKS scheme, particularly useful for machine learning on encrypted data, requires massive polynomial arithmetic with coefficients of 36-60 bits and polynomial degree N=65536.

**The Bottleneck:**
KeySwitch dominates runtime, triggered every multiplication or rotation to maintain decryptability. It comprises three expensive kernels:
1. **BConv (Base Conversion):** Converts polynomials between modulus representations
2. **NTT (Number Theoretic Transform):** FFT's modular arithmetic cousin for polynomial multiplication
3. **IP (Inner Product):** Multiply-accumulate with massive evaluation keys

**Prior Work's Limitation (TensorFHE):**
Used INT8 Tensor Core components (624 TFLOPS peak) but only accelerated NTT. For 36-bit integers, INT8 requires splitting into 5 chunks via Booth's algorithm, resulting in 5×5=25 partial matrix multiplications per operation.

**Neo's Core Mechanisms:**

*Mechanism 1 - Algorithm Transformation (Algorithms 1→2 and 3→4):*
BConv and IP appear as element-wise multiply-accumulate operations where each coefficient is read from global memory multiple times (α' times for BConv, β̃ times for IP). Neo reshapes these into matrix multiplications:
- BConv: Reshape tensor from α×BatchSize×N to N×BatchSize×α, multiply by α×α' conversion matrix
- IP: Reshape to (BatchSize×N)×β×β̃ for matrix multiplication with evaluation keys

This converts O(α' × N × BatchSize) memory accesses to O(N × BatchSize + N × α') through GEMM's inherent data reuse.

*Mechanism 2 - FP64 Tensor Cores over INT8:*
FP64's 53-bit mantissa can exactly represent integers up to 2^53. For 36-bit integers split into three 12-bit chunks:
- Only 3 matrix multiplications (vs. 25 for INT8)
- With K=16 accumulations: 36+12+4=52 bits < 53 bits (exact results, no rounding error)

Figure 3 shows empirically: FP64 is 1.65× faster than INT8 at WordSize=36, and 1.74× faster at WordSize=48.

*Mechanism 3 - KLSS KeySwitch Method:*
Allows computing in an "extended ring" R_T with tunable WordSize_T. Setting WordSize_T=48 (Figure 16's sweet spot) balances algorithmic complexity (limb count) against Booth decomposition overhead on TCUs.

**Data Flow (Figure 4):**
Each kernel maps to CUDA Cores (preprocessing/postprocessing) or FP64 TCUs (matrix multiplication). The Radix-16 NTT (from SHARP [25]) reduces matrix multiplication complexity from 2^25 to 2^22.

**Result:** 3.28× speedup over TensorFHE on application benchmarks.

---

# Q2: The Key Insight

**The Central Insight:**
Neo weaponizes a counterintuitive architectural observation: *FP64 Tensor Core components outperform INT8 components for FHE's specific bit-width requirements*, combined with *reformulating memory-bound element-wise operations as compute-bound matrix multiplications*.

**Why This is Non-Obvious:**
The raw numbers suggest INT8 should dominate: 624 TFLOPS (INT8) vs. 19.5 TFLOPS (FP64)—a 32× advantage. But FHE's large integer requirements destroy this:
- INT8 for 36-bit: 5 splits → 25 partial products
- FP64 for 36-bit: 3 splits → 9 partial products (actually 3 due to structure)

The fragment shape mismatch compounds this: INT8 uses 16×16×16 fragments, but BConv matrices are α×α' where α=4, α'=8. Figure 11 shows only 25% valid computation for INT8 vs. 100% for FP64's 8×8×4 fragments that better match the algorithm's natural dimensions.

**The Deeper Algorithmic Insight:**
Prior work missed that BConv and IP have *hidden matrix multiplication structure*. The original triple-nested loops (Algorithm 1) perform the same linear algebra as matrix multiplication but with terrible memory access patterns. By exposing the accumulation dimension as the K-dimension of GEMM, Neo achieves single-read data access with reuse throughout the matrix operation.

**Trade-off Surface (Section 3.5, Figure 16):**
WordSize_T=48 is optimal—not 36 (too many limbs in R_T increasing algorithmic complexity) and not 64 (excessive Booth complexity on TCU). This sweet spot emerges from co-optimizing algorithmic complexity (KLSS parameters) with hardware implementation complexity.

**What's NOT Novel:**
- KLSS method itself (Kim et al. [28], CRYPTO 2023)
- Radix-16 NTT (SHARP [25])
- Basic TCU usage for FHE (TensorFHE [12])

**The True Contribution:**
The *synthesis*: transforming BConv/IP into matrix form (new), selecting FP64 over INT8 for FHE's integer widths (new), and co-optimizing KLSS parameters for GPU implementation (new). Figure 14's ablation shows these contributions compound multiplicatively.

---

# Q3: Evaluation Critique

**Strengths:**

*1. Multi-Level Validation (Tables 5-7, Figures 13-14):*
The evaluation spans three granularities:
- Application level: PackBootstrap (0.24s vs 0.67s), ResNet-20 (12.03s vs 38.77s), HELR (0.22s vs 0.73s)
- Operation level: HMult, HRotate, PMult, etc. (Table 6)
- Kernel level: BConv 2.74×, IP 2.60×, NTT 3.74× (Table 7)

This hierarchical validation enables tracing application speedups to kernel improvements.

*2. Principled Ablation (Figure 14):*
Decomposes speedup into four components: +KLSS (~35-40% of improvement), +dataflow optimization (~15-20%), +Radix-16 NTT (~25%), +FP64 TCU (~10-15%). This honestly reveals that KLSS adoption contributes substantially before GPU-specific optimizations.

*3. Memory Transfer Quantification (Figures 2, 15):*
At l=35, BConv and IP constitute 43.4% and 41.8% of KeySwitch memory transfer under KLSS. Figure 15 shows concrete reductions: BConv transfer drops ~75%, IP drops ~80% post-optimization.

*4. Sensitivity Studies (Section 6.3, Table 8, Figure 16):*
Parameter sweeps for d_num (4-18), α̃ (4-10), WordSize_T (36/48/64), and BatchSize (8-128) demonstrate the optimization isn't cherry-picked.

**Weaknesses:**

*1. BatchSize Dependency (Figure 17):*
At BatchSize=8, performance degrades ~2× versus BatchSize=128. Many real applications process single ciphertexts or small batches for latency reasons. Single-ciphertext latency is never reported.

*2. Baseline Fairness Questions:*
- TensorFHE was reimplemented with DS integration (Table 5 footnote), not the original implementation
- HEonGPU comparison uses different parameter sets (Set-E: WordSize=60 vs. Set-C: WordSize=36)—when matched at WordSize=60, the gap shrinks to ~18%, not 27%
- CPU baseline (Table 6) comes from a different paper [22] with different parameters (Set-H)

*3. Missing Power/Energy Analysis:*
No power measurements despite datacenter deployment claims. A100 TDP is 400W. If Neo achieves 3.28× speedup at higher power draw, energy efficiency gains may be smaller.

*4. Single Hardware Platform:*
All experiments on NVIDIA A100-40GB only. No H100 (different FP64:INT8 ratios), no multi-GPU scaling, no AMD comparison. The FP64 advantage may not transfer to other architectures.

*5. Arbitrary 80% Threshold (Section 4.5.3):*
IP maps to TCUs when "valid proportion" exceeds 80%; otherwise CUDA Cores. No justification provided. Figure 12 shows IP drops below 80% around l=20 and to ~25% at l=5—for computations at low levels, IP runs on CUDA Cores.

*6. No Numerical Accuracy Analysis:*
CKKS is an approximate scheme. FP64 intermediate computations could introduce precision differences versus exact integer arithmetic. Correctness is claimed but not quantified.

*7. Missing ASIC Comparison:*
They cite Craterlake, SHARP, Taiyi but never quantify the performance gap. The implicit "GPUs are more practical" argument lacks numbers.

---

# Q4: What the Authors Didn't Tell You

**Hidden Costs and Overhead:**

*1. Preprocessing/Postprocessing is Non-Trivial:*
Figure 13 shows preprocessing for BConv(new) takes ~0.3μs out of ~0.9μs total (~33% overhead). For IP(new), preprocessing is almost as long as matrix multiplication. The paper calls this "negligible" because absolute times are small, but these operations become the new bottleneck if future work further accelerates matrix multiplication.

*2. KLSS Contributes More Than Their Novel Optimizations:*
Figure 14 reveals that adopting KLSS (prior work from [28]) provides 35-40% of total improvement before any Neo-specific GPU optimizations. The paper's Tensor Core framing oversells the TCU contribution relative to algorithmic changes.

*3. FP64 Advantage is Architecture-Specific:*
The 48-bit WordSize_T sweet spot is tuned for A100's specific FP64 TCU characteristics. On H100, FP64 delivers ~67 TFLOPS vs. ~1979 TOPS for INT8 (~30x ratio vs. A100's ~32x). The optimal parameters and even the FP64 vs. INT8 decision may flip on different hardware.

*4. IP Falls Back to CUDA Cores at Low Levels:*
Figure 12 shows IP's "valid proportion" drops below 80% for l<20 and to ~25% at l=5. For applications with many rescale operations that spend time at low levels, the flagship TCU optimization doesn't apply to a major kernel.

*5. Evaluation Key Memory Explosion:*
IP requires β̃×β×α' polynomial keys (Section 2.3). With β=4, β̃=6, α'=8, that's 192 polynomial keys per KeySwitch. Each polynomial at N=65536 with 8-byte coefficients is ~500KB. Thousands of such polynomials create multi-GB memory footprints. The paper claims reduced memory transfer but doesn't quantify absolute evaluation key storage.

*6. Shared Memory Pressure:*
Kernel fusion stores intermediate results in shared memory (164KB per SM on A100). For BatchSize=128, N=65536, FP64 values, a single tile of α=4 polynomials can consume 64KB+ per fragment. Occupancy impacts and potential spilling are not discussed.

*7. The 3.28× Headline vs. Closer Competition:*
Against TensorFHE (2023-era work), Neo achieves 3.28×. Against HEonGPU (2024), which doesn't use TCUs at all, the advantage drops to 19.9% average (Table 5)—and only ~18% when comparing at equivalent WordSize. The framing emphasizes the larger number.

*8. Security Parameter Concerns:*
Set-H in Table 4 has λ≥98, below the standard 128-bit threshold. This is used for CPU comparison but noted only briefly. Additionally, the paper never discusses side-channel attack implications for cryptographic implementations on GPUs.

*9. Radix-16 NTT is Borrowed:*
The complexity reduction from 2^25 to 2^22 (8× reduction) comes from SHARP [25], an ASIC paper. Neo's contribution is porting it to GPU, not designing it.

*10. Multi-GPU and Reproducibility Gaps:*
No multi-GPU scaling despite datacenter deployment claims. No code release or artifact availability. HE-Booster [45] addressed multi-GPU but isn't compared against.

**Bottom Line for Future Work:**
Performance gains are highly parameter-dependent (BatchSize, level l, WordSize_T). The FP64 vs. INT8 advantage may not transfer to future GPUs. For low-batch, low-level operations typical of interactive applications, expect significantly smaller speedups than headline numbers.