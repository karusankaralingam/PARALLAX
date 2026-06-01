# Paper Analysis: Cerium - A Scalable Multi-GPU Framework for Encrypted Large-Model Inference

## Q1: Whiteboard Explanation

Alright, let me break down what this paper is actually doing.

**The Core Problem:** Fully Homomorphic Encryption (FHE) lets you compute on encrypted data without decrypting it—incredibly powerful for privacy, but horrifically slow (10,000x+ overhead). Prior work built custom ASICs to accelerate FHE, but those require expensive fabrication and aren't accessible. GPUs exist everywhere, but nobody has figured out how to make them run FHE efficiently at scale.

**Why is this hard?**

Think of FHE as working with data in a very strange format. Everything is encoded as massive polynomials with thousands-of-bits coefficients. To do a simple multiply, you need Number Theoretic Transforms (NTT—like FFT but for modular arithmetic), polynomial multiplications, and periodic "bootstrapping" to refresh the ciphertext (otherwise you run out of computation budget). 

The killer issues are:
1. **Kernel overhead:** Each FHE operation maps to many GPU kernels. Launching thousands of small kernels kills performance.
2. **Memory explosion:** A BERT model that's ~400MB in plaintext becomes **1.5 TB** when encoded for FHE. Llama-8B? **112 TB**. That doesn't fit anywhere.
3. **Multi-GPU communication:** Scaling across GPUs is pointless if you spend all your time shuffling data between them.

**Cerium's Solution (the 3-layer cake):**

1. **DSL + Compiler:** You write FHE programs in a Python DSL. The compiler lowers this through a "Limb IR" (representing operations on the RNS residues of polynomials), then automatically *fuses* operations—both horizontally (parallel independent ops → one big kernel) and vertically (dependent ops → data stays in registers instead of going to memory).

2. **Sparse Plaintext Compression:** Here's the clever trick. When packing weight matrices for FHE, you get redundant patterns with power-of-2 strides. This creates sparse polynomials after encoding. Cerium exploits this symmetry to compress 1.5TB → 16.6GB for BERT (96x reduction). Without this, LLMs are literally impossible.

3. **Multi-GPU Runtime:** The compiler generates a kernel+memory schedule. The runtime uses CUDAGraphs (batch-launch thousands of kernels with one call), pins frequently-used data in GPU memory, and prefetches weight matrices layer-by-layer for models that don't fit.

**The punchline:** First system to run encrypted BERT-Base (8.8 seconds) and Llama3-8B (134 seconds) end-to-end. Matches the performance of CraterLake (an FHE ASIC) using off-the-shelf GPUs.

---

## Q2: The Key Insight

The paper has **two distinct key insights**, operating at different levels:

### Insight 1 (Compiler): Limb-Level Fusion as the Right Abstraction

Prior GPU FHE work (like Cheddar) achieved good performance through hand-crafted, application-specific kernel fusion. This doesn't scale—every new model needs new expert optimization.

Cerium's insight is that **reasoning about fusion at the "limb" level** (the RNS residues of polynomials) is the sweet spot for automation. Limbs are:
- Data-parallel across different RNS bases (safe to parallelize)
- Data-dependent within the same RNS base (tells you what to fuse)
- Small enough to reason about for cycle detection and resource estimation

This lets them automatically determine correct fusion boundaries by checking parent/child intersections in the limb IR DAG (Section IV-C). The result: 2.87× speedup from fusion alone (Section V-E1), matching or exceeding hand-tuned code.

### Insight 2 (Memory): Exploiting Encoding Symmetry for Compression

This is the enabler for LLMs. When you pack weight matrices using Baby-Step Giant-Step (BSGS) packing for encrypted matrix multiplication, the redundancy patterns have power-of-2 strides. After FFT encoding and NTT transformation, this creates **repeated contiguous blocks** in the evaluation representation (Figure 7).

Cerium stores only the unique values and their equivalence classes, achieving 96-119× compression. This transforms encrypted BERT from requiring 1.5TB (impossible) to 16.6GB (fits in one GPU). **Without this single trick, encrypted LLM inference doesn't exist on any practical hardware.**

The insight here is recognizing that the *structure* of FHE packing schemes can be systematically exploited, not just accepted as a cost.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Benchmark Suite with Real Workloads**

The paper doesn't just run microbenchmarks. They implement full end-to-end inference for:
- Bootstrapping (the FHE primitive everyone cares about)
- ResNet-20 (standard CNN benchmark for FHE)
- BERT-Base with 128 tokens and actual accuracy reported (69.3% on GLUE RTE, matching plaintext)
- Llama3-8B decoder blocks

This is the first paper to demonstrate BERT and Llama at all. Prior work (THOR, Nexus) either ran layer-by-layer with CPU offloading or only estimated performance.

**2. Apples-to-Apples ASIC Comparison**

Figure 10 provides a direct comparison against CraterLake, ARK, and Cinnamon FHE ASICs on the same benchmarks (bootstrap, ResNet-20, BERT). Cerium on 8×B200 achieves 1.06× CraterLake's performance—essentially matching an ASIC with commodity hardware. This is a significant practical result.

**3. Rigorous Ablation Studies**

Section V-E systematically isolates contributions:
- Horizontal fusion: 1.84-2.04× speedup
- Vertical fusion: additional 1.43-1.68×
- CUDAGraphs: 7-14% improvement
- Sparse compression: 3.25× for BERT, enables Llama
- Memory scheduling: 2.3-2.5× vs. online allocation

Each technique's contribution is quantified independently, which is refreshing.

