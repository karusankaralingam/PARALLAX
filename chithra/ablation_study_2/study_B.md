# Study B — Rich Directive
**Paper:** 3695053.3731408  
**Model:** claude-opus-4-5  
**Generated:** 2026-04-21 11:20

---

# Comprehension Report: Neo: Towards Efficient Fully Homomorphic Encryption Acceleration using Tensor Core

## Q1: Whiteboard Explanation

Let me explain Neo as if presenting to a colleague at a whiteboard.

**The Problem Setup:**
FHE allows computation on encrypted data, but it's painfully slow. The CKKS scheme encrypts N/2 complex numbers into polynomial ciphertexts, and operations like multiplication require KeySwitch operations that dominate execution time. KeySwitch has three critical kernels: BConv (base conversion), NTT (number-theoretic transform), and IP (inner product with evaluation keys).

**Why Existing GPU Solutions Fall Short:**

*Drawing the memory access pattern:*
```
Original BConv: For each output level j (α' total):
  For each input level i (α total):
    Read coefficient, multiply by conversion factor, accumulate
→ Each coefficient read α' times from global memory!

Original IP: Similar problem - coefficients read β̃ times
```

Previous work (TensorFHE) used INT8 tensor cores for NTT only. The problem: when WordSize increases from 32 to 36 bits (required for precision), you need 64-bit storage, halving throughput.

**Neo's Key Transformations:**

*Drawing the algorithm restructuring:*
```
BConv Transformation:
Original: element-wise multiply + accumulate (poor reuse)
        ↓
Neo: Reshape tensor from [α × BatchSize × N] to [N × BatchSize × α]
     Then: Matrix multiply [BatchSize × α] × [α × α'] = [BatchSize × α']
     → Each coefficient read ONCE, reused across all α' outputs!
```

*Drawing the TCU utilization insight:*
```
TCU Components:
┌─────────────────────┬──────────────────────┐
│ INT8 (624 TFLOPS)   │ FP64 (19.5 TFLOPS)   │
│ Fragment: 16×16×16  │ Fragment: 8×8×4      │
└─────────────────────┴──────────────────────┘

For 36-bit integers:
- INT8: Split into 5 parts → 25 partial products + complex merging
- FP64: Split into 3 parts → 3 matrix multiplications (53-bit mantissa covers 36-bit)

FP64 wins despite lower peak TFLOPS because:
(a) Fewer partial products
(b) Smaller fragment fits irregular dimensions (α=4, α'=8) without padding
```

**The KLSS Method Trade-off:**

*Drawing the parameter space:*
```
KLSS maps to ring R_T with selective WordSize_T
- Larger WordSize_T → Lower algorithm complexity (smaller α')
- BUT larger WordSize_T → More Booth complexity in TCU
- Sweet spot: WordSize_T = 48 bits
```

**End-to-End Pipeline:**
The complete system: KLSS KeySwitch algorithm → Radix-16 NTT (reduces complexity 8×) → Matrix-form BConv/IP → FP64 tensor cores for all matrix multiplications → CUDA cores for element-wise ops.

Result: 3.28× speedup over TensorFHE on real workloads.

---

## Q2: The Key Insight

The central insight of Neo is that **the memory access pattern inefficiency in FHE kernels can be eliminated by reformulating element-wise multiply-accumulate operations as matrix multiplications, which then enables exploitation of the FP64 tensor core components that previous work left completely unused**.

This is actually a two-part insight that builds synergistically:

**Part 1 - Algorithmic Restructuring:** BConv and IP fundamentally perform the same computation pattern: multiply a coefficient by multiple factors and accumulate. The naive implementation treats this as independent scalar operations, causing each coefficient to be fetched from global memory O(output_levels) times. By reorganizing data layouts and expressing this as matrix multiplication [BatchSize × input_dims] × [input_dims × output_dims], each coefficient is read exactly once and reused across all output computations. This is not a new algorithmic complexity reduction—it's a data reuse optimization enabled by changing the computation structure.

