# Paper Deconstruction: Neo - FHE Acceleration using Tensor Cores

## Q1: Whiteboard Explanation

Let me draw this out for you like I'm explaining it to a first-year grad student who just wandered into my office.

**The Problem They're Solving:**
Fully Homomorphic Encryption (FHE) lets you compute on encrypted data without decrypting it first—magical for privacy, but computationally brutal. The CKKS scheme they're targeting is particularly nasty: you're multiplying huge polynomials (degree N=65536) with coefficients that need 36+ bits of precision, and you're doing this millions of times.

The bottleneck? A beast called **KeySwitch**. Every time you multiply ciphertexts or rotate data, you need to run KeySwitch to maintain decryptability. It dominates runtime because it involves:
1. **BConv (Base Conversion)**: Converting polynomials between different modulus representations
2. **NTT (Number Theoretic Transform)**: FFT's integer cousin for polynomial multiplication
3. **IP (Inner Product)**: Multiplying with huge evaluation keys and accumulating

**The Core Insight (drawn on whiteboard):**

```
BEFORE Neo:                          AFTER Neo:
BConv: loop over α' outputs          BConv: reshape data into matrix
  loop over α inputs                   single matrix multiply: (BS×N) × α × α'
    scalar multiply & accumulate       
    [reads same data α' times!]        [each datum read ONCE]

IP: loop over β̃ outputs              IP: reshape into (BS×N) × β × β̃
  loop over β inputs                   single matrix multiply
    element-wise multiply              [data reused via matmul]
    accumulate
```

The magic trick: They noticed that these "multiply many things and accumulate" patterns ARE matrix multiplication in disguise. By reshaping the data layout, they convert element-wise operations with terrible data reuse into dense matrix multiplications.

**Why Tensor Cores?**
NVIDIA's A100 has Tensor Cores (TCUs) that do matrix multiply-accumulate blazingly fast. Prior work (TensorFHE) used INT8 TCU components, but here's the dirty secret: FHE needs 36-bit integers minimum. Splitting 36 bits into INT8 chunks requires 5 splits per operand, giving you 25 partial matrix multiplies! 

Neo's insight: Use FP64 TCU components instead. FP64 has 53 bits of mantissa precision, so you only need 3 splits for 36-bit numbers. That's 9 partial products vs 25—nearly 3× fewer operations (Figure 3 shows this clearly: FP64 beats INT8 at 36-bit and 48-bit widths).

**The KLSS Method Trick:**
Prior KeySwitch (Hybrid method) works in the full ring R_PQ. KLSS works in a smaller "extended ring" R_T with selectable WordSize_T. This reduces total complexity (Table 2), BUT there's a trade-off: larger WordSize_T means fewer limbs but more expensive per-operation due to Booth decomposition on TCUs. Neo finds the sweet spot at WordSize_T=48 (Section 6.3, Figure 16).

## Q2: The Key Insight

**The Real Contribution:**
Neo's actual delta over prior work boils down to one architectural observation weaponized across multiple kernels:

*The FP64 components of Tensor Cores are criminally underutilized for FHE, and several FHE kernels that look like element-wise operations are actually disguised matrix multiplications waiting to be exploited.*

Let me be precise about the mechanism:

**For BConv and IP:** The original algorithms (Algorithms 1 and 3) perform scalar or element-wise multiplications where the same data is read repeatedly—α' times for BConv, β̃ times for IP. By reordering tensor dimensions (Algorithm 2: α×BS×N → N×BS×α), they expose the accumulation dimension as the K-dimension of matrix multiplication. Now each coefficient is loaded once and reused throughout the matrix operation.

**For TCU component selection:** Prior work (TensorFHE) used INT8 TCU components because they have higher raw throughput (624 TFLOPS vs 19.5 TFLOPS for FP64). But FHE's large integers destroy this advantage:
- 36-bit integers → 5 INT8 splits → 25 partial products
- 36-bit integers → 3 FP64 splits (12-bit chunks into 53-bit mantissa) → 9 partial products

Figure 3 is the smoking gun: at WordSize=36, FP64 achieves 1.65× the speed of INT8. This is counterintuitive—the "slower" unit wins.

