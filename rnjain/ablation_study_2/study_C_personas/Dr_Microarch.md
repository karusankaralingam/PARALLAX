# Avant-Garde: Architectural Deconstruction

## Q1: Whiteboard Explanation

Let me draw you the wiring diagram of what's actually happening here.

**The Problem They're Solving:**
Modern GPUs have Tensor Cores that can crunch INT8/FP8 matrix operations blazingly fast. But "scaled numeric formats" like MX9 have this hierarchical structure: a block of 16 elements shares an 8-bit first-level scaling factor, and within that, pairs of 2 elements share a 1-bit second-level scaling factor (see Figure 1b). The Tensor Core has no idea what to do with this hierarchy—it just sees fixed-point numbers.

**Current Baseline (The Pain):**
Look at Figure 3—this is the smoking gun. To do one MMA operation with scaled formats on a baseline H100:
1. `wmma.load.a`, `wmma.load.b`, `wmma.mma` — the Tensor Core part
2. Then FOUR `ld.global` instructions to fetch scaling factors
3. Then a cascade of `mul` and `mad` instructions to apply scaling factors to results

That's why Figure 4 shows 2.14× more instructions and 1.38× more register usage for MX9 vs INT8. The Tensor Core finishes its job, dumps partial results, then CUDA Cores have to clean up the scaling mess.

**Avant-Garde's Architecture (Figure 6):**
They insert a new pipeline stage called **Operand Transform** between operand read and execute. The key hardware additions:

1. **Operand Transformer (Figure 7):** 16 FP8/INT8 multipliers + 32 temporal registers. For a two-level format like MX9, it takes the second-level scaling factors (those 1-bit values) and multiplies them into each element. The first-level scaling factor gets passed through untouched. This "flattens" the multi-level format into a single-level format.

2. **Avant-Garde Tensor Core (Figure 8):** They add two things to a standard Tensor Core:
   - An 8-bit fixed-point adder that combines the scaling factors from matrices A and B (since scaling factors are exponents, you just add them)
   - A "Scaling Unit" that multiplies the combined scaling factor into the dot product result *before* accumulation

**The Data Flow:**
```
Memory → Register File → Operand Transformer (flatten) → AG Tensor Core (scale & accumulate) → Register File/Memory
```

The flattened representation stays in that format for the entire workload execution—weights get flattened once before inference, inputs get flattened on entry. This eliminates repeated conversion overhead.

**Block Size Handling (Figure 5):**
- Block size ≤16: Coalesce multiple blocks into one 32-element flattened block, keeping their scaling factors
- Block size = 32: Direct mapping, one block → one flattened block
- Block size > 32: Split into multiple flattened blocks, each retaining the original scaling factor

The warp size (32 threads) is the magic number—everything gets aligned to 128-byte warp registers.

## Q2: The Key Insight

The "magic trick" is recognizing that **multi-level scaled formats are computationally equivalent to single-level formats if you pre-multiply the lower-level scaling factors into the elements**. 

Here's the bit-level insight: In MX9, you have:
- First-level: 8-bit exponent shared across 16 elements
- Second-level: 1-bit exponent shared across pairs of 2 elements  
- Elements: 8-bit fixed-point (7-bit mantissa + 1-bit sign)

The authors observed that the second-level 1-bit scaling factor is essentially just "shift left by 0 or 1." If you pre-shift each element by its second-level factor, you now have a standard single-level block floating point format that existing Tensor Core designs can handle—with one modification: the Tensor Core must apply the (now single) scaling factor to dot product results before accumulation.

This is clever because:
1. **The flattening is SIMD-friendly**: All 32 elements in a block can be flattened in parallel (16 multipliers handling 2 elements each, twice)
2. **The scaling factor application is trivial**: Adding two 8-bit exponents is just an 8-bit addition, and the "scaling unit" is essentially a shifter
3. **The representation persists**: Once flattened, operands stay flattened through the entire inference/training pipeline

The key equation they're exploiting (implicit in Section 3): For a two-level format, the true value of element *i* is:
```
V[i] = Element[i] × 2^(L1_scale) × 2^(L2_scale[i/2])
```
Flattening pre-computes `FlatElement[i] = Element[i] × 2^(L2_scale[i/2])`, leaving only `L1_scale` for the Tensor Core to handle.

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Realistic Baseline Comparison:** They actually implement the software-based MX9 handling on a baseline (Figure 3, Section 2.2) and measure the overhead with nvcc and Nsight Compute. The 2.14× instruction count and 1.38× register usage for MX9 vs INT8 (Figure 4) are compelling empirical evidence, not estimates.

2. **Silicon Overhead is Quantified:** Section 3.3 reports synthesis results using FreePDK 45nm. The 1.4% area and 1.2% power overhead relative to a full GPU pipeline is believable for 16 multipliers + 32 registers per SM.

3. **Accuracy Validation is Present:** Table 4 shows <0.2% accuracy difference between flattened MX9 and FP32 on ViT-Base, BERT, and GPT-2. They properly acknowledge that flattening introduces quantization error and measure it.

