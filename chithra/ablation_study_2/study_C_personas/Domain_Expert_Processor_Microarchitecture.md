# Paper Deconstruction: Neo — FHE Acceleration using Tensor Cores

## Q1: Whiteboard Explanation

Let me draw you the napkin version of what's actually happening here.

**The Problem They're Solving:**
Fully Homomorphic Encryption (FHE) lets you compute on encrypted data without decrypting it. Think of it as doing math while blindfolded — the data stays secret, but you can still add and multiply. The catch? It's *brutally* slow. We're talking 1000x-10000x slower than plaintext operations.

The CKKS scheme they target is particularly useful for machine learning on encrypted data because it handles approximate arithmetic (floating-point-ish operations). But the killer operation is **KeySwitch**, which happens every time you do a multiplication or rotation. It dominates runtime.

**What KeySwitch Actually Does (Simplified):**
1. **Mod Up (BConv)**: Convert polynomial coefficients from one modular representation to another. Think of it as changing the "number system" the encrypted data lives in.
2. **NTT**: Number Theoretic Transform — the modular arithmetic cousin of FFT. Converts polynomials to "evaluation form" so multiplication becomes element-wise.
3. **Inner Product (IP)**: Multiply the transformed ciphertext by pre-computed keys and accumulate results.
4. **INTT + Mod Down (BConv)**: Convert back.

**The Core Mechanism:**
The authors realize two things:

*First*, the existing GPU implementations (TensorFHE) use Tensor Cores (TCUs) only for NTT, and they use INT8 components. But FHE needs 36-60 bit integers. Splitting a 36-bit number into five 8-bit chunks means 25 partial multiplications (5×5 cross-products) plus merging overhead.

*Second*, BConv and IP are implemented as repeated element-wise multiplications with poor data reuse — each coefficient gets read from global memory multiple times.

**Neo's "Trick":**
1. **Algorithm Transformation**: Reshape BConv and IP from "element-wise multiply-accumulate" into proper matrix multiplications. This is shown in Algorithm 2 vs Algorithm 1 (Section 4.2). The input tensor gets reordered from (α × BatchSize × N) to (N × BatchSize × α), then multiplied by a small (α × α') conversion matrix. Same math, but now it's a GEMM.

2. **Use FP64 Tensor Cores instead of INT8**: Here's the counter-intuitive insight. FP64 has 53 bits of mantissa precision. A 36-bit integer fits perfectly! So instead of splitting into 5 INT8 chunks (25 partial products), split into 2-3 FP64 chunks (4-9 partial products). Figure 3 shows FP64 is 1.65x faster than INT8 for 36-bit integers.

3. **Adopt KLSS KeySwitch Method**: Instead of the standard "Hybrid" method, they use KLSS which allows choosing a different modulus size (WordSize_T) for intermediate computations. By picking WordSize_T = 48, they reduce algorithmic complexity while staying efficient on FP64 TCUs.

**The Data Flow (Figure 4):**
- BConv: Reorder → Split → Matrix Multiply (TCU) → Merge → Reorder
- NTT: Split → Matrix Multiply (TCU) → Twist → Transpose → Repeat
- IP: Reorder → Matrix Multiply (TCU or CUDA Cores depending on dimensions) → Reorder

The key is they've made three traditionally "scalar-heavy" operations into matrix operations that map onto TCUs.

---

## Q2: The Key Insight

**The Real Delta:**
This paper has *two* genuine contributions that I'd separate:

**Contribution 1 (Algorithmic): Reformulating BConv and IP as matrix multiplications.**
Prior work (TensorFHE) only accelerated NTT on TCUs. The authors observed that BConv and IP, which together account for ~85% of memory transfers in KeySwitch (Figure 2), are fundamentally "multiply a set of vectors by a constant matrix and accumulate." Algorithm 1 shows the naive triple-nested loop; Algorithm 2 shows the matrix form. This isn't just code restructuring — it changes the memory access pattern from O(α' × N × BatchSize) repeated reads to O(N × BatchSize × α) single reads followed by matrix multiply.

