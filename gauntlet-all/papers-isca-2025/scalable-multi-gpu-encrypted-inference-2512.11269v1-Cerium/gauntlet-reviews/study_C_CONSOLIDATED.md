# Study C — Multi-Persona Synthesis
**Paper:** 2512.11269v1 Cerium  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 09:16

---

# Q1: Whiteboard Explanation

Cerium tackles the fundamental challenge of making Fully Homomorphic Encryption (FHE) practical on GPUs for large AI models. FHE allows computation on encrypted data without decryption—powerful for privacy but catastrophically slow (10,000×+ overhead).

**The Data Representation Problem:**
FHE operations work on polynomials with coefficients that are thousands of bits long. To make this tractable, you use the Residue Number System (RNS) to decompose these massive integers into "limbs"—residues modulo small 28-bit primes that fit in GPU words. A single encrypted number becomes a polynomial with 65,536 coefficients, each decomposed across multiple RNS bases.

**The Three-Headed Monster:**
1. **Kernel Overhead:** Mapping each FHE operation to a GPU kernel creates thousands of tiny kernel launches—launch overhead dominates.
2. **Memory Explosion:** BERT-Base encrypted becomes 1.5TB; Llama3-8B becomes 112TB (Figure 2). This exceeds any practical storage.
3. **Multi-GPU Communication:** Naive scaling across GPUs spends all time shuffling data.

**Cerium's Solution Stack:**

*Compiler (DSL → Limb IR → Fused Kernels):*
The key abstraction is the **Limb IR**—instructions of the form `{opcode, RNS_Base_ID, dest, src}`. Limb operations are (i) data-parallel, (ii) mostly independent across RNS bases, and (iii) mostly dependent within the same base. This enables:
- **Horizontal Fusion:** Pack independent limb ops (same opcode, different RNS bases) into one kernel launch—they run in separate thread blocks, amortizing launch overhead.
- **Vertical Fusion:** Chain dependent ops within the same thread—data stays in registers instead of round-tripping through global memory. Fusion boundaries are determined by cycle detection and cross-thread-block dependency checking.

*Sparse Plaintext Compression (Figure 7):*
When packing weight matrices using Baby-Step Giant-Step for encrypted matrix multiplication, power-of-2 strided redundancies create sparse polynomials. After NTT transformation, these produce contiguous repeated blocks. Cerium stores one copy and transforms indices at code-generation time: 96× compression for BERT (1.5TB → 16.6GB), 119× for Llama3-8B (112TB → 982GB).

*Runtime:*
CudaGraphs batch thousands of kernels into single launches. Memory pools are separated by lifetime (evalkeys pinned, weights prefetched from host). For multi-GPU, aggregate-scatter + all-gather operations are merged into all-reduce when output-aggregation keyswitching feeds input-broadcast keyswitching, achieving 44% communication reduction.

**Results:** 7.5ms bootstrapping (first sub-10ms on real hardware), BERT-Base in 8.8s, Llama3-8B in 134s—matching CraterLake ASIC performance with commodity GPUs.

---

# Q2: The Key Insight

The paper contains two synergistic insights that together enable practical encrypted LLM inference:

**Primary Insight: Limb-Level Abstraction for Automated Fusion**

Prior GPU FHE work (Cheddar, TensorFHE) achieved performance through hand-crafted, application-specific kernel fusion—unsustainable for diverse models. Cerium recognizes that **the limb level is the Goldilocks zone for fusion decisions**: abstract enough to reason about correctness via simple graph operations, yet concrete enough to predict resource pressure and generate efficient code.

From Section IV-C: "Limbs are well suited for this task as limb operations (i) form the atomic building blocks of RNS-CKKS, (ii) are data parallel, (iii) can be grouped into classes that require significantly distinct kernels that cannot be fused, (iv) are largely data independent across RNS bases, and (v) mostly data dependent within the same RNS base."

This creates a natural DAG structure where horizontal fusion = packing independent bases (more thread blocks, amortized launch) and vertical fusion = chaining dependent operations (register communication, no global memory round-trip). The cycle-detection trick (checking parent/child set intersection) makes this tractable at compile time, achieving 2.63-3.32× speedup from fusion alone (Section V-E1).

**Enabling Insight: Encoding Symmetry for Memory Compression**

This transforms encrypted LLM inference from "physically impossible" to "feasible." When you pack matrix diagonals with power-of-2 strides for BSGS multiplication, the encoding→NTT pipeline produces limb vectors with contiguous repeated blocks. This is a mathematical symmetry arising from FFT structure that prior work stored redundantly.

Without this compression, Llama3-8B requires 112TB—exceeding any plausible storage. With it: 982GB, which can be orchestrated from host memory. **This single trick is what makes encrypted LLM inference exist on any practical hardware.**

**Why This Works on GPUs:**
The paper exploits that NTT and Base Conversion kernels are latency-bound (scale linearly with SM count, Figure 6) while Elementwise kernels are bandwidth-bound (plateau quickly). Vertical fusion helps latency-bound ops by keeping data in registers; horizontal fusion helps bandwidth-bound ops by amortizing launch overhead and enabling load reuse.

---

# Q3: Evaluation Critique

## Strengths

**Comprehensive Benchmark Coverage with Real Workloads:**
The evaluation spans four orders of magnitude in model size: bootstrapping, ResNet-20, BERT-Base, and Llama3-8B (Table I). This is the first paper to demonstrate end-to-end encrypted BERT and Llama inference. They report accuracy numbers (91.4% for ResNet-20, 69.3% for BERT on GLUE RTE—matching plaintext), addressing the common criticism that FHE papers ignore model quality.

