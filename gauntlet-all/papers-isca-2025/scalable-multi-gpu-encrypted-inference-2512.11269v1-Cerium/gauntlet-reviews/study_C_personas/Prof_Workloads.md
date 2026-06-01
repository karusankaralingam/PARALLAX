## Q1: Whiteboard Explanation

**What is this paper actually doing?**

Imagine you want to run a neural network on someone else's data without ever seeing the data in plaintext. Fully Homomorphic Encryption (FHE) lets you compute on encrypted values directly—but it's catastrophically slow (10,000× slower than plaintext). Prior work built custom ASICs to fix this, but those require cutting-edge fabs and HBM, making them inaccessible.

**Cerium's pitch:** "We can get ASIC-level FHE performance using off-the-shelf datacenter GPUs."

**The three-headed monster they're fighting:**

1. **Kernel Optimization:** FHE operations (NTT, keyswitching, rotations) map poorly to GPUs if you launch one kernel per operation. Prior work hand-crafted fused kernels per application—not scalable.

2. **Memory Capacity:** Encrypted LLMs explode in size. BERT-Base: 1.5 TB of encoded weights. Llama3-8B: 112 TB. This doesn't fit anywhere without compression.

3. **Multi-GPU Coordination:** Scaling FHE across GPUs requires minimizing inter-GPU communication, which dominates runtime if done naively.

**Cerium's solution stack:**

- **DSL + Compiler:** User writes FHE circuits in Python; compiler automatically performs horizontal/vertical kernel fusion at the "Limb IR" level (RNS polynomial residues), generates optimized CUDA kernels, and builds CudaGraphs.

- **Sparse Plaintext Compression:** Exploits power-of-2 redundancy patterns in packed weight matrices to achieve 96-119× compression, making LLMs tractable.

- **Runtime:** Memory pooling, liveness analysis for buffer reuse, prefetching for host-GPU orchestration, and multi-GPU scheduling with communication/compute overlap.

**The end result:** Bootstrap in 7.5ms (first sub-10ms on real hardware), BERT-Base in 8.8s, Llama3-8B in 134s—matching or approaching custom ASICs.

---

## Q2: The Key Insight

**The central insight is architectural, not algorithmic:**

*FHE performance on GPUs isn't limited by GPU compute capability—it's limited by the abstraction mismatch between FHE's circuit-level operations and GPU's need for large, fused, memory-efficient kernels.*

Prior GPU FHE libraries (Cheddar, TensorFHE) attacked this by hand-optimizing kernels per application. Cerium recognizes that **Limb-level operations**—the RNS residues that form FHE's atomic building blocks—are the right abstraction for reasoning about fusion. Limbs are:
- Data-parallel across RNS bases (perfect for GPUs)
- Mostly data-independent across bases (safe horizontal fusion)
- Mostly data-dependent within a base (guides vertical fusion constraints)

This lets the compiler systematically decide fusion boundaries by checking for cycles and cross-thread-block dependencies, rather than requiring human intuition.

**The second insight is about memory, not compute:**

For large models, the real bottleneck isn't FLOPS—it's the 112TB of encoded weights for Llama3-8B. The sparse plaintext compression exploits a mathematical symmetry: power-of-2 strided redundancies in diagonal packing (used for Baby-Step Giant-Step matrix multiplication) create sparse polynomials whose NTT form has contiguous repeated blocks. Compressing these blocks achieves 96-119× reduction—the difference between "impossible" and "runnable."

**Why it matters:** This reframes FHE acceleration from "build faster hardware" to "build smarter compilers that understand both FHE's algebraic structure and GPU's architectural constraints."

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Benchmark Coverage (Commendable)**

Unlike prior GPU FHE work that stopped at ResNet-20 (Section V-A), Cerium evaluates on bootstrapping, ResNet-20, BERT-Base, and Llama3-8B. This is the first end-to-end encrypted Llama3-8B inference reported anywhere (Table I). They also report accuracy numbers (91.4% for ResNet-20, 69.3% for BERT on GLUE RTE—matching plaintext), addressing the common criticism that FHE papers ignore model quality.

**2. Ablation Study is Thorough (Figure 11)**

Section V-E systematically isolates contributions:
- Horizontal+vertical fusion: 2.63-3.32× speedup
- CudaGraphs: 7-14% speedup
- Sparse compression: 3.25× for BERT, enables Llama3-8B entirely
- Memory scheduling: 2.3-2.51× speedup

This is the right way to validate a compiler paper—show what breaks when you remove each piece.

**3. Multi-GPU Scaling Analysis is Honest (Section V-E5)**

They show that naively applying Cinnamon's parallel keyswitching algorithms *hurts* performance (1.2× slower than single GPU!). Only with their scheduling optimizations does multi-GPU help. This honest reporting of failure modes increases credibility.

**4. Comparison Against Multiple Baselines**

They compare against Cheddar (hand-optimized GPU), THOR/Nexus (prior LLM FHE work), and three ASICs (CraterLake, ARK, Cinnamon). Figure 10 normalizes to Cinnamon-8, giving a clear apples-to-apples view.

### Weaknesses

**1. The "Cherry-Pick" Check: Benchmark Selection Concerns**

The benchmark set is dominated by dense, regular workloads: CNNs and Transformers with fixed sequence lengths (128 tokens). What about:
- **Sparse models?** Mixture-of-experts, sparse attention patterns
- **Variable-length inputs?** The 128-token fixed sequence length for BERT/Llama is convenient but unrealistic for production
- **Pointer-chasing FHE patterns?** Any workload with data-dependent control flow (though FHE fundamentally struggles here)

The sparse plaintext compression (Section IV-E) requires power-of-2 stride redundancies. What fraction of real FHE applications satisfy this constraint? The paper doesn't quantify this.

