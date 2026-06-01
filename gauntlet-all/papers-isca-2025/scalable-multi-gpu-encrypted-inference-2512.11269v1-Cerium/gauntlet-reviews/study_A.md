# Study A — Simple Directive
**Paper:** 2512.11269v1 Cerium  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 09:16

---

Q1: Whiteboard Explanation

Imagine you want to run AI inference on sensitive data (medical records, financial data) in the cloud, but you don't trust the cloud provider to see your data. Fully Homomorphic Encryption (FHE) lets you compute directly on encrypted data—the cloud never sees plaintext. The catch? FHE is brutally slow—10,000x slower than normal computation.

**The Problem Cerium Solves:**
Prior work built custom FHE ASICs (expensive, inaccessible) or hand-tuned GPU libraries (brittle, model-specific). Neither scales to large models like LLMs. Consider encrypted Llama3-8B: the encoded weights alone require 112 terabytes—impossible to fit anywhere without clever compression.

**Cerium's Architecture (3 components):**

1. **DSL & Compiler**: You write FHE programs in a Python DSL. The compiler lowers this through polynomial-level IR to limb-level IR, then performs automatic kernel fusion. The key insight: reason about fusion at the "limb" level (RNS residues of polynomials)—limbs are data-parallel building blocks that naturally group into fuseable or non-fuseable classes.

2. **Sparse Plaintext Compression**: When weight matrices are packed with power-of-2 strided redundancies, a cyclic symmetry emerges that makes encoded polynomials sparse. Cerium compresses 1.5TB (BERT) to 16.6GB—a 96x reduction.

3. **Multi-GPU Runtime**: Memory pools with heterogeneous lifetime management, CudaGraphs for low-overhead kernel launch, and communication optimizations (merge scatter+gather into all-reduce) enable scaling across 8 GPUs.

**Result**: 7.5ms bootstrapping (first sub-10ms on real hardware), BERT-Base in 8.8 seconds, matching FHE ASICs like CraterLake—all on commodity GPUs.

Q2: The Key Insight

The central insight is that **limbs—the RNS residues of FHE polynomials—provide the ideal abstraction level for automated GPU kernel optimization**. 

Prior approaches either worked at the ciphertext level (too coarse, missing polynomial optimizations) or required manual kernel fusion per application (brittle, unscalable). Cerium recognizes that limb operations: (i) form atomic RNS-CKKS building blocks, (ii) are inherently data-parallel, (iii) naturally partition into distinct kernel classes, (iv) are largely independent across RNS bases but dependent within a base, and (v) have properties that allow efficient reasoning about fusion correctness via cycle detection through parent/child set intersections.

This abstraction enables a compiler to automatically determine which operations can be horizontally fused (different thread blocks) vs. vertically fused (register communication within threads), generating performant kernels without application-specific hand-tuning. Combined with the sparse plaintext compression insight—that power-of-2 strided redundancies create exploitable cyclic symmetry—this transforms encrypted LLM inference from a 112TB memory problem into a feasible 982GB problem, making the previously impossible tractable.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**
- **Comprehensive benchmarks**: Spans bootstrapping microbenchmark through Llama3-8B, demonstrating scalability across 4 orders of magnitude in model size
- **Meaningful baselines**: Compares against hand-optimized Cheddar (single-GPU SOTA), prior LLM work (THOR, Nexus), and three FHE ASICs (CraterLake, ARK, Cinnamon)
- **Detailed ablation studies**: Isolates contributions of horizontal fusion (1.84-2.04x), vertical fusion (1.43-1.68x additional), compression (96-119x memory reduction), memory scheduling (2.3-2.5x), and multi-GPU optimizations (44% communication reduction)
- **End-to-end accuracy validation**: Reports encrypted inference accuracy matching plaintext models (91.4% ResNet-20, 69.3% BERT-RTE)
- **Multiple GPU generations**: Tests A100, H100, B200 showing generality

**Weaknesses:**
- **No throughput/batching evaluation**: All results are single-inference latency; real deployments batch requests
- **Asymmetric ASIC comparison**: Compares 8 B200 GPUs (thousands of watts, ~$200K+) against single-chip ASICs without power/cost normalization
- **Limited multi-GPU scaling analysis**: Bootstrap goes from 14.5ms (1 GPU) to 7.5ms (8 GPUs)—only 1.93x speedup, suggesting communication overhead dominates; deeper analysis warranted
- **No comparison to Cheddar on H100/B200**: Only A100 comparison with Cheddar due to availability issues
- **Llama3-8B incompleteness**: Only evaluates decoder blocks for first token generation; no autoregressive generation analysis

Q4: What the Authors Didn't Tell You

**Practical deployment concerns:**
- **Energy and cost**: Eight B200 GPUs consume ~5.6kW and cost >$200K. The ASIC comparison ignores that CraterLake/ARK would be orders of magnitude more power-efficient per inference.
- **Autoregressive generation**: 134 seconds for one Llama3-8B token means generating a 100-token response would take ~4 hours—impractical for interactive use. The paper avoids discussing this.

**Technical limitations:**
- **Accuracy-performance tradeoff opacity**: The paper uses specific polynomial approximations for nonlinearities but doesn't analyze how accuracy degrades with faster (lower-degree) approximations or what precision-performance Pareto frontier exists.
- **Compile time scaling**: 11 minutes for Llama3-8B seems acceptable, but with the "sub-DAG partitioning" mentioned, it's unclear how much optimization quality is sacrificed for compilation speed.

**Missing comparisons:**
- **CPU FHE libraries**: No comparison to SEAL/OpenFHE on modern CPUs—readers can't assess when GPUs become worthwhile.
- **Hybrid MPC approaches**: BOLT comparison is dismissive ("91s on LAN"); but MPC overhead scales differently with model size and might win for smaller models.

**Reproducibility concerns:**
- The 112TB→982GB compression for Llama3-8B requires restructuring weight packing to have power-of-2 strides—this may require architecture-specific packing algorithms not detailed in the paper.