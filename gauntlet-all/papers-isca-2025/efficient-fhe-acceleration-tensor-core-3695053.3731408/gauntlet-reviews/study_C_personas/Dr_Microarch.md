# Neo: Towards Efficient FHE Acceleration using Tensor Core

## Q1: Whiteboard Explanation

Let me draw out what's actually happening in this paper.

**The Problem Setup:**
FHE (specifically CKKS) requires massive polynomial arithmetic. The critical bottleneck is the **KeySwitch** operation, which appears in both HMULT and HROTATE. KeySwitch consists of six steps: Mod Up → NTT → Inner Product (IP) → INTT → Recover Limbs → Mod Down.

Three kernels dominate: **BConv** (Base Conversion), **NTT**, and **IP** (Inner Product). Looking at Figure 2, at level l=35, BConv and IP together consume ~85% of the global memory transfer in KeySwitch.

**The Core Transformation:**
The authors observed that both BConv and IP are fundamentally doing the same thing: *"element-wise multiply multiple limbs by specific arrays and accumulate the results."* This is algorithmically identical to matrix multiplication.

**BConv Before (Algorithm 1):**
```
for each input level i (0 to α-1):
    for each output level j (0 to α'-1):
        for each batch b:
            output[j][b] += input[i][b] × factor[i][j]
```
Each coefficient is read from global memory α' times. Terrible data reuse.

