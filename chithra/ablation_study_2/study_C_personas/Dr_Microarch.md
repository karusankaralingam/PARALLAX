# Neo: Architectural Deconstruction

## Q1: Whiteboard Explanation

Let me walk you through what's actually happening in the silicon when Neo runs a Fully Homomorphic Encryption (FHE) workload on an NVIDIA A100 GPU.

**The Problem They're Solving:**
FHE lets you compute on encrypted data, but it's computationally brutal. The CKKS scheme (which handles approximate arithmetic on encrypted numbers) requires massive polynomial operations. Each polynomial has N=2^16 coefficients, and each coefficient is a 36-60 bit integer. The killer operation is KeySwitch, which involves:
1. **BConv (Base Conversion):** Convert polynomials between different modulus bases
2. **NTT (Number Theoretic Transform):** Like FFT, but over finite fields
3. **IP (Inner Product):** Multiply-accumulate with giant evaluation keys

**The Hardware Reality:**
Looking at Figure 1, the A100's Tensor Core Unit (TCU) has two distinct computational pathways:
- **Component A:** INT8 multipliers (624 TFLOPS peak)
- **Component B:** FP64 multipliers (19.5 TFLOPS peak)

TensorFHE (the prior work) tried to use the INT8 path by splitting 36-bit integers into multiple 8-bit chunks using Booth's algorithm. This requires 5×5=25 partial matrix multiplications for a single 36-bit×36-bit operation.

**Neo's Core Mechanism:**
Instead, Neo uses the FP64 components. Here's the trick: FP64 has 53 bits of mantissa precision, which can represent integers up to 2^53 exactly. For a 36-bit WordSize:
- Split B matrix into three 12-bit chunks stored as FP64
- Compute: A × B_low, A × B_mid, A × B_high
- Only 3 matrix multiplications instead of 25

Figure 3 shows this empirically: at WordSize=36, FP64 is 1.65× faster than INT8. At WordSize=48, it's 1.74× faster.

**The Data Reuse Trick (Algorithms 1-4):**
The original BConv and IP kernels perform element-wise operations, reading each coefficient from global memory multiple times (α' times for BConv, β̃ times for IP). 

Neo transforms these into matrix multiplications:
- BConv: Reshape N×BatchSize×α tensor, multiply by α×α' conversion matrix
- IP: Reshape to N×α'×BatchSize×β, multiply by β×β̃ evaluation key matrices

This converts O(α' × N × BatchSize) memory accesses into O(N × BatchSize + N × α') due to data reuse in the matrix multiply.

**The Memory Architecture Impact:**
Figure 2 shows BConv and IP together consume ~85% of KeySwitch memory bandwidth (43.4% + 41.8% at l=35 in KLSS). By converting to matrix multiplications, Figure 15 shows application-level memory transfer reductions from 144GB to roughly 70GB for PackBootstrap.

## Q2: The Key Insight

**The "Magic Trick" is exploiting FP64's exact integer arithmetic within TCUs.**

Let me be precise about what's clever here. The A100 TCU's FP64 components were designed for scientific computing that needs high precision. Neo repurposes them for exact 48-bit integer arithmetic by recognizing that:

1. FP64 mantissa = 53 bits, which can exactly represent any integer up to 2^53
2. For a 36-bit × 12-bit multiplication, the result fits in 48 bits < 53 bits
3. With K=16 accumulations in the GEMM, you get 36 + 12 + 4 = 52 bits < 53 bits

This means Neo gets **exact** integer results from floating-point hardware with no rounding error.

The second insight is the **algorithmic transformation** from element-wise operations to matrix multiplications. Looking at Algorithm 2 vs Algorithm 1:
- Original BConv: Triple nested loop with scalar multiply-accumulate
- Neo BConv: Tensor reshape + batched GEMM

This isn't just about TCU compatibility—it fundamentally changes the memory access pattern. In the original, each coefficient is read α' times from global memory. In Neo's version, data locality in the GEMM tiles means each coefficient is read once into shared memory and reused α' times.

**The structural delta from TensorFHE:**
TensorFHE used TCUs only for NTT, and used INT8 components. Neo extends TCU usage to BConv and IP kernels, and switches to FP64. The fragment size difference matters: INT8 requires 16×16×16 fragments (with padding for small dimensions), while FP64 uses 8×8×4 fragments that better match the algorithm's natural dimensions (α=4, α'=8 in their parameters).

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive kernel-level profiling (Table 7):** They show BConv at 2.74×, IP at 2.60×, NTT at 3.74× speedup individually. This decomposition is honest—you can trace the 3.28× application speedup back to kernel improvements.

2. **Memory transfer analysis (Figure 2, Figure 15):** The paper quantifies actual GB transferred, not just theoretical bandwidth. At l=35, KeySwitch drops from 9.43GB to 7.80GB with KLSS, and their optimizations reduce BConv+IP memory requirements by ~50% (visible in Figure 15a).

