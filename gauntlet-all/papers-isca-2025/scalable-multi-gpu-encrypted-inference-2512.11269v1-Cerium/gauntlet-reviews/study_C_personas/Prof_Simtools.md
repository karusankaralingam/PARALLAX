## Q1: Whiteboard Explanation

**Cerium: Making Encrypted AI Practical on GPUs**

Imagine you want to run a private medical AI model where the cloud provider *never* sees the patient's data—not even temporarily. Fully Homomorphic Encryption (FHE) lets you compute directly on encrypted data. The problem? It's devastatingly slow—10,000× slower than plaintext computation.

**The Core Challenge:**
FHE operations work on polynomial rings, not tensors. A single encrypted number becomes a polynomial with 65,536 coefficients, each thousands of bits long. Running Llama3-8B encrypted would naively require 112 TB of memory and take hours.

**Cerium's Three-Layer Solution:**

1. **Compiler Magic (DSL → Optimized GPU Kernels)**
   - FHE programs are circuits of additions, multiplications, and rotations on ciphertexts
   - Prior work: hand-optimize kernels for each model (months of expert effort)
   - Cerium: automatically fuses operations at the "limb level" (RNS decomposition)
   - Key insight: Limb operations are data-parallel and form clean fusion boundaries
   - Horizontal fusion: batch independent ops into one kernel launch
   - Vertical fusion: chain dependent ops to use registers instead of global memory

2. **Memory Compression (1.5 TB → 16 GB)**
   - Weight matrices in FHE get packed diagonally with power-of-2 strides
   - This creates cyclic symmetry in NTT-domain representations
   - Cerium exploits this: 96× compression for BERT, 119× for Llama3-8B
   - Without this, LLM inference is literally impossible

3. **Multi-GPU Orchestration**
   - FHE's "keyswitching" operation dominates runtime and requires communication
   - Cerium merges scatter+gather into all-reduce (44% less communication)
   - Overlaps compute and communication via stream scheduling

**The Result:**
- 7.5ms bootstrapping (first sub-10ms on real hardware)
- BERT-Base: 8.8 seconds encrypted inference
- Llama3-8B: 134 seconds (first-ever demonstration)
- Matches CraterLake ASIC performance with commodity GPUs

---

## Q2: The Key Insight

**The authors' central insight is that reasoning about kernel fusion at the *limb level* (RNS polynomial residues) rather than at the ciphertext or kernel level enables tractable, automated optimization of FHE programs on GPUs.**

This is non-obvious because FHE practitioners typically think at the ciphertext abstraction (add/multiply/rotate), while GPU programmers think at the kernel level (thread blocks, registers, memory hierarchy). Neither abstraction is right for fusion.

**Why limbs work:**
From Section IV-C: "Limbs are well suited for this task as limb operations (i) form the atomic building blocks of RNS-CKKS, (ii) are data parallel, (iii) can be grouped into classes that require significantly distinct kernels that cannot be fused, (iv) are largely data independent across RNS bases, and (v) mostly data dependent within the same RNS base."

The limb IR provides a Goldilocks zone—abstract enough to reason about fusion correctness (cycle detection, cross-block dependencies) via simple graph operations, yet concrete enough to predict resource pressure (registers, shared memory) and generate efficient code.

**Validating the insight (Section V-E1):**
- Horizontal fusion alone: 1.84–2.04× speedup
- Adding vertical fusion: additional 1.43–1.68×
- Combined: up to 3.32× over unfused baseline

The insight extends to memory: the "sparse compressed plaintext encoding" (Section IV-E) similarly exploits structure at the polynomial representation level—power-of-2 stride redundancies create NTT-domain symmetries that enable 100×+ compression. This transforms Llama3-8B from "impossible" (112 TB) to "feasible" (982 GB).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Benchmark Coverage (Table I)**
The evaluation spans four orders of magnitude in model size: bootstrapping (a single FHE primitive), ResNet-20 (small CNN), BERT-Base (110M params), and Llama3-8B. This demonstrates generality—the same compiler framework handles diverse workloads without manual kernel engineering.

**2. Ablation Studies with Clear Causality (Figure 11)**
Section V-E methodically isolates each optimization:
- Horizontal fusion alone: 1.84× (ResNet-20)
- Adding vertical fusion: +1.43×
- CudaGraphs: +11% average
- Memory scheduling: 2.3–2.5× over online allocation
- Sparse compression: 3.25× for BERT, enables Llama3-8B entirely

Each number has a clear baseline, making the contribution of each technique measurable.

**3. Real Hardware, Multiple Generations (Table I)**
Testing on A100, H100, and B200 across 1/2/4/8 GPU configurations demonstrates scaling behavior and rules out generation-specific effects.

**4. ASIC Comparison with Normalized Metrics (Figure 10)**
Comparing against CraterLake, ARK, and Cinnamon provides context against purpose-built hardware. Matching CraterLake (1.06×) with commodity GPUs is a strong practical result.

### Weaknesses

