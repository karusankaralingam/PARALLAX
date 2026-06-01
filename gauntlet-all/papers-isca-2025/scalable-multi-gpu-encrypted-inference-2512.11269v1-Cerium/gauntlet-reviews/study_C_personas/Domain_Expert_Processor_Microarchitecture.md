# Paper Deconstruction: Cerium - A Scalable Multi-GPU Framework for Encrypted Large-Model Inference

## Q1: Whiteboard Explanation

Let me sketch this out like we're at a conference poster session.

**The Problem Space:**
Fully Homomorphic Encryption (FHE) lets you compute on encrypted data—the holy grail for privacy-preserving ML. But it's devastatingly slow: 10,000× overhead versus plaintext. Prior work built custom ASICs (CraterLake, ARK, Cinnamon), but those require bleeding-edge fab processes with HBM and CoWoS packaging. Meanwhile, everyone has GPUs sitting in datacenters.

**Why GPUs Have Struggled:**
The naive approach—map each FHE ciphertext operation to a GPU kernel—leaves massive performance on the table. You get crushed by:
1. Kernel launch overhead (thousands of tiny kernels)
2. Memory bandwidth (shuffling data to/from global memory between kernels)
3. Memory capacity (a BERT model encrypted becomes 1.5TB; Llama3-8B becomes 112TB)

**Cerium's Architecture (Figure 3):**

```
DSL Program → Polynomial IR → Limb IR → Fused Limb IR → GPU Kernels
                    ↓              ↓            ↓
              [Hoisting]    [Horizontal   [Code Gen with
               [Min-KS]      Fusion]       Register/BW
                             [Vertical     Optimization]
                              Fusion]
```

**The Core Mechanism:**
FHE operations decompose into "limb" operations (think: working in a residue number system where big integers become vectors of small integers). These limbs are data-parallel and mostly independent across RNS bases. Cerium introduces **Limb IR**—an intermediate representation that makes reasoning about kernel fusion tractable.

1. **Horizontal Fusion**: Pack independent limb ops with the same opcode into one kernel (different thread blocks). Amortizes launch overhead.

2. **Vertical Fusion**: Chain dependent limb ops within the same thread, communicating via registers instead of global memory.

**The Memory Problem (Figure 2):**
The paper's Figure 2 is the real "oh shit" moment. ResNet-50 needs ~100GB. BERT-Base needs 1.5TB. Llama3-8B needs 112TB. That's not fitting in your H100's 80GB.

**Sparse Compressed Plaintext Encoding (Section IV-E, Figure 7):**
When you pack weight matrices for FHE using Baby-Step Giant-Step, you create redundant copies with power-of-2 strides. Cerium exploits the resulting symmetry: after inverse FFT encoding, you get sparse polynomials. After NTT, you get repeated blocks. Store only the unique values → 96-119× compression.

**Multi-GPU Coordination (Figure 8):**
They inherit limb-level parallelism from Cinnamon but add compiler passes to merge aggregate-scatter + all-gather into all-reduce operations, cutting 44% of inter-GPU communication.

---

## Q2: The Key Insight