**Part 2 - Hardware Mapping Insight:** Previous work (TensorFHE) only used INT8 tensor cores for NTT and struggled when WordSize exceeded 32 bits. The paper's crucial observation is that for high-bit-width FHE computations, FP64 tensor cores are actually more efficient despite having ~32× lower peak throughput than INT8. The reason is concrete and quantitative: computing a 36-bit integer matrix multiplication requires splitting into 5 INT8 components (25 partial products with complex carry propagation), versus 3 FP64 operations (the 53-bit mantissa directly represents integers up to 2^53). Furthermore, the smaller FP64 fragment shape (8×8×4 vs 16×16×16) matches the irregular dimensions of BConv/IP matrices (α=4, α'=8) without wasting computation on padding.

**Why this differs from prior approaches:** TensorFHE viewed tensor cores as fixed-function INT8 matrix engines and only accelerated NTT. Neo recognizes that (1) more kernels can be expressed as matrix multiplication with proper restructuring, and (2) the FP64 datapath in tensor cores—designed for scientific computing—happens to be a better fit for FHE's computational requirements.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Comparison Methodology:**
The paper compares against multiple baselines (TensorFHE with three parameter sets, HEonGPU) across the same workloads, and critically, **reimplements TensorFHE with Double Rescale** since the original lacked it and would produce incorrect results for 36-bit WordSize. This is methodologically honest.

**2. Multi-Level Breakdown:**
The evaluation provides performance data at application level (PackBootstrap, HELR, ResNet), operation level (HMult, HRotate), and kernel level (BConv, IP, NTT). This allows readers to understand where speedups originate. Table 7 shows NTT achieves 3.74× speedup, which makes sense since Radix-16 reduces complexity 8× at the algorithmic level.

**3. Ablation Study Quality (Figure 14):**
The incremental optimization breakdown (+KLSS → +dataflow → +Radix-16 NTT → +FP64 TCU) clearly attributes performance gains to each contribution. For ResNet-20, the final configuration achieves ~0.29× of TensorFHE's execution time, matching the claimed 3.4× speedup.

**4. Memory Transfer Analysis (Figure 15):**
Quantifying global memory transfer reduction directly validates the data reuse claim. For KeySwitch at l=35, BConv+IP data transfer drops from ~85% to ~40% of total—roughly 2× reduction, consistent with the algorithmic restructuring eliminating redundant reads.

### Weaknesses

**1. Single GPU Evaluation:**
All experiments run on A100-40GB only. The technique relies on FP64 tensor core availability and specific fragment shapes. Would this approach work on H100 (different tensor core architecture)? What about A30 (no FP64 tensor cores)? The generalization claims are unsupported.

**2. Incomplete Sensitivity Analysis:**
Table 8 shows sensitivity to d_num and α̃, but the parameter sweep is coarse and the table reveals the "optimal" choice (d_num=9, α̃=5) performs **worse** than some alternatives (d_num=7, α̃=6 achieves 3.30 vs 3.22). This suggests the parameter space isn't well-understood or the optimum is configuration-dependent.

**3. Memory Capacity Glossed Over:**
BatchSize=128 is chosen because "GPGPU memory capacity" limits it, but no analysis of memory footprint vs. batch size tradeoffs is provided. The evaluation keys in KLSS require β̃×β×α' polynomial storage—for large parameters this could be several GB. The A100's 40GB is generous; real deployment scenarios may have tighter constraints.

**4. Comparison Against HEonGPU is Thin:**
HEonGPU is included but uses different parameters (Set-E, dnum=36). The 19.9% average improvement over HEonGPU is modest, and we don't know if HEonGPU could achieve better results with Neo's parameters, or vice versa.

**5. No Power or Energy Analysis:**
FHE is compute-intensive; understanding energy efficiency (J/bootstrap) would be valuable for data center deployment arguments made in Section 3.1.

**6. The "Valid Proportion" Threshold (80%) is Arbitrary:**
Section 4.5.3 states IP maps to tensor cores when valid computation exceeds 80%, otherwise CUDA cores. No justification for this threshold—is it empirically derived? Does it vary with parameters?

**7. Precision Validation Missing:**
The paper claims correctness for 36-bit WordSize with Double Rescale but provides no numerical accuracy results. FHE schemes accumulate noise; showing maintained decryption accuracy after bootstrapping would strengthen claims.