**BConv After (Algorithm 2):**
1. Reorder input tensor from [α × BatchSize × N] → [N × BatchSize × α]
2. Perform N×BatchSize matrix multiplications of shape [1 × α] × [α × α']
3. Reorder output back

Now each coefficient is read exactly once. The matrix multiplication can be offloaded to TCU.

**IP follows the same pattern** (Algorithm 3 → Algorithm 4), converting repeated element-wise multiplications into matrix multiplications of shape [BatchSize × β] × [β × β̃].

**The TCU Mapping Decision (Figure 1, Section 4.5):**

Here's where the hardware reality gets interesting. The A100 TCU has two distinct datapaths:
- **Component A**: INT8 units, supports fragment shapes 16×16×16, 32×8×16, or 8×32×16
- **Component B**: FP64 units, supports only 8×8×4 fragments

Prior work (TensorFHE) used INT8 for NTT, splitting 32-bit values into INT8 chunks. But SHARP [25] showed WordSize needs to be 36-bit for precision. This breaks the INT8 approach.

**Figure 3 reveals the critical insight:** For 36-bit integers, FP64 is 1.65× faster than INT8. For 48-bit, it's 1.74× faster. Why?

- **INT8 path for 36-bit:** Split into 5 chunks → 5×5=25 partial matrix multiplications + split/merge overhead
- **FP64 path for 36-bit:** FP64 has 53-bit mantissa → split into 3 chunks → only 3 partial multiplications (each 12 bits of one operand × full other operand, accumulation stays under 2^52)

**The Kernel-to-Hardware Mapping (Figure 4):**
- **NTT**: Always uses FP64 TCU (fixed 16×16 dimensions from Radix-16 decomposition, 100% valid computation)
- **BConv**: Always uses FP64 TCU (α×α' dimensions, 100% valid computation as shown in Figure 11)
- **IP**: Conditional mapping based on "valid proportion" threshold of 80% (Figure 12). When β and β̃ become small at lower levels, padding overhead exceeds TCU benefit → falls back to CUDA cores

**NTT Optimization (Section 4.4, Figure 9):**
They use Radix-16 NTT (from SHARP), which decomposes the 4-step NTT into smaller 16×16 matrix multiplications. This reduces matrix multiplication complexity from 2^25 to 2^22 (8× reduction), though it adds twisting factor overhead.

---

## Q2: The Key Insight

**The Magic Trick:** The authors recognized that the dominant FHE kernels (BConv and IP) are performing *disguised* matrix multiplications. By reshaping the data layout—specifically reordering tensors so the accumulation dimension becomes the K dimension of GEMM—they convert O(α'×N×BatchSize) individual scalar multiply-accumulates into O(N) matrix multiplications of shape [BatchSize × α] × [α × α'].

This is not a new algorithm. It's a **data layout transformation** that exposes existing parallelism in a form that TCUs can exploit.

**The Second Insight (Hardware-Specific):** Prior work assumed INT8 TCU components were optimal because they have ~32× higher peak throughput than FP64 (624 TFLOPS vs 19.5 TFLOPS on A100). But FHE requires 36-48 bit precision. The Booth decomposition overhead for INT8 (25 partial products for 36-bit) versus FP64's simpler 3-way split completely inverts this calculation.

The FP64 mantissa (53 bits) is *just barely* wide enough to accumulate 16 products of 36-bit × 12-bit values (36+12+log₂(16)=52 bits). This is a tight fit that happens to work perfectly for FHE's typical parameters.

**The KLSS Method Adoption:** The paper adopts the KLSS KeySwitch method (Section 2.2), which performs most computation in an extended ring R_T with selectable WordSize_T. This creates a tuning knob: larger WordSize_T reduces algorithmic complexity (smaller α') but increases TCU Booth complexity. They empirically find WordSize_T=48 optimal (Figure 16).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Comprehensive Comparison (Table 5):** They compare against TensorFHE under multiple parameter sets (Set-A, B, C) and against HEonGPU. The 3.28× speedup over TensorFHE's best configuration is convincing. The 19.9% average improvement over HEonGPU (a non-TCU implementation) validates that their TCU utilization actually helps.

2. **Incremental Ablation (Figure 14):** They break down the contribution of each optimization: KLSS adoption, dataflow optimization, Radix-16 NTT, and FP64 TCU usage. Each step shows measurable improvement, ruling out the possibility that one optimization dominates while others are noise.

3. **Memory Transfer Analysis (Figure 15):** They directly measure global memory transfer reduction—down to ~2× for BConv and IP in applications. This provides mechanistic evidence that the data reuse transformation is working as designed.

4. **Kernel-Level Validation (Table 7):** The 3.74× NTT speedup and 2.74× BConv speedup directly support the claimed mechanism.

5. **Valid Proportion Analysis (Figure 12):** The authors honestly show that their IP optimization degrades at lower levels (β, β̃ shrink), with valid proportion dropping below their 80% threshold. This explains their conditional CUDA core fallback.

### Weaknesses

1. **Single GPU Evaluation:** All experiments run on A100-40GB. The paper makes architectural claims about FP64 vs INT8 TCU tradeoffs but doesn't validate on H100 (which has different TCU architecture) or any AMD hardware. The optimal WordSize_T=48 is likely A100-specific.

2. **BatchSize Dependency (Figure 17):** Performance degrades substantially at lower BatchSize (nearly 2× slower at BS=8 vs BS=128). Many real FHE applications process single ciphertexts. The authors acknowledge this but don't propose solutions. The "average time per batch" normalization in Section 6 obscures per-ciphertext latency.

3. **Memory Capacity Constraint (Section 6.3):** They state "due to the limitations of GPGPU memory capacity, BatchSize cannot be increased indefinitely." At BS=128 with L=35, they're using most of the 40GB. They don't analyze memory footprint or discuss multi-GPU scaling, which matters for practical deployment.

4. **Missing Preprocessing Overhead:** The paper separates "preprocessing" and "postprocessing" times (Figure 13) but doesn't discuss where the reordered evaluation keys come from. The IP kernel requires evaluation keys pre-arranged in [N × α' × β × β̃] layout. If this happens at runtime, it adds latency; if offline, it multiplies key storage.

5. **Precision Validation Gap:** They claim WordSize=36 is needed for precision (citing SHARP [25]) and that their implementation supports it. But the evaluation doesn't validate output correctness—no comparison of decrypted results against ground truth or noise budget analysis.

6. **Reproducibility Concerns:** The parameter sets vary significantly (Table 4). Set-D uses 60-bit WordSize and L=35 to match HEonGPU, while Neo's own results use Set-C (36-bit, L=35). These aren't directly comparable due to different security guarantees (Set-D notes λ≥98 vs λ≥128).

---

## Q4: What the Authors Didn't Tell You

1. **The Hidden Memory Bandwidth Reality:** Figure 2 shows that global memory transfers are 7.80GB per KeySwitch at l=35 with KLSS (vs 9.43GB Hybrid). But A100 has 1.5TB/s bandwidth—meaning a KeySwitch should take ~5.2ms just for memory. Table 6 shows HMULT at 3.47ms. This implies either (a) their analysis overcounts transfers, (b) L2 caching effects are significant, or (c) their optimization reduces effective transfers more than claimed. They don't reconcile these numbers.

2. **The Evaluation Key Storage Problem:** The KLSS method requires 2×β×β̃×α' polynomial evaluation keys (Section 2.3). For Set-C parameters (α'=8, β and β̃ varying with level), at level 35, this is substantial. They mention 40GB VRAM but don't break down how much is ciphertexts vs evaluation keys vs workspace. This matters because key storage often dominates FHE memory.

3. **The Conditional IP Mapping is a Hack:** Section 4.5.3 states that when valid proportion <80%, they fall back to CUDA cores. Figure 12 shows this happens for most levels below l=15. For Bootstrapping (which touches low levels), a significant portion of IP operations run on CUDA cores, not TCUs. The "TCU acceleration" headline doesn't fully apply.

4. **Radix-16 NTT is from SHARP, Not Novel:** Section 4.4 cites SHARP [25] for Radix-16 NTT. The paper's contribution is mapping it to TCU FP64, but the algorithmic complexity reduction (2^25 → 2^22) is not their innovation.

5. **The FP64 "Discovery" Has Caveats:** The 53-bit mantissa trick (Section 3.4) requires that one operand be split to ≤12 bits while the other stays full precision. This works for BConv (constant factors can be pre-split) but for IP, both operands are limbs. The paper doesn't fully explain how they handle IP with FP64—it seems they must split both operands, giving 2×2=4 partial products, not the "3" claimed for BConv.

6. **No Power or Efficiency Numbers:** ISCA papers often include power measurements. An A100 draws 250-400W. They don't compare performance-per-watt against CPU baselines or estimate TCU utilization rates. "3.28× faster" with equal power is very different from "3.28× faster with 4× power."

7. **The "Practical" Claim is Arguable:** Table 5 shows PackBootstrap takes 0.24 seconds and ResNet-20 inference takes 12.03 seconds. For context, plaintext ResNet-20 inference takes <10ms. The 1000× overhead means FHE remains impractical for latency-sensitive applications despite this speedup.

8. **Figure 11's INT8 Comparison is Misleading:** They show INT8 requires 32×8×16 fragments with 75% padding for α=4, α'=8. But INT8 also supports 16×16×16 and 8×32×16 shapes. A fair comparison would select the best-fitting INT8 shape, not the worst. The FP64 advantage may be smaller than depicted.