# Study C — Multi-Persona Synthesis
**Paper:** 3695053.3731408  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 08:30

---

# Q1: Whiteboard Explanation

Neo accelerates Fully Homomorphic Encryption (FHE) on GPUs by transforming memory-bound element-wise operations into compute-bound matrix multiplications that can leverage Tensor Core Units (TCUs).

**The Problem Context:**
FHE (specifically the CKKS scheme) enables computation on encrypted data, but with astronomical overhead—often 10,000× slower than plaintext. The critical bottleneck is **KeySwitch**, which appears in both HMULT and HROTATE operations. KeySwitch consists of six steps: Mod Up → NTT → Inner Product (IP) → INTT → Recover Limbs → Mod Down. Three kernels dominate: **BConv** (Base Conversion), **NTT** (Number Theoretic Transform), and **IP** (Inner Product). Figure 2 reveals that at level l=35, BConv and IP together consume ~85% of global memory transfer.

**The Core Transformation:**
The authors observed that BConv and IP are fundamentally performing *disguised* matrix multiplications: "element-wise multiply multiple limbs by specific arrays and accumulate the results." The original algorithms have terrible data reuse—each coefficient is read from global memory α' times (BConv) or β̃ times (IP).

*BConv Before (Algorithm 1):* Each coefficient accessed α' times from global memory.