---

## Q4: What the Authors Didn't Tell You

### Hidden Assumptions and Limitations

**1. The KLSS Method is Not Free:**
The paper adopts KLSS as the KeySwitch algorithm and tunes WordSize_T to 48 bits. However, KLSS has constraints (Equation 4) that couple security parameters to algorithmic complexity. The paper doesn't discuss how choosing WordSize_T=48 affects the security-performance tradeoff or whether certain parameter regimes make KLSS infeasible.

**2. Evaluation Keys are Preloaded:**
The evaluation assumes evk (evaluation keys) fit in GPU memory and are resident. Table 2 shows IP in KLSS requires β̃×β×α' polynomial keys. For the default parameters (β=4, β̃=5, α'=8, N=65536, 64-bit storage), this is approximately 4×5×8×65536×8 = 80MB per KeySwitch instance. With batching and multiple key sets for different rotation indices, memory pressure could be severe. The BatchSize=128 limitation is likely hitting this constraint.

**3. Data Layout Transformation Overhead:**
The preprocessing (reorder) and postprocessing steps in BConv and IP are mapped to CUDA cores. Figure 13 shows these consume ~25-30% of the optimized kernel time. The paper frames this as "negligible" but 25-30% overhead is meaningful. More critically, these data movements are now explicit memory transactions that weren't counted in the "memory transfer reduction" metrics.

**4. Radix-16 NTT is Independent Work:**
The Radix-16 NTT is cited from SHARP [25], which is an ASIC paper. Neo's contribution is implementing it on GPU with tensor cores, but the algorithmic complexity reduction (8× fewer matrix operations) is not Neo's innovation. This is disclosed but easy to miss.

**5. The FP64 Advantage is Situation-Dependent:**
Figure 3 shows FP64 winning at WordSize=36 (1.65×) and WordSize=48 (1.74×). But the crossover point isn't characterized—at what bit-width does INT8 become favorable again? For schemes with smaller moduli (like BGV with 30-bit primes), INT8 might win. The paper's focus on CKKS with large WordSize is a favorable scenario.

**6. Multi-GPU Scaling Unexplored:**
Section 7 mentions HE-Booster [45] for multi-GPU, but Neo provides no multi-GPU results. For production FHE services, scaling across devices is essential.

**7. Kernel Fusion Strategy is Incomplete:**
Section 4.6 mentions kernel fusion but provides no details on which kernels are fused. The KeySwitch dataflow (Figure 5) shows 6 kernel types with data dependencies. Suboptimal fusion could leave performance on the table.

**8. Comparison Baseline Selection:**
TensorFHE (HPCA 2023) and HEonGPU are the GPU comparisons. Other recent GPU work like cuFHE or GPU-accelerated OpenFHE aren't evaluated. The CPU baseline (17.2s for PackBootstrap) is from Craterlake [40], a 2022 paper—modern CPU implementations with AVX-512 would provide a more realistic comparison.

### Engineering Realities

**9. Implementation Complexity:**
The system requires: custom CUDA kernels for 6 primitives, Radix-16 NTT implementation, data layout management for BConv/IP transformations, dynamic mapping decisions for IP (TCU vs CUDA based on valid proportion), and multi-stream scheduling. This is substantial engineering that would be challenging to reproduce.

**10. Parameter Interdependencies:**
The paper presents parameters (N, L, WordSize, WordSize_T, dnum, α̃, BatchSize) as somewhat independent, but they're tightly coupled through security constraints (Equation 4), memory constraints, and performance tradeoffs. Finding "optimal" configurations requires navigating a complex multi-dimensional space that the sensitivity study only partially explores.

### What Would Make This Work More Complete

- H100 evaluation showing tensor core architectural changes don't break the approach
- Energy efficiency measurements
- Memory footprint analysis vs. BatchSize/parameter tradeoffs
- Numerical precision verification through end-to-end decryption accuracy tests
- Characterization of the INT8/FP64 crossover point across WordSize values
- Open-source implementation for reproducibility (not mentioned in paper)