**Contribution 2 (Architectural): Using FP64 Tensor Cores for integer FHE arithmetic.**
This is the more surprising insight. Everyone assumed INT8 TCUs were the answer because they have 32x higher peak throughput than FP64 (624 TOPS vs 19.5 TFLOPS). But Figure 3 demolishes this assumption for FHE workloads — the Booth decomposition overhead for large integers makes INT8 *slower* in practice.

The specific numbers from Section 3.4: For 36-bit integers, INT8 needs 5×5=25 partial multiplications. FP64 needs only 3 (split the 36-bit number into 12+12+12 bits, stays under 2^53 after accumulating 16 products). That's 8x fewer operations, which more than compensates for FP64's lower raw throughput.

**What's NOT novel:**
- Using KLSS instead of Hybrid (that's from Kim et al. [28])
- Radix-16 NTT (that's from SHARP [25])
- Kernel fusion and multi-stream processing (standard GPU optimization)
- The basic idea of using TCUs for FHE (TensorFHE [12] did this)

**The "Aha" Moment:**
The paper's real cleverness is recognizing that the *algorithmic structure* of BConv and IP can be transformed to exploit TCUs, and that *FP64's precision* is actually a feature, not a limitation, for FHE's integer requirements. These two insights compound — you can't get the full benefit of FP64 TCUs without first making BConv/IP into matrix operations.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive Baselines (Table 5-6):** They compare against TensorFHE (the prior GPU TCU work), HEonGPU (recent non-TCU GPU work), and CPU implementations. This triangulation is good practice.

2. **Real Applications (Section 5):** They don't just microbenchmark NTT. They run PackBootstrap, HELR (logistic regression training), and ResNet-20/32/56 inference. These are standard FHE benchmarks from the literature.

3. **Detailed Kernel Breakdowns (Table 7, Figure 13):** They show where the speedups come from — BConv gets 2.74x, IP gets 2.60x, NTT gets 3.74x. This makes the contribution auditable.

4. **Sensitivity Studies (Section 6.3):** Figure 16 validates their WordSize_T = 48 choice by comparing against 36 and 64. Table 8 shows the parameter sweep for d_num and α̃.

5. **Memory Analysis (Figure 2, Figure 15):** They quantify the memory transfer reduction from their algorithmic transformations — this is the kind of evidence that separates "we made it faster" from "we understand why it's faster."

**Weaknesses:**

1. **Baseline Fairness Issues:**
   - TensorFHE was designed for WordSize ≤ 32 bits (Section 3.2). They had to reimplement TensorFHE with DS (Double Scaling) to support 36-bit WordSize (Table 5 footnote). This reimplementation may not be optimal.
   - The "CPU" baseline in Table 6 comes from a *different paper* (100x [22]) with *different parameters* (Set-H with L=44, not their Set-C with L=35). Cross-paper CPU numbers are notoriously unreliable.

2. **Limited Hardware Exploration:**
   - All experiments are on a single NVIDIA A100. No H100 (which has different TCU characteristics), no multi-GPU scaling, no comparison to AMD GPUs or Intel's Ponte Vecchio.
   - The A100's FP64 TCU throughput is unusually high (19.5 TFLOPS). On consumer GPUs or older architectures, the FP64 advantage may not hold.

3. **Security Parameter Concerns:**
   - Set-H in Table 4 has λ ≥ 98, below the standard 128-bit security threshold. While they note this, comparing at different security levels is problematic.
   - The paper doesn't discuss how their optimizations interact with side-channel attacks — a real concern for cryptographic implementations on GPUs.

4. **Missing Power/Energy Analysis:**
   - They report execution time but not power consumption. TCUs may have different power characteristics than CUDA Cores. For datacenter deployment, performance/watt matters.

5. **IP Mapping Heuristic (Section 4.5.3):**
   - They use an 80% "valid proportion" threshold to decide whether to use TCUs or CUDA Cores for IP. This magic number isn't justified — is it empirically derived? Analytically optimal? Figure 12 shows the valid proportion varies significantly with level, but the threshold choice seems arbitrary.

6. **Limited BatchSize Analysis:**
   - Figure 17 shows performance improves with larger BatchSize, but they stop at 128 "due to GPGPU memory capacity." This artificially limits comparisons — different GPUs would have different optimal batch sizes.

7. **No Error Analysis:**
   - CKKS is an *approximate* HE scheme. Using FP64 for intermediate computations could introduce precision differences compared to exact integer arithmetic. They claim correctness but don't quantify any precision loss.

---

## Q4: What the Authors Didn't Tell You

**The Hidden Costs:**

1. **Preprocessing/Postprocessing Overhead:**
   Figure 13 shows it clearly if you look carefully — the "Preprocessing" and "Postprocessing" bars for BConv(new) and IP(new) are non-trivial. For IP, preprocessing is almost as long as the matrix multiplication itself! They acknowledge this ("introducing additional overhead from preprocessing and postprocessing stages") but claim it's "negligible" — the figure suggests otherwise. The net kernel speedup comes from reducing memory transfers, not from TCU acceleration per se.

2. **The KLSS Method is Doing Most of the Work:**
   Look at Figure 14. Going from TensorFHE to "+KLSS" (just changing the KeySwitch algorithm, no other Neo optimizations) gets you 35-40% of the total improvement. The dataflow optimization adds another 15-20%. The TCU optimization adds the rest. The paper is titled around Tensor Cores, but KLSS adoption is arguably more important.

3. **FP64 TCU Advantage is Architecture-Specific:**
   The A100 has an unusually high FP64:INT8 TCU ratio. On the H100, FP64 TCU performance is similar (~67 TFLOPS vs 1979 TOPS for INT8) — an INT8:FP64 ratio of ~30x. The analysis in Section 3.4 and Figure 3 may not generalize.

4. **Evaluation Keys Memory Explosion:**
   They mention that IP requires "two sets of ββ̃α' polynomial keys" (Section 2.3) but don't quantify the memory footprint. For HROTATE with many rotation indices, evaluation key storage can exceed ciphertext storage. This limits practical batch sizes.

5. **Why Not FP32 Tensor Cores?**
   The A100 has 156 TFLOPS of FP32 TCU throughput — 8x more than FP64. For 36-bit integers, you could potentially use FP32 (24 bits of mantissa) with more splits but higher throughput. They never discuss this alternative.

6. **The "Valid Proportion" Problem is Fundamental:**
   Figure 12 reveals a core limitation: IP's valid proportion drops below 50% for low levels. This means for most of the computation in applications with many rescale operations, IP falls back to CUDA Cores. The optimization only helps for the early, high-level operations.

7. **Comparison to ASIC is Missing:**
   They cite Craterlake [40], SHARP [25], and Taiyi [11] as ASIC solutions but don't compare performance. ASICs achieve orders of magnitude better performance and efficiency. The implicit argument is "GPUs are more practical/available" but they should quantify the gap.

8. **Bootstrapping Dominates Everything:**
   Table 5 shows PackBootstrap takes 0.24s while a single HELR iteration takes 0.22s. But HELR does 32 iterations with periodic bootstrapping. The paper optimizes KeySwitch, but bootstrapping (which contains many KeySwitches) is still the bottleneck for unlimited FHE computation.

**Questions a Reviewer Would Ask:**

1. "Can you show results on non-A100 GPUs to demonstrate generality?"
2. "What is the precision difference between your FP64-based computation and exact integer arithmetic?"
3. "How does your approach scale to multi-GPU systems for larger batch sizes?"
4. "Why is 80% the threshold for IP mapping? Show a sensitivity analysis."
5. "What is the energy efficiency comparison, not just execution time?"

**The Bottom Line:**
This is a solid systems paper that combines algorithmic restructuring (BConv/IP → GEMM), algorithmic adoption (KLSS), and architectural insight (FP64 > INT8 for large integers). The 3.28x over TensorFHE is real. But the framing around "Tensor Cores" oversells the TCU contribution relative to the algorithmic changes, and the results may not transfer to other GPU architectures. For a PhD student: this is how you write a competitive ISCA paper — find multiple compounding optimizations and present them as a coherent system.