**Rigorous Ablation Studies (Figure 11):**
Section V-E methodically isolates each contribution: horizontal fusion (1.84-2.04×), vertical fusion (additional 1.43-1.68×), CudaGraphs (7-14%), sparse compression (3.25× for BERT, enables Llama entirely), and memory scheduling (2.3-2.51×). Each technique's contribution is quantified independently with clear baselines.

**Honest ASIC Comparison (Figure 10):**
They normalize to Cinnamon-8 and show they're 4.4× slower than the best multi-ASIC system but match CraterLake (1.06×). The 7.5ms bootstrap is genuinely impressive. They also honestly report that naive multi-GPU scaling with Cinnamon's algorithms performs 1.2× *slower* than single-GPU (Section V-E5).

**Real Hardware Across Multiple Generations:**
Testing on A100, H100, and B200 across 1/2/4/8 GPU configurations demonstrates scaling behavior and rules out generation-specific effects. Compilation times are reasonable (11 minutes for Llama3-8B, Table II).

## Weaknesses

**ASIC Comparison Has Significant Caveats:**
The comparison pits real GPU measurements against simulated/projected ASIC performance. CraterLake (ISCA'22) and ARK (MICRO'22) are 3 years old; comparing 2025 B200 GPUs against 2022 ASIC designs is temporally unfair. Process node advantages (B200 at 4nm vs. ASICs at 7nm/5nm) are bundled into the "GPU wins" narrative. Critically, the paper admits ASICs "cannot support workloads like Llama3-8B that exceed accelerator memory" (Section VI)—so the comparison only includes smaller workloads where both can execute.

**Multi-GPU Scaling Efficiency is Poor:**
From Table I: 8×B200 bootstrap achieves 1.93× speedup for 8× resources (24% efficiency). ResNet-20: 1.53× with 8×GPUs (19% efficiency). The paper attributes this to communication overhead but provides no roofline-style analysis showing theoretical bounds. The 44% communication reduction sounds good, but we don't know if they're near the lower bound.

**Sparse Compression Requires Specific Constraints:**
Section IV-E states compression only works when "the repetition stride is a power of two." This requires careful algorithm design—the DSL requires programmers to declare `repeatStride=256` (Figure 4). The paper doesn't discuss what happens for operations where power-of-2 packing isn't natural or quantify what fraction of real FHE applications satisfy this constraint.

**Accuracy Claims Need More Context:**
GLUE RTE is a small dataset (2,490 examples) where ~50% is random baseline. For Llama3-8B, they only run decoder blocks for single-token generation on 128-token prompts—no perplexity, no downstream task evaluation, no discussion of FHE noise accumulation over 8B parameters. The polynomial approximations for nonlinearities (softmax, GELU, SiLU) affect accuracy, but degrees and precision budgets are undisclosed.

**Missing Resource Utilization Analysis:**
The paper never reports actual bandwidth utilization, register file pressure for fused kernels, achieved occupancy percentages, or power/energy consumption. For bandwidth-bound elementwise kernels, are they hitting 80% of HBM bandwidth? 50%? This matters for understanding headroom.

---

# Q4: What the Authors Didn't Tell You

**1. Host Memory Requirements Are Buried:**
Llama3-8B compressed weights are 982GB (Section V-E3). With 8×B200 (192GB HBM each = 1.5TB total), weights must stream from host memory. A DGX system has 2TB host RAM—they're using half just for weights. Add evaluation keys ("10-100 GBs"), bootstrap matrices, and intermediates ("several GBs per function"), and the actual memory breakdown for Llama3-8B is never disclosed.

**2. The UVM Baseline is a Strawman:**
Section V-E4 claims 12.1× speedup from memory pinning/prefetching over "UVM without prefetching." Naive UVM with on-demand page faults is pathologically slow for streaming workloads. A fairer baseline would be explicit `cudaMemcpyAsync` with double-buffering.

**3. Compiler Heuristics Are Black-Boxed:**
Section IV-C mentions partitioning large DAGs into "smaller sub-DAGs" with fusion performed independently within each. Section IV-D mentions kernel splitting when registers exceed a threshold. What are these thresholds? How were they tuned? How sensitive is performance to these choices? The paper "experimentally picks the default sub-DAG size" but never discloses it.

**4. NVLink Assumptions:**
All multi-GPU results use DGX systems with NVLink (SXM form factor). The optimizations (Section IV-G) assume high-bandwidth interconnect for all-reduce fusion. What happens on PCIe-connected systems that are far more common? The paper explicitly avoids PCIe H100 comparison with Cheddar (footnote, page 9).

**5. Single-Token Generation Only:**
For Llama3-8B, they run decoder blocks for "the first token" on a 128-token prompt. Generating 100 tokens would take 13,400+ seconds (3.7+ hours) with no optimizations for autoregressive generation or KV-cache management. The paper's framing suggests breakthrough capability; the reality is a proof-of-concept.

**6. Polynomial Approximation Costs Hidden:**
Section II mentions nonlinearities require polynomial approximations "whose degree and precision must be tailored to model accuracy requirements." Higher-degree polynomials mean more multiplications and more bootstraps (the dominant cost). The paper doesn't disclose polynomial degrees used or additional multiplicative depth consumed.

**7. The "First" Claims Have Scope:**
"First sub-10ms bootstrapping" and "first FHE Llama3-8B inference" are qualified by specific parameter choices (N=64K, 128-bit security, specific RNS basis). Different parameters might shift these numbers significantly, and no sensitivity analysis is provided.

**8. Open Source Promise, Not Delivery:**
Section I states the framework "will be open-sourced following publication." Without the 25K lines of C++ compiler and 11K lines of CUDA runtime, reproducibility is impossible and claims cannot be verified.