# Paper Deconstruction: Neo — FHE Acceleration using Tensor Core

---

## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Forget the jargon for a moment.

**The Problem:** Fully Homomorphic Encryption (FHE) lets you compute on encrypted data without ever decrypting it—which is cryptographic magic for privacy. The catch? It's *brutally* slow, often 10,000x slower than plaintext computation. The main culprit is a core operation called **KeySwitch**, which is essentially how you "refresh" a ciphertext after performing multiplications so it doesn't become garbage noise.

**What KeySwitch Actually Does:** Imagine you have a polynomial (a fancy list of ~65,000 numbers). KeySwitch involves:
1. **BConv (Base Conversion):** Taking those numbers and converting them between different modular number systems—like converting dollars to euros to yen and back, but for math.
2. **NTT (Number Theoretic Transform):** A Fourier-transform-like operation that converts polynomials so multiplications become cheaper.
3. **IP (Inner Product):** Multiplying your data by giant "evaluation keys" (think of them as cryptographic correction factors).

**The Core Insight of Neo:** The authors looked at BConv and IP and realized: "Hey, these operations are currently done as *element-wise* multiplications—each number gets multiplied one at a time, which means the GPU fetches the same data from slow global memory over and over." (Section 3.3, Figure 2 shows BConv and IP account for 43.4% and 41.8% of memory traffic at level 35.)

Their fix: **Reshape these element-wise operations into matrix multiplications.** Why? Because matrix multiplication has much better data reuse—you load data once, and it participates in many calculations before being evicted. And critically, modern GPUs have **Tensor Cores (TCUs)**—specialized hardware designed to churn through matrix multiplications at ridiculous speeds.