3. **Sensitivity studies (Table 8, Figure 16):** They sweep dnum from 4-18 and α̃ from 4-10, showing the optimal point at dnum=9, α̃=5. Figure 16 shows WordSize_T=48 beats both 36 and 64, validating their trade-off analysis.

4. **Real applications (Table 5):** PackBootstrap (0.24s vs 0.67s), ResNet-20 (12.03s vs 38.77s), HELR (0.22s vs 0.73s) on identical parameter sets. These aren't microbenchmarks.

**Weaknesses:**

1. **The "valid proportion" threshold is arbitrary (Section 4.5.3):** They claim IP switches to CUDA Cores when valid proportion drops below 80%, but Figure 12 shows this happens around l=20. They never justify why 80% is the right threshold—did they sweep this parameter? What's the crossover curve?

2. **BatchSize dependency obscures single-operation latency (Figure 17):** All results use BatchSize=128. At BatchSize=8, performance degrades 2× or more. Many real FHE applications process single ciphertexts. They don't report latency for BatchSize=1.

3. **HEonGPU comparison is incomplete:** Table 5 shows Neo beats HEonGPU by ~20% on average, but HEonGPU doesn't use TCUs at all. The fair comparison would be Neo-without-TCU vs HEonGPU to isolate their algorithmic contributions from TCU usage.

4. **Missing power/energy analysis:** They're using different TCU components than TensorFHE (FP64 vs INT8). What's the energy cost? The A100's FP64 TCU runs at lower throughput but what's the power draw?

5. **Multi-stream "optimization" lacks microbenchmark (Section 4.6):** They claim multi-stream processing helps, but provide no ablation. How much does kernel fusion contribute vs multi-stream? The data in Figure 14 lumps all TCU optimizations together.

6. **Security parameter validation is weak:** Table 4 shows Set-H has λ≥98, below the 128-bit security target. They use this for CPU comparison but don't note it's insecure.

## Q4: What the Authors Didn't Tell You

**Hidden Hardware Costs:**

1. **Data layout transformation overhead:** Algorithms 2 and 4 show preprocessing (reshape from α×BS×N to N×BS×α) and postprocessing steps. Figure 13 claims these are "negligible," but look closer—preprocessing for BConv takes roughly 0.3μs out of 0.9μs total, which is 33% overhead. They're hiding this by showing absolute times are smaller than the original kernel.

2. **Shared memory pressure:** The kernel fusion strategy (Section 4.6) stores intermediate results in shared memory. A100 has 164KB shared memory per SM. For BatchSize=128, N=2^16, and 8-byte FP64 values, a single "tile" of α=4 polynomials requires 4×128×16×8 = 64KB just for one fragment's worth. They don't discuss tile sizing or occupancy impact.

3. **The KLSS method requires 2× more evaluation keys:** Table 2 shows IP complexity is β̃×β×α' for KLSS vs 2β(l+α) for Hybrid. But the evaluation keys are stored in global memory—for Set-C with β=4, β̃=6, α'=8, that's 192 polynomial keys vs roughly 156 for Hybrid at l=35. This memory footprint increase isn't discussed.

4. **Register file pressure from FP64:** FP64 operands consume 2× the register space of INT32. The A100 has 65,536 32-bit registers per SM. TCU FP64 fragments (8×8×4) require loading 8×4=32 FP64 values = 64 registers just for matrix A's fragment. They never discuss register spilling.

5. **The WordSize_T=48 choice has a hidden implication:** Using 48-bit intermediate values in R_T means NTT twiddle factors are also 48-bit. But their FP64 splitting (Figure 11) shows they split into 24-bit chunks, requiring 2 FP64 matrices per operand. This means 2×2=4 GEMMs for each NTT matrix multiply, not explicitly stated.

6. **Latency hiding assumptions:** Multi-stream processing (Section 4.6) assumes TCU and CUDA Core computations can overlap. But looking at Figure 4, NTT requires sequential split→GEMM→merge steps. The CUDA Core work (split/merge) produces data that TCU consumes, creating a dependency chain that limits overlap.

**What they glossed over in the "3.28× speedup" headline:**
- This is vs TensorFHE's best configuration (Set-B), not same parameters
- TensorFHE at Set-A (their original params) gets 3.41× speedup
- The Set-C params used by Neo have more aggressive dnum=9, which helps KLSS but isn't a Neo innovation
- Against HEonGPU (a 2024 paper), the speedup is only 1.36× on ResNet-20

The authors correctly describe their contributions but the framing emphasizes comparison with 2023-era TensorFHE rather than acknowledging that HEonGPU without TCUs gets within 36% of Neo with TCUs.