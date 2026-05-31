# Neo: Deconstruction Report

## The "No-BS" Summary

This paper accelerates **CKKS homomorphic encryption on NVIDIA A100 GPUs** by finally figuring out how to use the **FP64 Tensor Cores** instead of just the INT8 components that TensorFHE used. The core trick: they reformulate two historically ugly kernels—**Base Conversion (BConv)** and **Inner Product (IP)**—from element-wise multiplications into matrix multiplications, then run those on the FP64 units. They also adopt the **KLSS KeySwitch method** (a 2023 algorithm from CRYPTO) which shifts computation to a different modulus space with selectable word width, and they use a **Radix-16 NTT** to reduce butterfly complexity.

**Real speedup:** 3.28× over TensorFHE on the same A100 GPU, using the same security parameters. The baseline is reasonable (TensorFHE is the prior state-of-the-art GPU implementation from HPCA'23). They benchmark actual applications (ResNet inference, logistic regression, bootstrapping) rather than just isolated operations.

**What they actually accelerated:** The KeySwitch operation, which dominates HMULT and HROTATE. They did NOT tackle bootstrapping's fundamental latency—they just made the leveled operations faster.

---

## The Core Mechanism: A Whiteboard Explanation

### The Problem They're Solving

In CKKS, every time you do a homomorphic multiplication (HMULT) or rotation (HROTATE), you need a **KeySwitch** operation to keep the ciphertext decryptable. KeySwitch is expensive because it involves:

1. **Mod Up (BConv):** Convert polynomials from one RNS base to another—essentially, you have α input limbs and need to produce α' output limbs. Each output coefficient is a weighted sum of input coefficients.

2. **NTT:** Transform polynomials to evaluation form.

3. **Inner Product (IP):** Multiply your decomposed ciphertext by massive evaluation keys and accumulate.

4. **INTT + Mod Down:** Transform back and reduce.

The old way (TensorFHE) did BConv and IP as **element-wise multiplications**—meaning each coefficient gets read from global memory multiple times (once per output limb in BConv, once per evaluation key in IP). This is memory-bandwidth murder.

### The Neo Insight

**Observation 1:** BConv is secretly a matrix multiplication. If you have α input limbs and need α' output limbs, and each output is a linear combination of inputs weighted by base conversion factors, then:
- Reshape your input from (α × BatchSize × N) to (N × BatchSize × α)
- Your base conversion factors form an (α × α') matrix
- Now you have N×BatchSize independent matrix-vector products, which you can batch into a matrix multiplication

**Observation 2:** IP is also secretly a matrix multiplication. You have β groups of ciphertext limbs, and you need to multiply each by β̃ evaluation keys and accumulate across the β dimension. Reshape the data so the β dimension becomes the K dimension of a matrix multiply.

**Observation 3:** The A100's FP64 Tensor Cores are underutilized. TensorFHE used INT8 Tensor Cores for NTT, which requires splitting 36-bit integers into 5 chunks of 8 bits each, leading to 25 partial products per multiplication. But FP64 has 53 bits of mantissa precision—you can represent integers up to 2^53 exactly. For 36-bit coefficients, you only need to split into 3 chunks of 12 bits, giving 3 partial products instead of 25.

**Observation 4:** The KLSS KeySwitch method (from CRYPTO'23) lets you choose the word width of the intermediate computation space (WordSize_T). Bigger WordSize_T = fewer limbs in the intermediate space = lower algorithmic complexity. But bigger WordSize_T = more Booth complexity on the hardware. The sweet spot on A100 is **48 bits** for WordSize_T.

### The Data Layout Trick

The key to making this work is **data layout transformation**. Originally, limbs are stored contiguously (all N coefficients of limb 0, then all N coefficients of limb 1, etc.). Neo reorders to make the α dimension contiguous—so when you read a cache line, you get the same coefficient position across all α limbs. This enables coalesced memory access for the matrix multiplication.

```
Original: [limb0: c0,c1,...,c_{N-1}][limb1: c0,c1,...,c_{N-1}]...
Neo:      [position0: limb0,limb1,...,limb_{α-1}][position1: ...]...
```

### The Radix-16 NTT

Standard four-step NTT does two matrix multiplications of size N^{1/2} × N^{1/2}. Radix-16 NTT decomposes further into four matrix multiplications of size 16×16, reducing total complexity from O(N^{3/2}) to O(N × 16^2 × 4) = O(N × 1024). For N=2^16, this is a factor of 8 reduction in matrix multiplication complexity.

---

## The Critique

### Why It Got Into ISCA

1. **The FP64 Tensor Core insight is genuinely novel.** Everyone assumed INT8 was the way to go because of raw TOPS. This paper shows that for FHE's specific bit-width requirements, FP64's lower Booth complexity wins. This is the kind of "obvious in hindsight" observation that makes good architecture papers.

2. **They actually reformulated BConv and IP as matrix multiplications.** Prior work (TensorFHE) only used Tensor Cores for NTT. Extending to BConv and IP required algorithmic restructuring, not just kernel tuning.

3. **The evaluation is honest.** They compare against TensorFHE with the same security parameters, they test real applications (not just microbenchmarks), and they acknowledge that their IP kernel falls back to CUDA Cores when the matrix dimensions don't fit Tensor Core fragments well (the 80% utilization threshold).

4. **They adopted KLSS.** This is a recent algorithmic advance (CRYPTO'23), and they're the first to implement it on GPU. The paper shows how to navigate the algorithm-hardware tradeoff (WordSize_T selection).

### Where It's Weak

1. **No bootstrapping latency breakdown.** They benchmark "PackBootstrap" but only report total time. Bootstrapping is dominated by slot-to-coefficient and coefficient-to-slot transformations, which involve many rotations. How much of the 3.28× speedup survives in a bootstrapping-heavy workload? The ResNet numbers suggest it does, but the paper doesn't isolate this.

2. **The 80% utilization threshold for IP is hand-wavy.** They say "experimentally, performance on TCUs surpasses CUDA Cores only when valid proportion exceeds 80%." Where does this number come from? Is it stable across different batch sizes? This feels like a magic constant.

3. **Memory capacity limits BatchSize.** They use BatchSize=128, but acknowledge this is limited by A100's 40GB VRAM. For real datacenter deployments with many concurrent clients, this could be a bottleneck. They don't discuss multi-GPU scaling.

4. **The KLSS method requires different evaluation keys.** The paper glosses over key generation time and key storage. KLSS evaluation keys are structured differently than Hybrid method keys. How much memory do they consume? Is key generation amortized?

5. **Security parameter Set-H (used for CPU baseline) has λ≥98, not λ≥128.** This is buried in Table 4. The CPU numbers from 100x paper use weaker security. Not a fair comparison.

6. **No comparison to ASIC accelerators.** They argue GPUs are more practical, but don't quantify the gap. CraterLake and BTS achieve orders of magnitude better performance—how does Neo compare in performance-per-watt or performance-per-dollar?

7. **Double Rescale (DS) requirement is mentioned but not analyzed.** They say DS is "essential when WordSize < 36 bits" and that it "significantly affects execution conditions and performance." But their main results use WordSize=36, so when does DS actually kick in? The Set-F and Set-G results use L=23 specifically to avoid DS overhead—this feels like cherry-picking.

---

## Discussion Questions

### Question 1: The Booth Complexity Crossover

The paper shows FP64 beats INT8 for 36-bit and 48-bit integers. But what happens at 60-bit (their Set-D/E parameters)? At 60 bits, FP64 needs 4 chunks (60/15 bits per chunk to stay under 53-bit precision after accumulation), giving 16 partial products. INT8 needs 8 chunks, giving 64 partial products. The ratio is still 4×, but the absolute overhead is higher. 

**Ask yourself:** Why did they choose WordSize_T=48 as optimal? Is this because 48 bits happens to split evenly into 4×12-bit chunks for FP64, or is there a deeper reason? What happens on future GPUs with different Tensor Core configurations (e.g., FP32 Tensor Cores with 24-bit mantissa)?

### Question 2: The Memory Bandwidth Wall

Figure 2 shows BConv and IP dominate memory transfer in KeySwitch. But after their optimization, what's the new bottleneck? They reduced memory transfers by improving data reuse, but they didn't increase memory bandwidth. 

**Ask yourself:** At what BatchSize does Neo become memory-bound rather than compute-bound? The paper shows performance improves monotonically with BatchSize up to 128—but is this because larger batches amortize kernel launch overhead, or because larger batches better utilize memory bandwidth? What would happen on an H100 with HBM3?

### Question 3: The KLSS vs. Hybrid Tradeoff

Table 2 shows KLSS has higher IP complexity (β̃×β×α' vs. 2β(l+α)) but lower Mod Up complexity. The paper claims "judicious parameter selection enables KLSS to achieve lower overall complexity."

**Ask yourself:** Under what parameter regimes does KLSS actually lose to Hybrid? The paper only shows KLSS winning, but Equation 4 imposes a security constraint on α'. If you need very high security (large β, large N), does α' blow up and make KLSS worse? The paper doesn't explore this boundary.

---

## Contextual Fit

**Relation to F1 (MICRO'21):** F1 introduced the compute cluster model for FHE with specialized NTT units. Neo doesn't have specialized hardware—it's pure software on commodity GPUs. But Neo's insight about reformulating operations as matrix multiplications could inform future ASIC designs (why build custom BConv units when you can use matrix engines?).

**Relation to CraterLake (ISCA'22):** CraterLake is "functionally complete"—it handles bootstrapping end-to-end. Neo is still doing leveled FHE with occasional bootstrapping. The 3.28× speedup over TensorFHE is nice, but CraterLake achieves 5,000× over CPU. The gap between GPU and ASIC remains enormous.

**Relation to SHARP (ISCA'23):** Neo directly builds on SHARP's Radix-16 NTT and Double Rescale techniques. SHARP identified the memory bandwidth wall; Neo addresses it through data reuse in matrix multiplications. This is a good example of architecture insights (SHARP) enabling software optimization (Neo).

**Relation to ARK (HPCA'22):** ARK focused on runtime key generation to reduce memory footprint. Neo doesn't address key storage—they assume keys fit in memory. For very deep computations requiring many rotation keys, ARK's approach might be necessary even on GPU.

**The bigger picture:** Neo represents the "squeeze more juice from commodity hardware" approach. It's valuable for near-term deployment, but it doesn't change the fundamental reality that FHE needs 10,000× more compute than plaintext. The ASIC accelerators are chasing that 10,000×; Neo is chasing 3×. Both are necessary—ASICs for the long term, GPUs for today.