4. **End-to-End Workloads:** They run full inference on ViT-Base (86M params), ViT-Large (307M), BERT (110M), and GPT-2 (124M) rather than just microbenchmarks.

**Weaknesses:**

1. **Accel-Sim Simulation Only:** All performance numbers come from Accel-Sim (Table 1, Section 4). There's no RTL implementation, no FPGA prototype. The 74% throughput improvement and 44% execution time reduction are simulated, not measured on real silicon.

2. **FP8 Modeling is Synthetic:** They admit "Accel-Sim does not support FP8" (Section 4) and they "modify the simulator to compute a scaling factor so that FP8 operations execute with the same latency as INT8." This is a significant simplification—FP8 E4M3 and E5M2 have different numerical properties than INT8.

3. **Memory Bandwidth Analysis Missing:** The paper focuses entirely on compute. Figure 10's throughput is "operations per clock cycle," but for real DNN workloads on GPUs, memory bandwidth often dominates. They don't show memory traffic comparisons or whether the additional scaling factor storage creates bandwidth pressure.

4. **Training Evaluation Absent:** They claim support for training (Section 3.2 describes "unflattening API") but all evaluations are inference-only. The microbenchmark and four DNN models in Table 3 are all inference workloads. Training backward passes and weight updates are not evaluated.

5. **Limited Scaled Format Diversity:** They evaluate three formats: HBFP, MX9, and MXFP8 (Table 2). But the sensitivity study in Section 5.6 uses "hypothetical numeric formats" for scaling levels beyond 2. No actual three-level or four-level format is evaluated on real workloads.

6. **Register File Utilization Claims Inconsistent:** Section 3.1 claims "Avant-Garde mitigates this overhead through dedicated Operand Transformers and redesigned Tensor Cores" without increasing register file utilization. But the flattened representation still needs to store scaling factors alongside elements (Figure 5 shows scaling factors embedded in the flattened block). The paper never shows a direct register utilization comparison for Avant-Garde vs baseline.

## Q4: What the Authors Didn't Tell You

**The Hidden Hardware Tax:**

1. **Operand Transformer Latency Impact:** Section 3.2 states the Operand Transform stage "performs 2×(N-1) iterations" for N scaling levels and "introduces a latency impact of two cycles per warp" (Section 3.3). For MX9 (N=2), that's 2 iterations. But they then claim in Section 5.6 that "operand transformation accounts for less than 1% of total execution time" because it's "hidden by interleaved warp execution." This hand-waves away the pipeline depth increase—every instruction in Avant-Garde has one more stage to traverse.

2. **The 32-Byte Temporal Registers Are Real SRAM:** Figure 7 shows "thirty-two temporal registers" in Operand Transformer. That's 32×32 = 1024 bytes per Operand Transformer. With 114 SMs (Table 1), that's 114KB of additional register-like storage they're adding. This is buried in the synthesis numbers but never explicitly accounted for in the area breakdown.

3. **Warp Register Waste:** Section 3.1 admits: "with the MX6 format, Avant-Garde requires only 192 bytes for a block, occupying two warp registers and leaving 64 bytes unused." That's 25% waste for MX6. For smaller block sizes or elements, this waste could be higher.

4. **CUDA Core Unflattening is Slow:** Section 3.2 states the unflattening API for training "leverages CUDA cores" and "these operations are performed on CUDA cores, they introduce a long latency." They then dismiss it as "unflattening occurs infrequently." But for training, weight updates happen every batch. How infrequent is "infrequent"?

5. **Non-GEMM Operations Still Suffer:** Section 3.1 acknowledges that "for all non-GEMM operations, Avant-Garde maintains operands in registers in the same manner as the baseline GPU described in Section 2.2." This means LayerNorm, Softmax, activation functions all still pay the register bloat penalty. They claim these "represent only a small portion of the total workload"—but in transformers, attention mechanisms have significant non-GEMM computation.

6. **The Scaling Unit is a Variable Shifter:** Figure 8 shows a "Scaling Unit" after the dot product and before accumulation. Since scaling factors are exponents, this is essentially a barrel shifter. For 8-bit scaling factors, that's a 256-position shift capability on a 32-bit (or larger) accumulator result. Barrel shifters of this size are not trivial—the 3.9% Tensor Core area overhead (Section 3.3) is mostly this shifter.

7. **Block Size Flexibility Claim vs Reality:** Section 3 claims support for "diverse scaled numeric formats" but the flattening strategy (Figure 5) is hardcoded around a 32-element boundary matching warp size. True flexibility would require parameterizable boundaries. The sensitivity study (Section 5.6) testing block sizes up to 512 shows only 1.1% execution time increase—but doesn't report area/power for supporting these larger configurations.

8. **No Analysis of Scaling Factor Cache Behavior:** Scaling factors are stored "side by side" with elements (Section 3). For large models where weights don't fit in cache, fetching scaling factors alongside weights increases memory traffic by 8 bits per block. For a block size of 32, that's 0.25 bits per element overhead. For block size of 16 (MX9), it's 0.5 bits per element. This is never quantified.