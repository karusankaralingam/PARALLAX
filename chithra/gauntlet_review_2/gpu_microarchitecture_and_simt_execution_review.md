# Neo: Towards Efficient FHE Acceleration using Tensor Core

## The "No-BS" Summary

This paper figures out how to make NVIDIA's Tensor Cores—those matrix multiplication units designed for AI workloads—actually useful for Fully Homomorphic Encryption (FHE). The core trick: they noticed that two critical FHE operations (Base Conversion and Inner Product) involve repeated element-wise multiplications that can be restructured as matrix multiplications. Once you do that, you can exploit the data reuse inherent in matrix operations to slash memory traffic, *and* you can offload the compute to Tensor Cores. The second insight is that for the bit-widths FHE actually needs (36-48 bits), the FP64 components of Tensor Cores are faster than the INT8 components—counterintuitive given INT8's raw throughput advantage, but the Booth decomposition overhead for wide integers kills INT8's performance. They get 3.28× over TensorFHE on an A100.

---

## The Core Mechanism: A Whiteboard Explanation

### The Problem They're Solving

FHE's KeySwitch operation is the performance killer. It's where you re-encrypt a ciphertext under a different key to maintain correctness after multiplication. KeySwitch involves:

1. **Mod Up (Base Conversion)**: Convert polynomial coefficients from one modulus basis to another
2. **NTT**: Number Theoretic Transform (polynomial multiplication via FFT-like transform)
3. **Inner Product**: Multiply-accumulate with evaluation keys
4. **INTT**: Inverse NTT
5. **Mod Down**: Convert back

The previous state-of-the-art (TensorFHE) only used Tensor Cores for NTT. BConv and IP were stuck on CUDA cores doing element-wise operations with terrible memory reuse.

### The Transformation Trick

**Original BConv**: You have α input limbs, and you need to produce α' output limbs. Each output coefficient is computed by:
- Reading each input coefficient
- Multiplying by a conversion factor
- Accumulating

This means each input coefficient gets read α' times. With α'=8 and millions of coefficients, that's brutal memory traffic.

**Neo's BConv**: Reshape the data so that instead of processing coefficient-by-coefficient, you process in batches where the α dimension becomes the K-dimension of a matrix multiply:

```
Input: [α × BatchSize × N] tensor
Reshape to: [N × BatchSize × α] 
Multiply by: [α × α'] conversion matrix
Output: [N × BatchSize × α']
```

