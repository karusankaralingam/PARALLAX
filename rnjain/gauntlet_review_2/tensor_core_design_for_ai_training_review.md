# Paper Deconstruction: Avant-Garde

## The "No-BS" Summary

This paper addresses a real problem: GPUs can't natively handle the zoo of emerging "scaled numeric formats" (MX4, MX6, MX9, HBFP, etc.) that the ML community is increasingly adopting to squeeze more FLOPS out of narrow-bitwidth arithmetic. Current GPUs only support FP8 with per-tensor scaling baked into software, so anything fancier—like MX9's two-level scaling hierarchy where 16 elements share one exponent and pairs of elements share a micro-exponent—requires a mess of CUDA Core instructions to unpack, scale, multiply, and repack. The authors propose adding a hardware "Operand Transformer" stage that flattens multi-level scaled formats into a single-level representation before hitting the Tensor Cores, plus modified Tensor Cores that can apply a shared scaling factor to dot-product results before accumulation. The result: fewer instructions, lower register pressure, and claimed 74% throughput improvement over baseline H100 running software-emulated MX9.

---

## The Core Mechanism: A Whiteboard Explanation

**The Problem in Plain English:**

Imagine you have a 16×16 tile of numbers, but instead of each number having its own full exponent (like FP16), you're using MX9 where:
- All 16 elements in a row share one 8-bit "block exponent"
- Every pair of adjacent elements shares an additional 1-bit "micro-exponent"
- Each element is just a 7-bit mantissa

To do a matrix multiply on a standard Tensor Core, you'd need to:
1. Load the block exponent and micro-exponents
2. For each element, multiply by 2^(block_exp + micro_exp) to "inflate" it back to a usable value
3. Feed the inflated values to the Tensor Core
4. Take the output and somehow deflate it back

This inflation/deflation dance requires extra instructions (mul, mad) and extra registers to hold intermediate values. The paper measured 2.14× more instructions and 1.38× more register usage for MX9 vs. INT8.

**Avant-Garde's Solution:**

1. **Operand Transformer (OT):** A new pipeline stage between register read and execute. When you load MX9 data, the OT takes the multi-level format and "flattens" it:
   - Apply all the micro-exponents to their respective element pairs
   - Keep only the top-level block exponent separate
   - Output: 32 fixed-point elements + 1 shared scaling factor

   Think of it like pre-multiplying all the nested exponents into the mantissas so you're left with a simple "block floating point" representation.

2. **Modified Tensor Core:** The Tensor Core now has:
   - An 8-bit adder to combine scaling factors from matrix A and matrix B (since exponents add when you multiply)
   - A "Scaling Unit" that multiplies the dot-product result by 2^(combined_scale) before accumulation

   So instead of: `result = Σ(A[i] × 2^expA × B[i] × 2^expB)` computed element-wise in software, you get: `result = 2^(expA + expB) × Σ(mantissaA[i] × mantissaB[i])` computed in hardware.

3. **Data Layout:** Flattened blocks are stored contiguously—32 elements + their scaling factor(s)—aligned to warp registers (128 bytes). Small blocks get coalesced; large blocks get sliced.

**The Key Insight:** All these fancy multi-level formats can be mathematically reduced to a single-level block floating point for the duration of a GEMM. You only need to flatten once (at load time), compute in flattened form, and optionally unflatten at the end. The flattening is a preprocessing step, not per-operation overhead.

---

## The Critique

### Why It Got In (The Strong Points)

1. **Addresses a Real Industry Pain Point:** The MX format is an OCP standard backed by Microsoft, NVIDIA, AMD, and Intel. The paper is timely—it's solving a problem that will matter as MX adoption grows.

2. **Clean Abstraction:** The "flattening" concept is elegant. Rather than building N different datapaths for N different scaled formats, they normalize everything to one internal representation. This is the right architectural philosophy.

3. **Reasonable Overhead:** 1.4% area, 1.2% power for the added hardware. The Operand Transformer is just 16 FP8 multipliers and some temp registers—not a massive investment.

4. **Solid Baseline Comparison:** They actually implemented MX9 in software on a simulated H100 and measured the instruction/register overhead. The 2.14× instruction increase is a concrete, believable number.

5. **Accuracy Validation:** They ran ViT-Base, BERT, and GPT-2 through a functional emulator and showed <0.2% accuracy deviation. This addresses the obvious concern that flattening might introduce precision loss.