**4. Multi-GPU Scaling Analysis with New Optimizations**

They don't just use Cinnamon's parallel keyswitching algorithms—they show those alone perform 1.2× *slower* than single-GPU (Section V-E5). Their communication optimization passes achieve 44% reduction in bytes transferred. This demonstrates that multi-GPU FHE requires *compiler* support, not just algorithmic techniques.

### Weaknesses

**1. Missing Worst-Case Performance Analysis**

The paper reports geometric means and specific benchmark times, but never shows per-layer breakdowns or identifies bottleneck operations. For BERT and Llama, which layers dominate? Is it attention, FFN, or bootstrapping? Figure 11 shows aggregate speedups but doesn't help practitioners understand where the remaining overhead comes from.

**2. Security Parameters are Fixed and Aggressive**

All benchmarks use N=64K, 1782-bit modulus, Hamming weight H=32K (Section V-A). While they claim 128-bit security, the specific parameter choices (especially the ternary secret with high Hamming weight) affect both security margins and performance. No sensitivity analysis shows how performance scales with more conservative parameters.

**3. Accuracy Claims Need Scrutiny**

They claim BERT accuracy of 69.3% on GLUE RTE "matching the plaintext model" and ResNet-20 at 91.4%. However:
- Section V-A mentions using polynomial approximations for nonlinearities (softmax, ReLU, GELU, SiLU)
- The degree and precision of these approximations affect accuracy
- No discussion of how approximation error accumulates over many layers in Llama3-8B

For Llama3-8B specifically, they only run the "decoder blocks" for generating "the first token" (Section V-A)—not full generation. This is an important caveat.

**4. Sparse Compression Requires Specific Packing Constraints**

Section IV-E states: "efficiently implementing large models in FHE requires creating packing strategies where the repetition stride is a power of two." This is a non-trivial constraint that may not apply to all model architectures or packing schemes. The paper doesn't discuss what happens when your model doesn't naturally fit this pattern.

**5. ASIC Comparison Has a Fairness Issue**

The ASIC comparison in Figure 10 compares against published numbers for CraterLake (ISCA'22), ARK (MICRO'22), and Cinnamon (ASPLOS'25). But Cerium runs on B200 GPUs (released 2024), while these ASICs were designed years earlier. A fair comparison would either normalize for technology node or compare against projected ASIC performance with equivalent transistor budgets.

**6. Llama3-8B Limitations Buried**

Section V-A quietly notes: "we do not use any modifications like LoRA that require retraining the model." But they also only run decoder blocks for *one* token on a 128-token prompt. Generating a full response would take orders of magnitude longer. The 134-second headline number is for a single forward pass, not practical inference.

---

## Q4: What the Authors Didn't Tell You

**1. The Cost of Bootstrapping Dominates Everything**

Bootstrapping runs in 7.5ms (Table I), which sounds fast. But BERT takes 8.8 seconds. This means bootstrapping is called *hundreds* of times per inference. The paper never discloses:
- How many bootstraps per layer?
- What's the multiplicative depth budget before bootstrap is needed?
- Could a different model architecture (encryption-friendly LLM designs like [30]) reduce bootstrap count?

**2. The Memory Bandwidth Wall**

Figure 6 shows elementwise kernels plateau with SM count—they're bandwidth-bound. But the paper never reports actual achieved bandwidth utilization. On an H100 with ~3TB/s HBM bandwidth, are they hitting 50%? 80%? This matters for understanding headroom.

**3. What Happens at Longer Sequence Lengths?**

All results use 128 tokens. Attention is O(n²) in sequence length. For FHE, this quadratic blowup is catastrophic because you're doing polynomial operations on already-massive ciphertexts. What happens at 512 tokens? 2048? The paper doesn't address this.

**4. The Compilation Time Cost**

Table II shows Llama3-8B takes 11 minutes to compile. This happens *once* per model configuration. But if you change sequence length, batch size, or any parameter that affects the circuit structure, you recompile. The paper doesn't discuss how stable these circuits are across different inputs.

**5. Unified Memory (UVM) Requires Specific Hardware**

Section IV-H3 mentions using UVM with memory pinning and prefetching for Llama3-8B. This assumes PCIe Gen5 bandwidth for host-GPU transfers. On older systems or with PCIe contention from multi-GPU setups, performance could degrade significantly. The 12.1× speedup from prefetching (Section V-E4) suggests that without careful orchestration, you're 12× slower—this is a fragile system.

**6. They Quietly Punt on Multi-GPU ASIC Comparison**

Section VI notes: "FHE ASICs are statically scheduled [and] cannot support workloads like Llama3-8B that exceed accelerator memory." This is a fair point, but it also means the comparison in Figure 10 doesn't include Llama3-8B for ASICs. The only comparable numbers are bootstrap, ResNet-20, and BERT—all of which *do* fit in ASIC memory.

**7. The Accuracy vs. Performance Tradeoff**

Using polynomial approximations for nonlinearities (softmax with max normalization, GELU, etc.) is standard in FHE, but the *degree* of the polynomial matters enormously. Higher degree = more accuracy = more multiplications = more bootstraps = slower. The paper chose approximations to achieve plaintext-matching accuracy, but doesn't discuss how much faster the system would be with lower-accuracy approximations (which might be acceptable for many applications).

**8. Open Source Promise, Not Delivery**

Section I states: "The Cerium framework will be open-sourced following publication." The paper is on arXiv from December 2025. Without the code, reproducibility is impossible, and the community cannot verify claims or build upon this work.