Now each coefficient is read once, participates in a matrix multiply, and the Tensor Core handles the accumulation internally. The conversion factors (the [α × α'] matrix) stay in registers/shared memory.

**Same trick for Inner Product**: The IP kernel multiplies ciphertext limbs by evaluation keys and accumulates across the β dimension. Original: β×α' element-wise multiplications per output. Neo: reshape so β becomes the K-dimension, do one matrix multiply of shape [BatchSize × β × β̃].

### Why FP64 Beats INT8

This is the counterintuitive part. A100's INT8 Tensor Core throughput is 624 TFLOPS vs. 19.5 TFLOPS for FP64—a 32× difference. But FHE needs 36-48 bit integers.

**INT8 approach** (what TensorFHE did for NTT):
- Split a 36-bit integer into 5 INT8 chunks
- Booth's algorithm: 5×5 = 25 partial products
- Each partial product is a matrix multiply
- Then merge results with shifts and adds

**FP64 approach** (Neo's insight):
- FP64 has 53 bits of mantissa precision
- Split a 36-bit integer into 3 chunks of 12 bits each
- Store as FP64 (no precision loss for integers < 2^53)
- Only 3 matrix multiplies needed (one per chunk of B matrix)
- Accumulation stays exact because 36 + 12 + log2(K) < 53

The math works out: FP64 is 1.65× faster than INT8 for 36-bit operands, 1.74× faster for 48-bit. The raw throughput disadvantage is overwhelmed by the reduction in Booth complexity.

### The KLSS Method Adoption

They also switched from the "Hybrid" KeySwitch method to "KLSS" (Kim-Lee-Seo-Song). The key difference: KLSS does most computation in an auxiliary ring R_T with a *selectable* word size (WordSize_T), rather than the full R_PQ ring.

Why this matters: If you pick WordSize_T = 48 bits, you can use FP64 Tensor Cores efficiently. The algorithm has more steps (Recover Limbs), but the per-step complexity drops because you're working with smaller rings. There's a sweet spot at WordSize_T = 48 where algorithmic complexity and hardware efficiency balance.

---

## The Critique

### Why It Got Into ISCA

1. **The FP64 insight is genuinely non-obvious.** Everyone assumed INT8 Tensor Cores were the path forward because of raw throughput. Showing that Booth complexity dominates for FHE-relevant bit-widths is a useful contribution that will influence future work.

2. **The algorithm-to-matrix-multiply transformation is clean.** Converting BConv and IP to matrix form isn't trivial—you need to get the data layout right so memory accesses coalesce. They worked through the details.

3. **End-to-end implementation on real hardware.** This isn't a simulation study. They ran ResNet-20 inference on encrypted data on an actual A100. The 3.28× speedup over TensorFHE is meaningful.

4. **The KLSS instantiation fills a gap.** KLSS was a theoretical algorithm improvement; this is the first GPU implementation showing it actually helps in practice.

### Where It's Weak

1. **The baseline is getting stale.** TensorFHE is from HPCA 2023, and they compare against HEonGPU (a non-TCU implementation). But the FHE acceleration space is moving fast. By the time this paper appeared (ISCA 2025), there may be newer baselines they're not comparing against. The 3.28× over TensorFHE is good, but TensorFHE itself had limitations they're partly inheriting.

2. **BatchSize = 128 is doing a lot of heavy lifting.** Look at Figure 17: at BatchSize=8, performance drops by 2× or more. Real applications may not always have 128 ciphertexts ready to process in parallel. The paper doesn't discuss latency for single-ciphertext operations, which matters for interactive applications.

3. **The "valid proportion" threshold for IP is hand-wavy.** They say IP goes to CUDA cores when valid proportion < 80%, but this threshold appears to be empirically determined without much justification. What's the sensitivity? Does it vary across GPU generations?

4. **Memory capacity limits aren't deeply explored.** They mention BatchSize can't grow indefinitely due to VRAM limits, but don't quantify where the wall is. For bootstrapping with L=44 (Set-H), how close are they to the 40GB limit? This matters for scaling to larger parameters.

5. **No power/energy analysis.** They're claiming better utilization of Tensor Cores, but Tensor Cores are power-hungry. Is the 3.28× speedup also a 3.28× energy improvement, or are they burning more power per operation? For cloud deployment, energy matters.

6. **The preprocessing/postprocessing overhead is glossed over.** Figure 13 shows these aren't negligible. For BConv, preprocessing + postprocessing is maybe 30% of total time. They fuse kernels to hide this, but the overhead is still there. What happens when fusion isn't possible (e.g., if you need intermediate results)?

7. **Security parameter sensitivity is limited.** They test λ ≥ 128 for most configurations, but Set-H drops to λ ≥ 98. That's below the commonly accepted 128-bit security threshold. The paper should be clearer about which configurations are actually secure for deployment.

---

## Discussion Questions

1. **On the FP64 vs. INT8 tradeoff**: The analysis assumes the Booth decomposition is the bottleneck, but what about memory bandwidth? INT8 operands are 8× smaller than FP64. For memory-bound phases (which FHE often is), does the smaller data footprint of INT8 ever win? Under what conditions would you switch back to INT8?

2. **On the KLSS parameter selection**: Table 8 shows dnum=9, α̃=5 is optimal, but the search space is small. Did they do any theoretical analysis to predict the optimum, or is this purely empirical? If it's empirical, how confident are they that the optimum doesn't shift for different L values or on different GPU architectures (e.g., H100 with different Tensor Core ratios)?

3. **On generalization beyond CKKS**: The paper focuses entirely on CKKS. The BConv-to-matrix-multiply transformation should work for BFV/BGV too, but the FP64 trick might not (those schemes use exact arithmetic, not approximate). How much of this work transfers to other FHE schemes, and what would break?

4. **On the comparison with ASICs**: Table 5 shows Neo at 0.24s for PackBootstrap vs. CraterLake (an ASIC) at... well, they don't directly compare. The CPU baseline is 17.2s. How does Neo compare to dedicated FHE accelerators like CraterLake, BTS, or SHARP? If ASICs are 10-100× faster, what's the real value proposition of the GPU approach beyond "GPUs are already deployed"?

5. **On the multi-stream optimization**: Section 4.6 mentions multi-stream processing to overlap TCU and CUDA core work, but there's no quantification of how much this helps. Is this a 5% improvement or a 50% improvement? What's the occupancy of each unit during execution?

---

## Contextual Fit in the Literature

This paper sits at the intersection of two threads:

**FHE Algorithm Optimization**: The KLSS method (Kim et al., CRYPTO 2023) reduced KeySwitch complexity theoretically. Neo is the first to show it helps on GPUs. This connects to the broader trend of algorithm-architecture co-design—you can't just implement the textbook algorithm; you need to reshape it for the hardware.

**Tensor Core Exploitation**: TensorFHE (HPCA 2023) was the first to use Tensor Cores for FHE, but only for NTT and only with INT8. Neo extends this to BConv/IP and shows FP64 is better. This is part of a larger story about repurposing AI accelerators for non-AI workloads—see also work on using Tensor Cores for scientific computing, sparse linear algebra, etc.

**What's Missing**: The paper doesn't engage much with the ASIC literature (CraterLake, BTS, SHARP, Taiyi). Those designs achieve 10-100× better performance than GPUs by building custom NTT units, on-chip key storage, and specialized interconnects. Neo's value proposition is "GPUs are already deployed," but the paper could be stronger about quantifying the gap and arguing when GPU-based FHE is "good enough."

**The Bigger Picture**: FHE is still 4-6 orders of magnitude slower than plaintext computation. Neo gets bootstrapping down to 0.24s, but that's still 240ms of latency for what would be microseconds on plaintext. The field needs another 100-1000× improvement before FHE is practical for interactive applications. Neo is a step, but the destination is far.