**2. The Baseline Validity Problem: ASIC Comparisons**

Figure 10 compares against CraterLake, ARK, and Cinnamon. But:
- **CraterLake (ISCA'22) and ARK (MICRO'22) are 3 years old.** Comparing 2025 GPUs (B200) against 2022 ASIC designs is temporally unfair.
- **The ASICs use 7nm/5nm; B200 is 4nm.** Process node advantages are bundled into the "GPU wins" narrative.
- **Cinnamon-8 uses 8 ASICs; Cerium uses 8 GPUs.** The cost/power comparison is absent. An 8×B200 DGX system costs ~$200K+ and draws 5-10kW. What's the ASIC equivalent?

The paper claims "GPUs are accessible, ASICs aren't" (Section I), but never quantifies TCO or power efficiency.

**3. The "Zero-Event" Reality Check: Memory Compression**

The sparse plaintext compression achieves 96-119× reduction for diagonal-packed matrices. But:
- **Does this generalize?** The paper states "efficiently implementing large models in FHE requires creating packing strategies where the repetition stride is a power of two" (Section IV-E). This shifts burden to the application developer.
- **What's the runtime overhead?** Decompression happens at kernel execution time (index transformations). The paper doesn't isolate this cost.

**4. Missing Multi-GPU Scaling Curve**

Table I shows 1×/2×/4×/8× GPU configurations. For Llama3-8B on B200:
- 1 GPU: 253s
- 8 GPUs: 134s

That's only 1.89× speedup for 8× resources (23.6% efficiency). The paper buries this in the table without discussing why scaling is so poor. Section V-E5 only analyzes bootstrapping, not the large models where scaling matters most.

**5. Latency vs. Throughput Conflation**

All numbers are single-inference latency. For datacenter deployment, throughput matters. Can Cerium batch multiple inferences? What's the throughput/latency tradeoff? This is unaddressed.

**6. Compilation Time Hidden in Supplementary**

Table II shows Llama3-8B takes 11 minutes to compile. This is actually impressive, but the paper doesn't discuss:
- How does compile time scale with model size?
- Is recompilation needed for sequence length changes?
- What about the CudaGraph creation overhead for dynamic models?

---

## Q4: What the Authors Didn't Tell You

**1. The Accuracy Story is Incomplete**

Section V-A claims "encrypted accuracy of 69.3% on GLUE RTE, matching the plaintext model." But:
- GLUE RTE is a small dataset (2.5K examples). What about harder GLUE tasks (CoLA, MNLI)?
- The paper uses polynomial approximations for nonlinearities (softmax, GELU, SiLU). What's the approximation degree? What precision is lost?
- For Llama3-8B, they report inference time but **no accuracy numbers**. The paper states "we do not use any modifications like LoRA that require retraining" (Section V-A), suggesting they're running the original model—but FHE noise accumulation over 8B parameters is non-trivial. Did they validate outputs?

**2. The Real Competition Isn't ASICs**

The paper positions against FHE ASICs (CraterLake, ARK, Cinnamon). But the practical alternative to encrypted inference is:
- **Trusted Execution Environments (TEEs):** Intel SGX, AMD SEV, ARM TrustZone. These have ~1.1-2× overhead, not 10,000×.
- **MPC-based approaches:** BOLT (cited in Section VI) does BERT in 91s on LAN. Cerium's 8.8s is faster, but MPC has different trust assumptions.

The paper doesn't discuss when FHE is the right tool versus these alternatives.

**3. The Memory Hierarchy Game**

Llama3-8B requires 982GB of compressed weights (Section V-E3). With 8×B200 (80GB HBM each = 640GB total), weights must stream from host memory. The paper says "runtime prefetches weights for each layer before it is run" (Section IV-H3), but:
- What's the PCIe bandwidth utilization?
- How much time is spent waiting for prefetches?
- The 134s runtime—how much is compute-bound vs. memory-bound?

The breakdown in Figure 11(c) shows memory scheduling gives 12.1× speedup for Llama3-8B, but this is measuring "UVM without prefetching" as the baseline—a strawman that no reasonable system would use.

**4. The Kernel Fusion Heuristics**

Section IV-C describes horizontal/vertical fusion but the stopping criteria are vague:
- "Cerium estimates the number of registers required by a kernel and splits the kernel into smaller kernels when the estimated register count is above a threshold" (Section IV-D).
- What's the threshold? How was it tuned?
- "We experimentally pick the default sub-DAG size" (Section IV-C). What is it? How sensitive is performance to this choice?

The compiler is a black box. For reproducibility, these heuristics matter.

**5. The Encryption Parameters**

Section V-A specifies N=64K, 1782-bit ciphertext modulus, 28-bit RNS basis, H=32K Hamming weight. But:
- How were these chosen? Different parameter sets trade off security, precision, and performance.
- What's the noise budget consumption through Llama3-8B's 32 decoder layers? Are they close to decryption failure?
- The bootstrapping refreshes to level l=18 (Section V-A). Is this sufficient for all models? What if you need deeper circuits?

**6. The Softmax with Max Normalization**

Section V-A mentions "to achieve high accuracy, we implement softmax with max value normalization." This is interesting because computing max in FHE is expensive (requires comparisons via polynomial approximation). The paper doesn't:
- Explain how they implement max
- Report the cost of max-normalized softmax vs. non-normalized
- Discuss numerical stability implications

This is likely a significant engineering effort that's glossed over.

**7. The Open Source Promise**

"Open Source: The Cerium framework will be open-sourced following publication" (Section I). This is in future tense as of December 2025. Without code, the 25K lines of C++ compiler and 11K lines of CUDA runtime cannot be validated or extended. The reproducibility gap in FHE research is significant.