**The Second Trick—FP64 over INT8:** Prior work (TensorFHE) used the INT8 (8-bit integer) Tensor Cores. But FHE uses 36-bit or larger integers. Splitting a 36-bit number into five 8-bit chunks creates a computational explosion (25 partial matrix multiplies via Booth's algorithm). Neo instead uses the **FP64 (64-bit floating-point)** Tensor Cores. FP64 has 53 bits of mantissa precision—enough to hold a 36-bit integer exactly. Now you only need 3 partial multiplies instead of 25. Figure 3 shows FP64 is 1.65x faster than INT8 for 36-bit, and 1.74x faster for 48-bit.

**The Third Trick—KLSS Method:** Prior GPU implementations used the "Hybrid" KeySwitch algorithm. Neo adopts a newer algorithm called KLSS (Kim-Lee-Song-Song), which lets you choose a separate `WordSize_T` for internal computations. This creates a knob to trade off algorithmic complexity versus hardware complexity. The paper finds the sweet spot at `WordSize_T=48` (Figure 16).

**Result:** 3.28x speedup over TensorFHE on real applications (Table 5).

---

## Q2: The Key Insight

**The Delta—What's Actually New:**

The *singular* contribution of this paper is **transforming BConv and IP from element-wise operations into matrix multiplications, and then executing them on the FP64 Tensor Cores instead of INT8 Tensor Cores or CUDA cores.**

Let me be precise about what's *not* new:
- Using GPUs for FHE? Not new (100x, TensorFHE, HE-Booster).
- Using Tensor Cores for NTT? Not new (TensorFHE did this in HPCA'23).
- The KLSS KeySwitch algorithm? Not new (published in CRYPTO'23 by Kim et al.).
- Radix-16 NTT? Borrowed from SHARP (ISCA'23).

**What *is* new:**
1. **Algorithm 2 (Section 4.2.1):** Reformulating BConv as a matrix multiplication of shape `(BatchSize × N) × α × α'`. Previously, each coefficient was accessed `α'` times from global memory; now it's accessed once (Algorithm 1 vs. Algorithm 2).

2. **Algorithm 4 (Section 4.2.2):** Reformulating IP similarly—coefficients were read `β̃` times; now once.

3. **FP64 Tensor Core Usage (Section 3.4, Section 4.5):** The key architectural insight is in Figure 1 and Figure 3. TensorFHE used INT8 TCUs, but the fixed fragment shapes (16×16×16, 32×8×16, 8×32×16) are poorly matched to the dimensions of FHE operations. For BConv with α=4 and α'=8, using INT8 means 75% of the computation is wasted padding (Figure 11). FP64's 8×8×4 fragment has *no* padding for these dimensions—100% utilization.

4. **The WordSize_T Trade-off Analysis (Section 3.2, Figure 16):** The authors found that 48-bit is the optimal internal precision—smaller means too many limbs (high algorithmic complexity); larger means too much Booth overhead on TCUs.

**The Magic Trick:** The paper exploits a mismatch in prior work. TensorFHE only accelerated NTT on TCUs. But BConv and IP *also* have the structure of "multiply many elements by constants and sum"—which is literally the definition of matrix multiplication. The insight is that data layout reorganization (Figures 6, 7, 8) exposes this latent structure.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Solid Baselines:** The authors compare against TensorFHE (HPCA'23, the prior state-of-the-art GPU FHE system) and HEonGPU (a newer non-TCU GPU library). They don't just beat a straw-man HuggingFace-style implementation. Table 5 shows 3.28x over TensorFHE's best configuration and 19.9% average over HEonGPU.

2. **Real Applications:** They evaluate on PackBootstrap (the core FHE refresh operation), HELR (logistic regression training), and ResNet-20/32/56 inference. These aren't toy benchmarks—bootstrapping and neural network inference are the canonical FHE workloads.

3. **Ablation Study:** Figure 14 shows the contribution of each optimization step: KLSS adoption, dataflow optimization, Radix-16 NTT, and FP64 TCU. Each step contributes measurably. This is good scientific practice.

4. **Memory Analysis:** Figure 15 quantifies the memory transfer reduction—after optimization, BConv and IP require dramatically less global memory traffic, validating the data reuse claim.

5. **Sensitivity Study:** Table 8 explores the `d_num` and `α̃` parameter space; Figure 16 justifies the `WordSize_T=48` choice; Figure 17 shows BatchSize scaling. This builds confidence that the results aren't cherry-picked.

### Weaknesses

1. **The Baseline Trap—TensorFHE Handicap:** The authors *reimplemented* TensorFHE with Double Scaling (DS) because "the absence of DS in TensorFHE leads to precision loss" (Table 5 footnote). This is fair for correctness, but it means they're comparing against their *own reimplementation* of TensorFHE, not the original artifact. We have to trust their reimplementation is faithful and not accidentally slower.

2. **Single GPU, Single Vendor:** All experiments are on NVIDIA A100. The Tensor Core architecture is NVIDIA-specific. The FP64 TCU strategy might not transfer to AMD's Matrix Cores (CDNA) or Intel's AMX. The paper makes no claims about portability.

3. **No Accuracy/Correctness Evaluation:** FHE is approximate (CKKS scheme). The paper never reports precision loss, noise growth, or decryption accuracy for any workload. Table 5 shows *timing* but not *correctness*. For HELR, what's the final model accuracy? For ResNet, what's the top-1 accuracy on CIFAR-10? This is a significant omission.

4. **HEonGPU Comparison Uses Different Parameters:** Neo uses Set-C (WordSize=36, KLSS with WordSize_T=48); HEonGPU uses Set-E (WordSize=60, Hybrid method). The security parameters are the same (λ≥128), but the operational characteristics differ. A same-configuration comparison would be more apples-to-apples.

5. **BatchSize=128 Required for Best Performance:** Figure 17 shows that at BatchSize=8 or 16, performance degrades significantly (1.5-2x slower). This is fine for bulk inference but problematic for interactive, single-query use cases.

6. **No Latency Breakdown:** The paper shows throughput (amortized time per operation) but doesn't discuss first-operation latency or memory footprint. Evaluation keys can be enormous (Section 2.3 mentions `β̃ × β × α'` polynomial keys)—how much GPU memory do they consume?

---

## Q4: What the Authors Didn't Tell You

1. **The FP64 Precision Sleight-of-Hand:** The paper claims FP64 TCUs are "sufficient to represent integers up to 2^53 without loss of precision" (Section 3.4). This is true. But what they don't emphasize is that intermediate products can exceed this. In Algorithm 2, the accumulation across K=16 dimensions can produce values up to `2^36 × 2^12 × 16 = 2^52`. They carefully engineer this to stay under 2^53, but this constraint *limits* the maximum WordSize they can support. If you wanted WordSize=54 bits, this entire approach breaks.

2. **KLSS Algorithm Complexity is Not Free:** The paper touts KLSS as having lower complexity (Table 2), but it introduces *additional* steps: "Recover Limbs" (complexity 2α'(l+α)) doesn't exist in Hybrid. The paper handwaves this by selecting parameters where "judicious parameter selection enables KLSS method to achieve a lower overall complexity." In other words, KLSS is only better if you carefully tune parameters—which they did.

3. **The "Valid Proportion" Problem:** Figure 12 shows that as ciphertext level `l` decreases, the "valid proportion" of IP matrix multiplications drops below 80%. Below this threshold, Neo falls back to CUDA Cores instead of TCUs (Section 4.5.3). This means **at low levels, Neo's TCU advantage disappears**. For applications that spend significant time at low levels (e.g., after many multiplications before bootstrapping), the speedup degrades.

4. **Pre-computation is Hidden:** The paper mentions that evaluation keys must be "reorganized in the corresponding pattern" (Section 4.3.2). This reorganization is preprocessing that happens once per key. But evaluation keys change per rotation index in HROTATE. If you have many unique rotation indices (common in CNNs), you pay this reorganization cost repeatedly. The paper doesn't quantify this.

5. **Memory Capacity Ignored:** The paper acknowledges that "due to the limitations of GPGPU memory capacity, BatchSize cannot be increased indefinitely" (Section 6.3), but never states the actual memory consumption. An A100 has 40GB; with BatchSize=128 and N=65536 and L=35, the ciphertext storage alone approaches the limit. Multi-GPU scaling (mentioned as HE-Booster's contribution) is out of scope.

6. **No Comparison to ASIC/FPGA:** The Related Work (Section 7) discusses Craterlake, FAB, Taiyi, SHARP—all custom accelerators. CPU data from Craterlake is cited (Table 5), but no direct FHE accelerator comparison. This makes sense (ASICs are apples-to-oranges with GPUs), but it obscures how far GPUs still are from custom hardware. Craterlake achieves ~4000x over CPU; Neo achieves ~100x. The gap is large.

7. **The "3.28x" Number is Cherrypicked:** The abstract claims "3.28x over TensorFHE," but Table 5 shows this is vs. *TensorFHE's best configuration* (Set-C). Against Set-A (Neo's comparable config), it's 3.42x for HELR but only 2.79x for PackBootstrap. The 3.28x is an average—but averages can hide variance.

8. **Double Scaling Adds Overhead Not Accounted For:** Section 2.1 notes that "DS is an essential operation when the WordSize is smaller than 36 bits." DS consumes two ciphertext levels, which affects the multiplicative depth budget. The paper uses WordSize=36, right at the boundary. If WordSize drops, DS overhead increases.