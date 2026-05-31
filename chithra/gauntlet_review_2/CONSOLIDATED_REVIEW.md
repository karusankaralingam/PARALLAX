# Consolidated Gauntlet Review

---

# Q1: Whiteboard Explanation


## The Whiteboard Explanation

Let me walk you through what this paper actually does, stripped of the cryptographic jargon.

**The Problem Setup:**
Fully Homomorphic Encryption (FHE) lets you compute on encrypted data. The CKKS scheme they're accelerating works with polynomials of degree N=65,536, where each coefficient is a 36-60 bit integer. The killer operation is **KeySwitch** - it's called constantly and involves:
1. **BConv (Base Conversion):** Transform polynomials between different modular representations
2. **NTT (Number Theoretic Transform):** FFT-like operation for fast polynomial multiplication  
3. **IP (Inner Product):** Multiply-accumulate with large evaluation keys

The baseline approach (TensorFHE) uses the **INT8 Tensor Cores** on A100 GPUs. They split 32-bit integers into 8-bit chunks, do matrix multiplications, then reassemble. Sounds clever, but there's a catch.

**The Data Flow Reality:**
```
Input: α polynomials × BatchSize × N coefficients
       (4 levels × 128 ciphertexts × 65536 coefficients)
       
BConv: Each coefficient gets multiplied by α' conversion factors
       Original: Load coefficient α' times → terrible reuse
       
IP: Each polynomial multiplied by β̃×β evaluation keys  
    Original: Load coefficient β̃ times → terrible reuse
```

The paper's insight: These are really **matrix multiplications in disguise**. BConv is `(BatchSize×N) × α × α'` and IP is `(BatchSize×N) × β × β̃`.

---

## The 'Aha!' Moment

The clever part is **not** using INT8 Tensor Cores. Here's why:

**The INT8 Fragment Shape Problem:**
- A100 INT8 Tensor Cores require fragments of 16×16×16, 32×8×16, or 8×32×16
- BConv has dimensions α=4, α'=8 (from their KLSS parameters)
- Mapping 4×8 to 32×8×16 means **75% of computation is padding waste**

**The FP64 Revelation (Figure 11):**
- FP64 Tensor Cores use 8×8×4 fragments
- 4×8 maps to 8×8×4 with **zero padding**
- FP64 mantissa is 53 bits → can represent integers up to 2^53 exactly

**The Booth Complexity Trade-off:**
For 36-bit integers:
- INT8 path: Split into 5 chunks → 5×5=25 partial products
- FP64 path: Split into 3 chunks (12 bits each) → 3 partial products

For 48-bit integers:
- INT8 path: 6 chunks → 36 partial products  
- FP64 path: 4 chunks → 4 partial products (since 48/12=4, and 2^36 × 2^12 × 16 < 2^53)

**Figure 3 shows the punchline:** FP64 is 1.65× faster than INT8 for 36-bit, 1.74× faster for 48-bit.

---

## The Skeptic's Check

### Hidden Overhead #1: Data Rearrangement
Look at Algorithms 2 and 4. Before matrix multiplication:
- BConv: Transpose from `α×BatchSize×N` to `N×BatchSize×α`
- IP: Transpose from `β×α'×BatchSize×N` to `N×α'×BatchSize×β`

After matrix multiplication: Transpose back.

They claim this is "fused" into the kernel using shared memory, but Figure 13 shows preprocessing/postprocessing is **not negligible** - it's visible in the breakdown. The paper doesn't quantify this overhead separately.

### Hidden Overhead #2: The KLSS Method Itself
They switched from the "Hybrid" KeySwitch to "KLSS" method. Table 2 shows KLSS adds a **Recover Limbs** step that Hybrid doesn't have: `2α'(l+α)` complexity.

The KLSS method also requires:
- New parameter `WordSize_T` (they pick 48 bits)
- New parameter `α'` constrained by security (Equation 4)
- More evaluation key storage: `β̃×β×α'` polynomial keys

### Hidden Overhead #3: The 80% Threshold Hack
Section 4.5.3 admits: "When the valid proportion calculated from the parameters exceeds 80%, the matrix multiplication steps of IP are mapped to the FP64 components in TCUs; **otherwise, they are mapped to the CUDA Cores**."

This means IP has **two completely different code paths** depending on the current ciphertext level `l`. As `l` decreases during computation, `β` and `β̃` shrink, and the valid proportion drops (Figure 12). They're dynamically switching between TCU and CUDA Core execution mid-application.

### Hidden Overhead #4: Memory Capacity Limits BatchSize
From Section 6.3: "Due to the limitations of GPGPU memory capacity, BatchSize cannot be increased indefinitely."

A100 has 40GB HBM. With N=65536, L=35, WordSize=36:
- One ciphertext ≈ 2 polynomials × 36 levels × 65536 coefficients × 8 bytes ≈ 37MB
- BatchSize=128 → 4.7GB just for input ciphertexts
- Evaluation keys for KLSS: `β̃×β×α'×N×8` bytes per key set