**The fragment shape mismatch is real:** INT8 fragments are 16×16×16, but BConv matrices are α×α' where α=4, α'=8 (Section 4.5.2). That's massive padding waste (Figure 11 shows only 25% valid computation for INT8 vs 100% for FP64's 8×8×4 fragments).

**What's NOT the contribution:**
- The KLSS KeySwitch method itself (from [28])
- Radix-16 NTT (from SHARP [25])
- The basic idea of using TCUs for FHE (TensorFHE [12])

Neo's contribution is the *synthesis*: transforming BConv/IP into matrix form (new), choosing FP64 over INT8 (new), and co-optimizing KLSS parameters for this GPU implementation (new).

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

**1. Real Application Benchmarks (Table 5):**
Unlike papers that stop at microbenchmarks, Neo evaluates on actual FHE applications: Bootstrapping, HELR (logistic regression training), and ResNet-20/32/56 inference. The 3.28× speedup over TensorFHE's best configuration on ResNet-56 is compelling for real workloads.

**2. Fair Comparisons with Parameter Alignment:**
They correctly note that comparing FHE implementations requires matching parameters. Table 4 shows multiple parameter sets, and they compare against HEonGPU (Set-D/E) with HEonGPU's own parameters. The 19.9% advantage over HEonGPU is honest—it's not a 10× claim from cherry-picked configs.

**3. Excellent Ablation Study (Figure 14):**
They decompose the speedup into four components: KLSS method, dataflow optimization, Radix-16 NTT, and FP64 TCU. This lets you understand where the gains come from. For PackBootstrap, +KLSS gives ~25% improvement, +dataflow gives another ~15%, +Radix-16 NTT gives ~25%, and +FP64 TCU adds the final ~10%.

**4. Memory Transfer Analysis (Figures 2 and 15):**
They don't just claim "better data reuse"—they quantify it. Figure 15 shows BConv data transfer drops by ~75% and IP drops by ~80% after optimization. This is verifiable evidence for their data layout claims.

### Weaknesses:

**1. Single GPU Evaluation Only:**
All experiments use NVIDIA A100 40GB. No comparison on:
- Other NVIDIA GPUs (V100, H100)
- AMD GPUs with matrix cores
- Different A100 variants (80GB, PCIe vs SXM)

The FP64 TCU throughput ratio differs across GPU generations. Would the INT8 vs FP64 tradeoff flip on H100?

**2. BatchSize Sensitivity is Concerning (Figure 17):**
At BatchSize=8, performance is 2× worse than BatchSize=128. Many real applications (interactive, latency-sensitive) can't batch 128 ciphertexts. The paper acknowledges this but doesn't offer solutions. For single-ciphertext operations, the speedups likely evaporate.

**3. Limited Level Sensitivity Analysis:**
Figure 12 shows the "valid proportion" of matrix operations varies dramatically with level l. At l=5, IP's valid proportion drops to ~25%. Table 8 only shows KeySwitch at one level. What's the speedup at low multiplicative depth where many practical computations live?

**4. Power and Energy Completely Ignored:**
Not a single power measurement. TCU utilization is high, but what's the Watts? For cloud deployment (their target scenario), energy efficiency matters. The A100 TDP is 400W—are they hitting it? Is the 3.28× speedup also a 3.28× energy improvement, or are they burning more power?

**5. Memory Capacity Limits Glossed Over:**
Section 4.6 mentions BatchSize=128 is limited by "GPGPU memory capacity." The A100 has 40GB HBM2e. What's the actual memory footprint? At N=65536, L=35, each ciphertext is ~8MB. With evaluation keys, how many ciphertexts fit? They don't quantify this constraint.

**6. No Comparison with ASIC Accelerators:**
Table 5 has a CPU baseline from Craterlake [40], but they don't compare against Craterlake itself or other ASIC designs (BTS, SHARP, Taiyi). They argue GPUs are "more practical," but the raw performance gap with ASICs goes unstated.

**7. Reproducibility Concerns:**
They mention "GCC 8.4, CUDA 11.3, PyTorch 1.12, Cupy 11.5" but don't provide code or detailed implementation specifications. The data layout transformations are described algorithmically, but the actual CUDA kernel implementations are not detailed.

## Q4: What the Authors Didn't Tell You

### Hidden Assumptions and Limitations:

**1. The KLSS Method Has Numerical Precision Implications:**
The paper breezes past Equation 4's security constraint and the precision implications of KLSS. Working in R_T instead of R_PQ means you're computing in a different ring—what happens to accumulated noise? They mention DS (Double Rescale) is "essential when WordSize is smaller than 36 bits" (Section 2.1), but the interaction between KLSS's extended ring and noise growth isn't analyzed. If KLSS introduces subtle precision issues, applications requiring high accuracy might fail silently.

**2. The 48-bit WordSize_T Choice is GPU-Specific:**
Figure 16 shows WordSize_T=48 is optimal. But this is tuned for A100's FP64 TCU characteristics. On GPUs with different TCU implementations or Booth decomposition costs, the optimal WordSize_T shifts. This "optimal parameter" is not portable.

**3. IP Kernel Sometimes Falls Back to CUDA Cores:**
Section 4.5.3 reveals that when the "valid proportion" drops below 80%, IP matrix multiplication runs on CUDA Cores instead of TCUs. Looking at Figure 12, at l<15, IP is below 80% valid. For applications that spend significant time at low levels (after many rescales), you're not getting TCU acceleration on a major kernel!

**4. The Radix-16 NTT is Borrowed Work:**
Section 4.4 and the Radix-16 NTT optimization come from SHARP [25], an ASIC paper. Neo's NTT contribution is "substituting butterfly operations with matrix multiplications" to fit GPGPU architecture—an adaptation, not an invention. The complexity reduction (2^22 vs 2^25) is SHARP's achievement.

**5. Multi-GPU Scaling is Completely Unexplored:**
For data center deployment, you'd want to scale across multiple GPUs. HE-Booster [45] addressed multi-GPU parallelization, but Neo doesn't compare against it or discuss scaling. Real cloud FHE would use multiple A100s—how does Neo's memory layout transformation interact with multi-GPU communication?

**6. The Comparison with HEonGPU is Apples-to-Oranges:**
Table 5 shows Neo (Set-C) at 12.03s vs HEonGPU (Set-E) at 16.42s for ResNet-20. But Set-C uses WordSize=36 while Set-E uses WordSize=60! Higher WordSize means larger ciphertexts and more computation. When they compare Neo Set-D (WordSize=60) against HEonGPU Set-E (also WordSize=60), the gap shrinks to 13.39s vs 16.42s—only 18% improvement, not 27%. The headline 19.9% average advantage includes this parameter mismatch.

**7. Kernel Fusion's Memory Overhead:**
Section 4.6 mentions kernel fusion uses "shared memory to minimize access to global memory." But shared memory on A100 is limited (164KB per SM configurable). The split-reorder-multiply-merge pipeline for large matrices might overflow shared memory, forcing spills. They don't discuss this constraint.

**8. Evaluation Keys Are Memory Monsters:**
Table 2 shows IP requires β̃×β×α' polynomial keys. With their parameters, that's thousands of polynomials, each 8MB+. These evaluation keys must be loaded from HBM for each KeySwitch. They claim "reduced global memory transfer" (Figure 15), but the evaluation keys themselves are the elephant in the room. The IP reordering optimization helps, but the absolute memory footprint isn't disclosed.

**9. The 3.28× Speedup Hides Kernel-Level Variance:**
Table 7 shows NTT improved 3.74×, BConv 2.74×, IP 2.60×. But KeySwitch isn't just these kernels—there's also Recover Limbs, Mod Down, etc. (Figure 5). The overall 3.28× application speedup is less than NTT's kernel speedup because other kernels don't benefit equally.

**10. Security Parameter λ Claims Need Verification:**
Table 4 lists λ≥128 for most parameter sets, but λ≥98 for Set-H (used in CPU baseline). Different security levels mean different hardness assumptions. The paper doesn't discuss whether their KLSS implementation maintains the theoretical security guarantees across all parameter choices.

### What This Means for Your Research:

If you're building on this work, understand that:
1. Performance gains are highly parameter-dependent (BatchSize, level l, WordSize_T)
2. The FP64 vs INT8 advantage is architecture-specific and may not transfer to future GPUs
3. The KLSS method reduces algorithmic complexity but adds implementation complexity
4. For low-batch, low-level operations typical of interactive applications, expect significantly smaller speedups than the headline numbers