The fundamental insight is **not** "use GPUs for FHE" (everyone's tried that). It's that **the limb level is the right abstraction for automated kernel fusion.**

Prior GPU FHE work (Cheddar, TensorFHE) hand-fused kernels in an application-specific, ad-hoc manner. The moment you change model dimensions, sequence length, or approximation degree for a nonlinearity, you need to re-engineer your kernels from scratch.

Cerium observes that:
1. RNS-CKKS limb operations are data-parallel and fall into a small number of kernel "classes" (NTT, Base Conversion, Elementwise—see Figure 6)
2. Limbs across RNS bases are largely independent (horizontal fusion is safe)
3. Limbs within the same RNS base have clear dependency chains (vertical fusion decisions are tractable)

This means you can express fusion constraints as graph properties on a Limb IR DAG, check for cycles efficiently (parent/child set intersection), and automatically generate fused kernels. The result: a compiler that produces kernels matching or beating expert hand-tuning, but generalizing across arbitrary FHE circuits.

**The second key insight** is that memory compression isn't just an optimization—it's an enabler. Without the 100×+ reduction from sparse plaintext encoding, Llama3-8B FHE inference is physically impossible (112TB exceeds any plausible host memory). The compression makes the previously impossible merely difficult.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. End-to-End System Evaluation on Real Hardware:**
Table I shows actual execution times on DGX A100, H100, and B200 systems. These aren't simulator numbers—they're running on hardware you can buy. The 7.5ms bootstrap on 8×B200 is a genuine milestone (Section V-C: "first to demonstrate a sub-10 ms bootstrapping on real hardware").

**2. Meaningful Baselines:**
- vs. Cheddar [10]: The state-of-the-art hand-optimized GPU library. Cerium beats it 1.21× on 1×A100, 2.24× with multi-GPU (Section V-B).
- vs. FHE ASICs: Figure 10 shows Cerium matching CraterLake (1.06×) and within 2.3-4.4× of more recent ASICs. This is a fair comparison—they're not just beating strawmen.

**3. Ablation Studies Are Thorough (Section V-E):**
Figure 11 decomposes contributions:
- Horizontal fusion: 1.84-2.04× speedup
- Vertical fusion: additional 1.43-1.68×
- CudaGraphs: 7-14% improvement
- Memory scheduling: 2.3-2.5× (avoiding online allocation)
- Sparse compression: 3.25× for BERT, and enables Llama3-8B entirely

**4. Compilation Time is Reported (Table II):**
BERT compiles in 2min 47s, Llama3-8B in 11min 29s. This matters—if compilation took hours, the "automatic" story would be undermined.

**5. Accuracy is Verified:**
ResNet-20 achieves 91.4% (matching plaintext), BERT-Base achieves 69.3% on GLUE RTE (matching plaintext). They're not just running garbage through the system.

### Weaknesses

**1. The ASIC Comparison Has Caveats:**
The paper compares against simulated/projected ASIC performance (CraterLake, ARK, Cinnamon are not fabricated chips). They're comparing real GPU measurements against paper designs. That said, this is standard practice when ASICs don't exist commercially.

**2. The B200 Hardware is Bleeding-Edge:**
The best numbers (7.5ms bootstrap, 8.8s BERT, 134s Llama3-8B) use 8×B200 GPUs. The B200 has ~2× the memory bandwidth of H100. Most readers don't have B200s. The A100 numbers (16.5ms bootstrap, 19.6s BERT, 341s Llama3-8B) are more representative of deployable systems.

**3. Llama3-8B Baseline is Weak:**
For Llama3-8B, they compare against Nexus [38], which "estimates performance by aggregating layer runtimes, without accounting for memory overheads" (Section V-B). This makes the 34.47× speedup impressive but not strictly apples-to-apples.

**4. Single-Token Generation Only:**
For Llama3-8B, they run "decoder blocks... to generate the first token" (Section V-A). This is inference on a 128-token prompt for a single output token. Autoregressive generation would require bootstrapping the KV cache repeatedly, which they don't address.

**5. Memory Capacity Still Requires Host Memory:**
Llama3-8B needs 982GB even after 119× compression (Section V-E3). This still requires prefetching from host memory, and the 12.1× speedup from memory management (comparing to naive UVM) shows how much time is spent on data movement.

**6. No Power/Energy Analysis:**
They compare performance but not performance-per-watt against ASICs. An 8-GPU DGX B200 draws ~10kW. ASICs would likely win dramatically on efficiency.

---

## Q4: What the Authors Didn't Tell You

**1. The Sparse Compression Requires Application Cooperation:**
The 100× memory reduction only works when "the repetition stride is a power of two" (Section IV-E). The DSL requires programmers to declare `repeatStride=256` (Figure 4). If your packing strategy doesn't naturally produce power-of-2 strides, you don't get compression. The paper says: "efficiently implementing large models in FHE requires creating packing strategies where the repetition stride is a power of two." This is a constraint on the ML model/packing design, not just a transparent optimization.

**2. Multi-GPU Scaling Hits Diminishing Returns:**
Look at Table I carefully. Bootstrap goes 14.5ms (1×B200) → 7.5ms (8×B200): only 1.93× speedup for 8× the hardware. ResNet-20 goes 456ms → 298ms: 1.53× speedup. BERT goes 28.3s → 8.8s: 3.2× speedup. The scaling efficiency varies wildly by workload, and they don't discuss the root causes.

**3. The Compiler Partitions Large DAGs:**
Section IV-C mentions: "if a function's limb IR DAG is too large, the compiler partitions it into smaller sub-DAGs and performs fusion independently within each." This means fusion decisions are local, potentially missing global optima. They "experimentally pick the default sub-DAG size" but don't report what that is or sensitivity analysis.

**4. CudaGraph Reuse Has Constraints:**
Section IV-H2 says CudaGraphs are reused by "just requiring a single update to the memory pool plaintext weight pointers." This works because of Cerium's specific memory layout design—but it implies that more dynamic workloads (variable sequence lengths, different batch sizes) might require graph regeneration. They don't discuss this limitation.

**5. The 100ms BERT on GPU vs. 91s BOLT Comparison is Misleading:**
Section VI mentions BOLT [29] requires 91s for 128-token BERT inference on a LAN due to MPC communication. But BOLT is a hybrid HE+MPC approach providing different security guarantees (two-party computation vs. pure FHE). The comparison conflates apples and oranges.

**6. FHE ASICs Can't Do Llama3-8B Either:**
Section VI drops this quietly: "as FHE ASICs are statically scheduled, they cannot support workloads like Llama3-8B that exceed accelerator memory and require host-to-accelerator memory orchestration." This is a major point—Cerium's host-GPU orchestration is necessary for large models, and ASICs (as designed in papers) don't have this capability. The ASIC comparison is thus only valid for smaller models.

**7. Accuracy Numbers Need Context:**
BERT-Base achieves 69.3% on GLUE RTE. But RTE is a small dataset (2,490 examples). The baseline BERT-Base can achieve ~66-70% depending on fine-tuning. We don't know if the FHE accuracy loss is from the polynomial approximations or just noise. They don't report accuracy on larger/harder benchmarks.

**8. The "First" Claims Have Scope:**
"First to break 10ms for bootstrapping" and "first FHE Llama3-8B inference" are qualified by the specific parameter choices (N=64K, 128-bit security, specific RNS basis). Different parameter choices might shift these numbers significantly.