The 128 BatchSize isn't a performance choice - it's a memory constraint.

### The Comparison Fairness Question
Table 5 compares against TensorFHE, but:
- TensorFHE used WordSize<32 bits originally
- They "reimplemented TensorFHE with DS integrated" (footnote ‡)
- HEonGPU comparison uses different parameters (Set-E vs Set-C)

The 3.28× speedup claim is against their own reimplementation of TensorFHE with parameters TensorFHE wasn't designed for.

---

## The Actual Hardware Utilization

**A100 Peak Performance:**
- CUDA Core FP64: 9.7 TFLOPS
- TCU FP64: 19.5 TFLOPS  
- TCU INT8: 624 TFLOPS

**What Neo Actually Uses:**
- FP64 TCU for matrix multiplications (19.5 TFLOPS theoretical)
- CUDA Cores for modular reduction, transposition, scalar operations

The paper never reports achieved TFLOPS or memory bandwidth utilization. Given that:
- NTT is memory-bound (each coefficient touched once per butterfly)
- BConv/IP after transformation are compute-bound matrix multiplications

I'd estimate they're hitting maybe 30-50% of peak FP64 TCU throughput, limited by the preprocessing/postprocessing overhead and the small matrix dimensions (α=4, α'=8 means tiny matrices).

---

## Discussion Question

**Ask yourself:** The paper claims FP64 Tensor Cores are better than INT8 for FHE because of Booth complexity. But what happens when:

1. Future GPUs have larger INT8 fragment shapes that better match FHE parameters?
2. The security requirements force WordSize > 60 bits (exceeding FP64's 53-bit exact integer range)?
3. You need to support multiple FHE schemes (BFV, BGV) that have different coefficient bit-widths?

The "FP64 is better" conclusion is highly specific to: (a) A100 architecture, (b) CKKS scheme, (c) their chosen 36-48 bit WordSize range, and (d) their specific KLSS parameters. This is a point solution, not a general principle.

---

# Q2: The Key Insight


**The one insight that makes everything work:** *Element-wise operations with poor data reuse can be restructured as matrix multiplications with excellent data reuse, if you're willing to pay the data layout transformation cost.*

Here's the whiteboard version:

**Original BConv (Base Conversion):**
```
For each of α' output limbs:
    For each of α input limbs:
        For each of N coefficients:
            output[j][n] += input[i][n] × conversion_factor[i][j]
```
Each input coefficient is read α' times from global memory. Terrible.

**Neo's BConv:**
```
Reshape input from [α × BatchSize × N] to [N × BatchSize × α]
Matrix multiply: [N×BatchSize × α] × [α × α'] = [N×BatchSize × α']
Reshape output back
```
Each coefficient read once. The conversion factors form a small matrix that lives in registers. The Tensor Core handles the accumulation internally.

**Why FP64 beats INT8:** A 36-bit integer split into INT8 chunks requires 5 chunks → 5×5=25 partial products. The same integer stored as FP64 (which has 53 mantissa bits) can be split into 3 chunks of 12 bits → only 3 partial products. The 32× raw throughput advantage of INT8 is overwhelmed by the 8× reduction in partial products.

**The KLSS connection:** The KLSS KeySwitch method lets you *choose* the bit-width of intermediate computations (WordSize_T). Pick 48 bits, and you hit the sweet spot where FP64 Tensor Cores are maximally efficient. This is algorithm-hardware co-design: the algorithm provides a knob, the hardware analysis tells you where to set it.

---

---

# Q3: Evaluation Critique


*adjusts glasses and pulls up the experimental section*

Let me be direct with you: this paper has some solid experimental work, but there are several methodological choices that warrant scrutiny. Let's dissect this systematically.

---

## 1. Methodology Audit

### Benchmark Selection

They evaluate on three workloads:
- **PackBootstrap** - A microbenchmark for the core FHE operation
- **HELR** - Logistic regression on MNIST (binary classification, 3 vs 8)
- **ResNet-20/32/56** - CNN inference on CIFAR-10

**My Assessment:** This is a *reasonable* but *narrow* benchmark suite. Here's what concerns me:

1. **HELR is a toy workload.** Training on 14×14 MNIST images with 196 weights? That's not representative of real privacy-preserving ML. Where's BERT inference? Where's a recommendation system with sparse embeddings? These would stress the memory hierarchy very differently.

2. **ResNet is compute-bound and regular.** CNNs have beautiful, predictable access patterns. FHE on transformers with attention mechanisms would expose whether their data layout optimizations generalize.

3. **No datacenter-scale evaluation.** They batch 128 ciphertexts, but real deployments might need to handle thousands of concurrent queries with different rotation indices. Does their evaluation key management scale?

---

## 2. The "Gotcha" Graphs

### Figure 12 - The Valid Proportion Problem

*This is the most honest graph in the paper, and it reveals a fundamental limitation.*

Look at how the "valid proportion" for IP drops as level `l` decreases:
- At l=35: ~75% valid
- At l=15: ~25% valid  
- At l=5: Essentially unusable on TCU

They acknowledge this by saying "when valid proportion exceeds 80%, map to TCU; otherwise, map to CUDA Cores." But here's the problem: **during Bootstrapping, you spend most of your time at low levels.** The paper doesn't break down what percentage of total execution time is spent at each level.

**Question for you:** If 60% of KeySwitch operations happen at l < 20, how much of their claimed TCU benefit actually materializes in practice?

### Figure 16 - The WordSize Trade-off

Notice how WordSize_T = 48 is optimal, but the difference between 48 and 64 is substantial at high levels. They chose 48 as the "default," but:

- At l=35, WordSize_T=64 would be faster for NTT
- At l=15, WordSize_T=48 wins

**This suggests their "optimal" parameter is actually a compromise that isn't optimal for any specific operating point.** A truly adaptive system would switch WordSize_T based on current level.

---

## 3. The Missing Data

### What I Would Have Loved to See:

1. **Roofline Analysis:** They claim to improve TCU utilization, but where's the roofline model showing how close they are to peak? The A100 has 19.5 TFLOPS FP64 on TCU - what percentage are they achieving?

2. **Memory Bandwidth Utilization:** Figure 2 shows they reduced memory transfer requirements, but did this translate to reduced *time* waiting on memory? What's their achieved bandwidth vs. the A100's 1.5 TB/s?

3. **Energy Consumption:** For privacy-preserving computation in datacenters, energy efficiency matters. They never mention power.

4. **Latency vs. Throughput Trade-off:** All results are throughput-oriented (batch size 128). What happens when you need single-ciphertext latency for interactive applications?

5. **Comparison at Equal Security Levels:** Table 4 shows Set-H has λ≥98 while others have λ≥128. The CPU baseline from Craterlake uses Set-H. **This is not an apples-to-apples comparison.**

---

## 4. Baseline Validity Check

### Is TensorFHE a Fair Baseline?

TensorFHE is from HPCA 2023 - that's recent and reasonable. However:

1. **They had to reimplement TensorFHE with Double Scaling (DS)** because the original "leads to precision loss." This is fair, but it means they're comparing against their own reimplementation, not the published artifact.

2. **HEonGPU comparison is more concerning.** They only beat HEonGPU by 19.9% on average (Table 5), and HEonGPU doesn't use TCU at all. This suggests their TCU optimizations provide diminishing returns compared to good CUDA Core implementations.

### The 3.28× Claim

The abstract says "Neo outperforms TensorFHE by 3.28×." But look at Table 5:
- PackBootstrap: 0.74s → 0.24s = 3.08×
- HELR: 0.78s → 0.22s = 3.54×
- ResNet-20: 38.77s → 12.03s = 3.22×

The 3.28× is cherry-picked from somewhere. More importantly, **against HEonGPU, they only achieve 1.2-1.5× speedup**, which is a much more modest improvement.

---

---

# Q4: What the Authors Didn't Tell You


**Skeleton #1: The 80% Threshold is Arbitrary**
Section 4.5.3 admits that IP kernel execution switches between Tensor Cores and CUDA Cores based on whether "valid proportion exceeds 80%." This threshold is empirically determined and never justified. Figure 12 shows the valid proportion drops below 50% at low ciphertext levels—meaning for much of a bootstrapping operation, they're *not* using Tensor Cores for IP at all.

**Skeleton #2: The BatchSize Dependency**
Figure 17 shows performance at BatchSize=8 is 2× worse than BatchSize=128. But they default to 128 throughout the evaluation. Real-world FHE services may need to handle single queries with low latency. The paper never reports single-ciphertext latency.

**Skeleton #3: The Baseline Modifications**
Footnote ‡ in Table 5: "We reimplemented TensorFHE with DS integrated since the absence of DS in TensorFHE leads to precision loss." They modified their baseline. The 3.28× speedup is against their own reimplementation, not the published TensorFHE artifact.

**Skeleton #4: The Security Parameter Mismatch**
Table 4 shows Set-H (used for CPU baseline from Craterlake) has λ≥98, while their GPU configurations have λ≥128. The CPU comparison is not apples-to-apples.

**Skeleton #5: No Roofline Analysis**
They claim improved TCU utilization but never show a roofline model. What percentage of peak FP64 Tensor Core throughput are they achieving? What's the operational intensity? Are they compute-bound or memory-bound after their optimizations? We don't know.

**Skeleton #6: Memory Numbers Don't Add Up**
Figure 2 shows ~7.8GB memory transfer for KeySwitch at l=35. At 3.2ms execution time (Table 8), that's 2.4 TB/s—exceeding A100's 1.6 TB/s bandwidth. Either the numbers are per-batch (making per-operation ~61 GB/s), or there's cache reuse they're not quantifying, or the numbers are theoretical. The paper doesn't clarify.

---
