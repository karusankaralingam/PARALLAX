## Q1: Whiteboard Explanation

Let me walk you through what Cerium actually does at the hardware level.

**The Problem Setup:**
FHE (Fully Homomorphic Encryption) lets you compute on encrypted data, but it's catastrophically slow—4+ orders of magnitude overhead. The operations happen on *polynomials* with coefficients that are thousands of bits long. To make this tractable, you use RNS (Residue Number System) to decompose these monster integers into "limbs"—residues modulo small machine-word-sized primes (28-bit in this paper, fitting in 32-bit GPU words).

**The Data Flow (Figure 1):**
1. Your plaintext vector (real numbers) gets **encoded** via inverse FFT into a polynomial
2. That polynomial gets **encrypted** into a ciphertext pair
3. RNS decomposes the huge coefficients into limbs across different prime bases
4. All computation happens on these limbs in parallel

**The Magic Trick - Limb IR Fusion (Figure 5):**

This is where the real architectural insight lives. Prior work either:
- Mapped each polynomial operation → one GPU kernel (terrible: launch overhead kills you)
- Hand-fused kernels for each application (unsustainable)

Cerium introduces a **Limb IR** abstraction. Each instruction has: `{opcode, RNS_Base_ID, dest, src}`. The key insight: limb operations are (i) data-parallel, (ii) mostly independent across RNS bases, (iii) mostly dependent *within* the same base.

**Horizontal Fusion:** Pack independent limb ops (same opcode, different RNS bases) into one kernel launch. They run in separate thread blocks. This amortizes launch overhead.

**Vertical Fusion:** Chain dependent ops *within* the same thread. Data stays in registers instead of round-tripping through global memory. But you can't fuse across operations that permute data (like rotations) because that creates cross-thread-block dependencies.

**The Memory Compression Trick (Figure 7):**
For matrix multiplication, you pack weight matrix diagonals into plaintext slots using Baby-Step Giant-Step. This creates power-of-2 strided redundancy. When you NTT such a sparse polynomial, the output has *contiguous repeated blocks*. Cerium exploits this symmetry: store one copy, index into equivalence classes. Result: 96× compression for BERT (1.5TB → 16.6GB), 119× for Llama3-8B (112TB → 982GB).

**The Runtime Glue:**
CudaGraphs batch thousands of kernels into a single launch. The memory layout separates pools by lifetime (evalkeys pinned, weights shuttled from host). For multi-GPU, they merge aggregate-scatter + all-gather into all-reduce when output-aggregation keyswitching feeds input-broadcast keyswitching.

---

## Q2: The Key Insight

**The Core Architectural Insight:**

The paper's central contribution is recognizing that **the Limb is the right abstraction layer for GPU kernel fusion decisions**—not the polynomial level (too coarse, misses optimizations) and not raw CUDA (too fine, intractable search space).

Limbs have a beautiful property: they are *algebraically independent* across different RNS bases but *sequentially dependent* within the same base. This creates a natural DAG structure where:
- Horizontal fusion = packing independent bases into one kernel (more thread blocks, amortized launch)
- Vertical fusion = chaining dependent operations within a base (register communication, no global memory round-trip)

The cycle-detection trick (checking parent/child set intersection) makes this tractable at compile time.

**The Second Key Insight - Sparse Plaintext Encoding:**

When you pack matrix diagonals with power-of-2 strides for BSGS multiplication, the encoding→NTT pipeline produces limb vectors with *contiguous repeated blocks*. This is a mathematical symmetry arising from the FFT structure. Prior work stored all redundant values. Cerium stores one block and transforms indices at code-generation time.

This isn't just a compression trick—it's what makes encrypted LLM inference *physically possible*. Without it, Llama3-8B requires 112TB of weight storage. With it: 982GB, which can be shuttled from host memory.

**What Makes This Work on GPUs:**

The paper exploits that NTT and Base Conversion kernels are *latency-bound* (scale linearly with SM count, Figure 6) while Elementwise kernels are *bandwidth-bound* (plateau quickly). Vertical fusion helps latency-bound ops by keeping data in registers. Horizontal fusion helps bandwidth-bound ops by amortizing kernel launch overhead and enabling load reuse when operands are shared.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive Ablation Study (Section V-E):** They actually decompose the speedup stack properly. Figure 11 shows horizontal fusion (1.84-2.04×), vertical fusion (1.43-1.68× additional), CudaGraphs (7-14%), memory scheduling (2.3-2.51×), and compression (96-119×) contributions individually. This is rare and valuable—you can see where the juice actually comes from.

2. **Real End-to-End Numbers:** Table I reports actual wall-clock times for BERT-Base (8.8s on 8×B200) and Llama3-8B (134s). Prior work (THOR, Nexus) either ran layer-by-layer or estimated by aggregating layers without memory orchestration overhead. The 9.12× speedup over THOR and 34.47× over Nexus are apples-to-apples comparisons on matching hardware.