### Where It's Weak (The Skeletons)

1. **Simulation-Only Evaluation:** This is Accel-Sim, not silicon. The 74% throughput improvement is against a *simulated* H100 running *their implementation* of software-emulated MX9. Real NVIDIA libraries might be more optimized. The baseline is somewhat of a strawman—they're comparing against their own software implementation, not a production-quality MX library.

2. **Workload Selection is Conservative:**
   - They ran ViT-Base, ViT-Large, BERT, GPT-2. These are 2020-era models.
   - No LLaMA, no Mixtral, no modern attention variants (FlashAttention, GQA, MQA).
   - No actual training runs—only inference and a microbenchmark.
   - The "microbenchmark" is just 1M parameters of GEMM. That's not representative of real memory-bound scenarios.

3. **Memory Bandwidth Elephant in the Room:** The paper focuses on compute throughput, but modern Transformer inference is often memory-bound, not compute-bound. If you're running GPT-2 with batch size 1, you're limited by weight loading, not MAC throughput. The paper doesn't show roofline analysis or discuss when their improvements actually matter vs. when you're bandwidth-limited anyway.

4. **Block Size Sensitivity is Hand-Waved:** They claim "less than 1% execution time increase" for block sizes up to 512, but they only tested on ViT-Large. What about attention layers with irregular shapes? What about the first/last layers of a network where dimensions don't tile nicely?

5. **Unflattening Overhead for Training:** They mention an "unflattening API" for training that runs on CUDA Cores and "introduces long latency." How long? They say it's "infrequent" but don't quantify. For training with frequent weight updates, this could be a real bottleneck.

6. **No Comparison to Structured Sparsity:** NVIDIA's A100/H100 already have 2:4 structured sparsity support. How does Avant-Garde's MX9 compare to FP16 with 2:4 sparsity in terms of effective throughput and accuracy? This is the obvious alternative approach to improving arithmetic density.

7. **The "Flattened MX9" Accuracy Claim is Suspicious:** They say flattened MX9 has "the same accuracy as non-flattened MX9." But flattening involves multiplying micro-exponents into mantissas, which could cause overflow/underflow or precision loss. They only tested three models—what about models with larger dynamic range in activations?

---

## Discussion Questions for the Student

1. **The Memory Bandwidth Question:** "Figure 10 shows 1.74× throughput improvement, but what's the operational intensity (FLOPS/byte) of their workloads? If ViT-Large inference at batch size 1 is memory-bound, does the Tensor Core improvement even matter? Why didn't they show a roofline plot?"

2. **The Baseline Fairness Question:** "They compare against their own software implementation of MX9 on a simulated H100. But NVIDIA hasn't released MX support yet—when they do, it might be more optimized. How would you design a fairer baseline? What if NVIDIA just adds MX support to cuBLAS with clever software tricks?"

3. **The Training Reality Check:** "They claim <0.2% accuracy deviation for inference, but training involves gradient computation, weight updates, and potentially thousands of flatten/unflatten cycles. The paper says unflattening has 'long latency' but doesn't quantify it. If you were training GPT-2 for 100K steps, how would you estimate the total unflattening overhead? What's the break-even point where Avant-Garde's training benefits outweigh the unflattening cost?"

---

## Contextual Fit

**Lineage:** This paper sits in the tradition of:
- **TPUv1's systolic array** (fixed-point, shared exponents via bfloat16)
- **HBFP/Equinox work from EPFL** (same authors—Falsafi's group has been pushing block floating point for years)
- **Microsoft's MSFP/MX papers** (the format definition this paper builds on)

**What's New:** Previous accelerators for block floating point (DBPS, FAST, Bucket Getter) were custom ASICs. Avant-Garde is the first to propose retrofitting this into a GPU's Tensor Core pipeline. That's the contribution—not the format itself, but the integration strategy.

**What's Missing:** No engagement with the sparsity literature (Ampere's 2:4, SparseRT, etc.). No discussion of how this interacts with FlashAttention-style memory optimizations. No comparison to the "just use FP8 with better calibration" approach that NVIDIA is pushing.

**The Real Question:** Is the MX format going to win? If NVIDIA decides FP8 with per-tensor scaling is "good enough" and optimizes the hell out of it in cuBLAS, Avant-Garde's value proposition weakens. The paper is betting on a future where multi-level scaled formats become standard—that's a reasonable bet given OCP backing, but it's still a bet.