**1. Missing Accuracy/Precision Validation Details**
The paper claims "69.3% accuracy on GLUE RTE matching plaintext" (Section V-A) for BERT, but provides no error analysis for FHE-induced precision degradation. CKKS is *approximate* homomorphic encryption—where is the precision budget accounting? For Llama3-8B, no accuracy numbers are reported at all, only that they "do not use any modifications like LoRA that require retraining." Did the encrypted output actually match plaintext? This is critical for validity.

**2. ASIC Comparison Has Significant Caveats**
From Section VI: "as FHE ASICs are statically scheduled, they cannot support workloads like Llama3-8B that exceed accelerator memory." This is a fundamental capability difference, not just a performance delta. The comparison in Figure 10 only includes Bootstrap, ResNet-20, and BERT—workloads that *do* fit on ASICs. The 4.4× gap to Cinnamon-8 for workloads where both can execute is more representative than the Llama3-8B "first demonstration" claim.

**3. Multi-GPU Scaling Efficiency Is Modest**
From Table I: 8×B200 bootstrap is 7.5ms vs 14.5ms for 1×B200 = 1.93× speedup with 8× resources (24% efficiency). ResNet-20: 1.53× with 8×GPUs (19% efficiency). BERT shows better scaling (3.2× with 8×), but this suggests communication overhead dominates for compute-bound kernels. Figure 11(d) shows Cinnamon's algorithms alone are *slower* than single-GPU until Cerium's scheduling is added.

**4. Sparse Compression Requires Specific Packing Strategies**
Section IV-E notes: "efficiently implementing large models in FHE requires creating packing strategies where the repetition stride is a power of two." This is a constraint on *how* models must be implemented, not a general optimization. The paper doesn't discuss what happens when weight matrices don't naturally have this structure or the engineering effort required to ensure it.

**5. No Energy/Cost Analysis**
8×B200 GPUs cost roughly $200K+ and consume ~5.6kW TDP. FHE ASICs target efficiency. A $/inference or J/inference comparison would strengthen the "GPUs are practical" argument.

---

## Q4: What the Authors Didn't Tell You

**1. The Simulation Gap Doesn't Apply—But the RTL Validation Gap Does**

This paper uses *real hardware* (DGX systems), which is excellent. However, the compiler generates fused GPU kernels automatically. Where is the verification that the generated code is correct? Section V mentions "≈25,000 lines of C++ compiler," but there's no discussion of:
- Formal verification of fusion correctness
- Testing methodology for generated kernels
- Coverage of edge cases in limb IR transformations

The fusion rules in Section IV-C (cycle detection, base ID alignment, cross-block dependency checking) are described algorithmically but not formally proven. A single bug in the code generator could produce *silently wrong* encrypted results.

**2. CudaGraph Creation Time Is Suspiciously Fast**

Table II shows Llama3-8B CudaGraph creation takes 24.45 seconds. But Section IV-H2 admits: "CudaGraph creation is expensive, and therefore, the graph cannot be created online." The resolution is that graphs are created *per-CeriumFunction* and reused. This works because Cerium's memory layout design allows "a single update to the memory pool plaintext weight pointers."

What they don't tell you: this design locks you into Cerium's memory layout. You can't incrementally adopt Cerium for parts of your FHE pipeline—it's all-or-nothing. This is a systems design tradeoff they don't discuss.

**3. The 112 TB Llama3-8B Footprint Is *Before* Compression**

Figure 2 shows Llama3-8B at 10^5 GB (100 TB) FHE footprint. Section V-E3 says compression achieves 119×, reducing to 982 GB. But the 134-second runtime (Table I) uses 8×B200 GPUs, each with 192GB HBM = 1.5 TB total GPU memory. 

So 982 GB of weights fits... but what about evaluation keys ("10-100 GBs," Section II), bootstrap matrices, and intermediate ciphertexts ("several GBs per function," Section IV-H1)? The paper says intermediates are "shared by all" (Figure 9), but the actual memory breakdown for Llama3-8B is never disclosed. Given the memory pinning/prefetching complexity described in Section IV-H3, the working set orchestration is likely fragile.

**4. Polynomial Approximations Are Hidden Complexity**

Section II notes: "CKKS cannot natively express any nonlinear function. Therefore nonlinearities like division, ReLU, max, softmax, SiLU, etc require the use of polynomial approximations whose degree and precision must be tailored to model accuracy requirements."

For BERT, they cite [19], [29], [38] for approximations. For Llama3-8B with SiLU activations, no details are given. The *choice* of polynomial approximation dramatically affects both accuracy and performance (higher degree = more multiplications = more bootstrapping). This is swept under the rug.

**5. The "First Llama3-8B" Claim Needs Context**

The paper demonstrates encrypted inference of "the decoder blocks" for "128 token prompt to generate the first token." This is prefill only—no autoregressive generation, no KV-cache management across tokens. Generating 100 tokens would take 13,400+ seconds (3.7+ hours) with no optimizations for the sequential nature of generation. The paper's framing suggests breakthrough capability; the reality is a proof-of-concept.