3. **ASIC Comparison is Honest (Figure 10):** They normalize to Cinnamon-8 and show they're 4.4× slower than the best multi-ASIC system but *match* CraterLake. The 7.5ms bootstrap is genuinely impressive—first sub-10ms on real hardware.

4. **Compilation Time is Reasonable (Table II):** Llama3-8B compiles in 11 minutes. This matters for practical adoption.

**Weaknesses:**

1. **The B200 Numbers Carry the ASIC Comparison:** The ASIC-competitive claims rely on 8×B200 GPUs. A DGX B200 system costs ~$275k and consumes 10kW+. The ASICs they compare against (CraterLake, ARK, Cinnamon) are *architectural proposals* with no real silicon. The comparison is "our real system vs. their simulator"—but their simulator assumes fabrication at advanced nodes with HBM that would also cost enormous money. Still, this asymmetry should be called out.

2. **Multi-GPU Scaling is Sublinear (Figure 11d):** Going from 1→8 B200s for bootstrap yields only 1.93× speedup (14.5ms → 7.5ms), not 8×. The paper attributes this to communication overhead but doesn't provide a roofline-style analysis showing what the theoretical bound is. The 44% reduction in bytes communicated from their optimizations sounds good but we don't know if they're near the communication lower bound.

3. **Accuracy Claims Need More Context:** Section V-A claims BERT achieves 69.3% on GLUE RTE "matching the plaintext model." But this is a binary classification task where ~50% is random. The 91.4% ResNet-20/CIFAR-10 accuracy is better validated. For Llama3-8B, they only run the decoder blocks for single-token generation—no perplexity or downstream task evaluation.

4. **Memory Bandwidth Analysis is Missing:** They characterize kernel types in Figure 6 but never show actual bandwidth utilization numbers. For bandwidth-bound elementwise kernels, are they hitting 80% of HBM bandwidth? 50%? This matters for understanding headroom.

5. **The "100× Memory Reduction" Requires Specific Packing:** Section IV-E states the compression only works when "the repetition stride is a power of two." This requires careful algorithm design for new models. The paper doesn't discuss what happens for operations where power-of-2 packing isn't natural.

---

## Q4: What the Authors Didn't Tell You

**1. The Real Hardware Tax:**

The paper is remarkably silent on actual resource utilization. Key missing numbers:
- Register file pressure for vertically fused kernels (they mention "kernel splitting" when registers exceed threshold, but never say what threshold or how often splitting happens)
- Shared memory usage for NTT butterfly exchanges
- Actual occupancy achieved (they discuss occupancy *optimization* but never report achieved occupancy percentages)

The "value recomputation" optimization (Section IV-D) admits they're trading compute for register pressure. What's the actual instruction count overhead?

**2. The UVM Baseline is a Strawman:**

Section V-E4 claims 12.1× speedup from memory pinning/prefetching over "UVM without prefetching." But naive UVM with on-demand page faults is known to be pathologically slow for streaming workloads. A fairer baseline would be explicit `cudaMemcpyAsync` with double-buffering. The 12.1× number likely overstates the contribution of their specific layout design.

**3. Host Memory Requirements Are Buried:**

Llama3-8B compressed weights are 982GB (Section V-E3). Even after compression, this requires ~1TB of host memory plus GPU memory for evalkeys, bootstrap matrices, and intermediates. The paper never states total host memory requirements. A DGX system has 2TB host RAM—they're using half of it just for weights.

**4. The Compilation Complexity is Hand-Waved:**

Section IV-C mentions "if a function's limb IR DAG is too large, the compiler partitions it into smaller sub-DAGs." What's the heuristic? How does partitioning affect fusion quality? They "experimentally pick the default sub-DAG size" but don't disclose it or show sensitivity analysis.

**5. NVLink Assumptions:**

All multi-GPU results use DGX systems with NVLink (SXM form factor). The multi-GPU optimizations (Section IV-G) assume high-bandwidth interconnect for the all-reduce fusion. What happens on PCIe-connected multi-GPU systems that are far more common in production? The paper explicitly avoids PCIe H100 comparison with Cheddar (footnote on page 9) because "it does not provide a direct comparison."

**6. The ASIC Comparison Has a Hidden Advantage for Cerium:**

Section VI notes that "FHE ASICs are statically scheduled, they cannot support workloads like Llama3-8B that exceed accelerator memory." This is framing the ASIC limitation as fundamental rather than a design choice. CraterLake/ARK/Cinnamon could add host memory orchestration—they just didn't because their papers focused on fitting within on-chip memory. Cerium's ability to run Llama3-8B is as much about their memory orchestration as their kernel performance.

**7. The Precision Cost of Polynomial Approximations:**

Section II mentions nonlinearities require "polynomial approximations whose degree and precision must be tailored to model accuracy requirements." The BERT softmax uses "max value normalization" to achieve accuracy, but the paper doesn't disclose the polynomial degrees used or the additional multiplicative depth consumed. Higher-degree polynomials mean more bootstraps, which are the dominant cost.