*BConv After (Algorithm 2):*
1. Reorder input tensor from [α × BatchSize × N] → [N × BatchSize × α]
2. Perform N×BatchSize matrix multiplications of shape [1 × α] × [α × α']
3. Reorder output back

Now each coefficient is read exactly once, and the computation maps to TCU-accelerated GEMM.

**The FP64 vs INT8 Decision (Figure 1, Section 3.4):**
Prior work (TensorFHE) used INT8 Tensor Cores, which seemed attractive given 624 TFLOPS peak versus FP64's 19.5 TFLOPS. But FHE requires 36-bit precision (per SHARP [25]). The Booth decomposition overhead inverts this calculation:
- **INT8 for 36-bit:** Split into 5 chunks → 25 partial matrix multiplications + split/merge overhead
- **FP64 for 36-bit:** 53-bit mantissa → split into 2-3 chunks → only 3-4 partial multiplications

Figure 3 shows FP64 is 1.65× faster for 36-bit and 1.74× faster for 48-bit computations. Additionally, FP64's 8×8×4 fragment shape matches FHE dimensions better than INT8's 16×16×16—Figure 11 shows INT8 wastes 75% of computation on padding for typical BConv dimensions (α=4, α'=8), while FP64 achieves 100% utilization.

**The KLSS Method Adoption:**
Neo adopts the KLSS KeySwitch method (Section 2.2), which performs computation in an extended ring R_T with selectable WordSize_T. This creates a tuning knob: larger WordSize_T reduces algorithmic complexity (smaller α') but increases TCU Booth complexity. They empirically find WordSize_T=48 optimal (Figure 16).

**The Complete Dataflow (Figure 4):**
- BConv/NTT/IP: Split & Reorder → Matrix Mult (on TCU FP64) → Reorder & Merge
- IP has conditional mapping: TCU if valid computation proportion >80%, else CUDA Cores (Figure 12)
- NTT uses Radix-16 decomposition (from SHARP), reducing complexity from 2^25 to 2^22

---

# Q2: The Key Insight

The paper's central insight is a **mismatch exploitation**: prior work assumed "more TOPS = better" and chased INT8 Tensor Cores (624 TFLOPS) over FP64 (19.5 TFLOPS). But this ignores the **Booth complexity tax**—when data width exceeds native precision, you pay in decomposition overhead, not just FLOPS.

**The First Insight (Hardware-Specific):**
For 36-bit FHE coefficients, INT8 requires O(n²) partial products (5×5=25 for 36-bit) while FP64 requires only O(1) effective operations per multiplication (3-4 partial products). The FP64 mantissa (53 bits) is *just barely* wide enough to accumulate 16 products of 36×12 bit values (36+12+log₂(16)=52 bits). This tight fit happens to work perfectly for FHE's typical parameters.

**The Second Insight (Algorithmic):**
The dominant FHE kernels (BConv and IP) are performing *disguised* matrix multiplications. By reshaping data layouts—specifically reordering tensors so the accumulation dimension becomes the K dimension of GEMM—they convert O(α'×N×BatchSize) individual scalar multiply-accumulates into O(N) matrix multiplications. This is not a new algorithm; it's a **data layout transformation** that exposes existing parallelism in a form TCUs can exploit.

**The Third Insight (Algorithm-Architecture Co-Design):**
The KLSS method's configurable WordSize_T creates a knob to balance algorithmic complexity (number of limbs) against hardware implementation complexity (Booth decomposition overhead). The optimal point (WordSize_T=48) emerges from understanding both dimensions—too small means too many limbs, too large means too much Booth overhead.

**What's Actually New vs. Borrowed:**
- Using GPUs for FHE, Tensor Cores for NTT, KLSS algorithm, Radix-16 NTT: all borrowed from prior work
- **Novel contributions:** (1) Reformulating BConv and IP as matrix multiplications (Algorithms 2 and 4), (2) First use of FP64 Tensor Cores for FHE, (3) Hardware-aware parameter tuning showing WordSize_T=48 is optimal

---

# Q3: Evaluation Critique

### Strengths

**1. Comprehensive Multi-Granularity Evaluation:**
The evaluation spans kernel performance (Table 7: 3.74× NTT, 2.74× BConv, 2.60× IP speedups), operation performance (Table 6), and full application performance (Table 5). This layered approach lets readers understand where speedups originate and validates the claimed mechanisms.

**2. Honest Incremental Ablation (Figure 14):**
The breakdown of contributions (+KLSS: ~25-35%, +dataflow: ~15-25%, +Radix-16 NTT: ~20-30%, +FP64 TCU: ~10-15%) prevents "black box speedup" claims. Each step contributes measurably, and notably, the FP64 TCU contribution is the smallest—suggesting algorithmic changes dominate.

**3. Memory Transfer Validation (Figure 2, Figure 15):**
They directly measure global memory transfer reduction—down to ~2× for BConv and IP in applications. This provides mechanistic evidence that the data reuse transformation works as designed, grounding claims in measurable reality rather than theoretical complexity.

**4. Sensitivity Studies:**
Table 8 explores the d_num × α̃ parameter space, Figure 16 validates WordSize_T=48, Figure 17 shows BatchSize effects. This transparency about parameter selection builds confidence results aren't cherry-picked.

**5. Fair Baseline Comparison:**
They compare against TensorFHE (reimplemented with Double Scaling for correctness) and HEonGPU on the same A100 hardware. The 3.28× speedup over TensorFHE's best configuration is credible.

### Weaknesses

**1. Single GPU Architecture:**
All experiments run on A100-40GB. The FP64:INT8 throughput ratio varies across generations—on H100, FP64 is 67 TFLOPS while INT8 is 3958 TFLOPS (much larger gap). The optimal WordSize_T=48 is likely A100-specific. No validation on AMD or Intel hardware.

**2. BatchSize Dependency (Figure 17):**
Performance at BatchSize=8 is ~2× worse than BatchSize=128. Many real FHE applications process single ciphertexts. The "average time per batch" normalization obscures per-ciphertext latency, which matters for interactive use cases.

**3. The "Valid Proportion" Threshold is Suspiciously Convenient:**
Section 4.5.3's 80% threshold for IP TCU vs CUDA Core mapping isn't justified. Figure 12 shows IP's valid proportion drops below 80% for most levels below l=15. For Bootstrapping (which touches low levels), significant IP operations run on CUDA cores—the "TCU acceleration" headline doesn't fully apply.

**4. Missing Correctness Validation:**
FHE with approximate arithmetic (CKKS) is precision-sensitive. The paper never validates output correctness—no comparison of decrypted results against ground truth, noise budget analysis, or final model accuracy for HELR/ResNet workloads.

**5. HEonGPU Comparison Uses Different Parameters:**
Neo uses Set-C (WordSize=36, KLSS with WordSize_T=48); HEonGPU uses Set-E (WordSize=60, Hybrid method). The 19.9% advantage may partially reflect algorithmic parameter choices rather than implementation quality.

**6. No Power/Energy Analysis:**
An A100 draws 250-400W. No performance-per-watt comparison against CPU baselines or TCU utilization rates. "3.28× faster" with equal power is very different from "3.28× faster with 4× power."

**7. Missing Profiler Breakdowns:**
No Nsight Compute or nvprof analysis showing TCU utilization rates, achieved TFLOPS vs. peak, or SM occupancy. We can't distinguish algorithmic wins from implementation quality.

---

# Q4: What the Authors Didn't Tell You

**1. The Hidden Memory Bandwidth Reality:**
Figure 2 shows 7.80GB per KeySwitch at l=35. A100 has 1.5TB/s bandwidth—meaning KeySwitch should take ~5.2ms just for memory. Table 6 shows HMULT at 3.47ms. This implies either their analysis overcounts transfers, L2 caching effects are significant, or optimization reduces effective transfers more than claimed. These numbers aren't reconciled.

**2. Evaluation Key Storage Problem:**
The KLSS method requires 2×β×β̃×α' polynomial evaluation keys (Section 2.3). At N=2^16 with 64-bit coefficients, one polynomial is 512KB. With β̃βα' potentially reaching hundreds, evaluation keys alone consume gigabytes. The BatchSize=128 limit likely reflects this constraint, not ciphertext storage. The paper doesn't break down memory footprint.

**3. The Conditional IP Mapping is a Hack:**
When valid proportion <80%, IP falls back to CUDA cores. Figure 12 shows this happens for most levels below l=15. The "TCU acceleration" headline doesn't fully apply to workloads spending significant time at low levels.

**4. Pre-computation is Hidden:**
Evaluation keys must be "reorganized in the corresponding pattern" (Section 4.3.2). This preprocessing happens once per key, but evaluation keys change per rotation index in HROTATE. For CNNs with many unique rotation indices, this reorganization cost recurs. The paper doesn't quantify this overhead.

**5. The FP64 "Discovery" Has Caveats:**
The 53-bit mantissa trick requires one operand be split to ≤12 bits while the other stays full precision. This works for BConv (constant factors can be pre-split), but for IP, both operands are limbs. The paper doesn't fully explain how IP handles this—it seems both operands must be split, giving 2×2=4 partial products, not the "3" claimed for BConv.

**6. The "Practical" Claim is Arguable:**
Table 5 shows PackBootstrap takes 0.24 seconds and ResNet-20 inference takes 12.03 seconds. Plaintext ResNet-20 inference takes <10ms. The 1000× overhead means FHE remains impractical for latency-sensitive applications despite this speedup. Meanwhile, ASIC accelerators (Craterlake, SHARP) claim orders of magnitude better efficiency—the paper argues GPUs are "practical" but doesn't quantify this gap.

**7. Double Scaling Adds Hidden Overhead:**
Section 2.1 notes DS is "essential when WordSize is smaller than 36 bits" and "consumes two ciphertext levels." Table 5 footnotes that TensorFHE was reimplemented with DS since "absence leads to precision loss." This modified baseline comparison may not be entirely fair to the original TensorFHE.

**8. Reproducibility Concerns:**
Despite extensive evaluation, there's no link to source code or reproducibility artifacts. For GPU work where CUDA version, driver version, and compile flags significantly affect performance, this is a notable omission. Additionally, no error bars or variance reporting appears in any figure or table.