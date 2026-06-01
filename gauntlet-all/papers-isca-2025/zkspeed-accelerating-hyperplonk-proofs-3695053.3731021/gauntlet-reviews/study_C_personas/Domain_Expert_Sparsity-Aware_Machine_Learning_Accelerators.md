# Paper Analysis: zkSpeed - Accelerating HyperPlonk for Zero-Knowledge Proofs

## Q1: Whiteboard Explanation

Let me draw this out for you as if we're standing at a whiteboard.

**The Problem:** Zero-Knowledge Proofs (ZKPs) let you prove you computed something correctly without revealing your secret inputs. Think of it as proving you solved a Sudoku puzzle without showing anyone your solution. The catch? The "prover" side is computationally brutal—we're talking minutes to hours for real applications.

**Where HyperPlonk Fits:** There's a zoo of ZKP protocols, each with different tradeoffs. The paper draws a useful triangle in Section 1:
- **Groth16**: Tiny proofs (192 bytes), millisecond verification, BUT needs a fresh "trusted setup ceremony" for every new application. If that ceremony is compromised, game over.
- **Orion**: No trusted setup, fast prover, BUT 8MB proofs—bigger than an Ethereum block!
- **HyperPlonk**: The Goldilocks zone—~5KB proofs, low verification cost, AND a "universal setup" that works for all applications forever.

**The Core Computational Bottleneck:** HyperPlonk's prover must do two expensive things:
1. **SumCheck** (bandwidth-bound): Imagine a polynomial with millions of variables. You need to prove the sum over all binary assignments (0 or 1) of each variable equals some claimed value. This is done round-by-round, streaming through massive lookup tables called "MLE tables" (Multi-Linear Extension tables). Each round halves the table size but requires reading/writing everything.

2. **MSM - Multi-Scalar Multiplication** (compute-bound): A dot product between huge vectors of 255-bit numbers and elliptic curve points (381-bit). This "commits" the prover to their polynomial values cryptographically.

**The Architecture at a Glance (Figure 2B):**
- Eight specialized hardware units connected via a multi-channel shared bus
- Global SRAM stores compressed input MLEs (exploiting that control signals are binary)
- HBM provides the bandwidth lifeline for streaming SumCheck data
- Units pipeline together: e.g., FracMLE feeds both MSM and ProdMLE simultaneously

**The Key Insight:** HyperPlonk's dataflow is *data-oblivious*—the schedule is fixed regardless of input values. This means zkSpeed can statically orchestrate everything without runtime decisions, enabling deep pipelining and overlapped communication/computation.

---

## Q2: The Key Insight

**The Delta (Real Contribution):** This is the *first* hardware accelerator for HyperPlonk, but more importantly, the paper's actual innovations lie in three mechanism-level contributions:

**1. Unified SumCheck PE Design (Section 4.1):** HyperPlonk requires three *different* SumCheck polynomials—ZeroCheck (Eq. 3), PermCheck (Eq. 4), and OpenCheck (Eq. 5)—each with different structure and degree. The paper's insight is that these share a "sum-of-products of multilinear polynomials" pattern. They design a single unified PE (using HLS to share 94 modular multipliers instead of 184) that handles all three variants. The trick is computing all polynomial evaluations *in parallel* before forming products, eliminating redundant computation present in the CPU baseline (Section 4.1.1, Figure 4).

**2. Batched Modular Inversion with Tree-Based Amortization (Section 4.4):** The Fraction MLE computation requires inverting every element of a denominator polynomial—a 509-cycle operation per element using the Binary Extended Euclidean Algorithm. The paper adapts Montgomery batching but replaces the sequential partial-product chain with a **multiplier tree** (Section 4.4.2), reducing latency from O(b) to O(log₂b). They then analytically optimize batch size to b=64 (Figure 8), achieving minimum latency imbalance and area simultaneously.

**3. Hybrid DFS/BFS Tree Traversal for MLE Operations (Section 4.3):** The CPU baseline uses breadth-first traversal for tree-structured computations (Build MLE, MLE Evaluate, Product MLE). This is memory-catastrophic—a 2²³ problem requires 128MB for intermediates at one level. zkSpeed uses depth-first traversal in upper levels (for memory reuse) and breadth-first in lower levels (for parallelism), keeping intermediate data on-chip.

**What This Is NOT:** This is not about sparsity in the ML-accelerator sense. It's about taming a different kind of irregularity—the heterogeneous polynomial structure and streaming memory access patterns unique to HyperPlonk.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Design Space Exploration (Figure 9, Table 2):** The authors sweep thousands of configurations across 7 bandwidth levels (64 GB/s to 4 TB/s), constructing local and global Pareto curves. This is the right methodology for a heterogeneous accelerator—they're not just reporting one magic design point.

**2. Honest Bandwidth Sensitivity Analysis (Section 7.1-7.2, Figures 9-11):** The paper explicitly shows that SumCheck is bandwidth-bound while MSM is compute-bound. Figure 11 cleanly demonstrates that adding MSM PEs scales nearly linearly regardless of bandwidth, while SumCheck PEs plateau after saturating memory. This informs the Pareto-optimal resource allocation (e.g., 16 MSM PEs but only 2 SumCheck PEs in the final design).

**3. Iso-Area Comparison (Section 7.3):** Comparing against a 296mm² AMD EPYC die (excluding I/O die) and matching zkSpeed to similar area is a fair baseline. The 801× geomean speedup (Table 3) is believable given the kernel profiling in Table 1.

**4. Real Workload Evaluation (Table 3):** Testing on ZCash, Zexe, and Rollup workloads—not just synthetic benchmarks—shows practical relevance. The 720×-862× speedup range is consistent across workloads.

### Weaknesses

**1. Missing GPU Baseline:** The paper compares only to CPU (Section 7.3). Given that GZKP [41] and cuZK [40] have accelerated ZKP primitives on GPUs, and that HBM bandwidth is available on high-end GPUs, the absence of a GPU comparison is glaring. An A100 has 2 TB/s HBM2e—exactly what zkSpeed assumes. The authors cite GZKP but don't compare against it.

**2. Synthetic Workload Dependence (Section 6.2):** The authors acknowledge "there is no publicly available compiler to generate real workloads" for HyperPlonk. Their benchmark methodology relies on "pessimistic 10% dense scalars" (Section 7.4), but this is borrowed from Groth16 statistics, not measured on HyperPlonk circuits. The witness sparsity directly affects Sparse MSM runtime.

**3. NoCap Comparison Is Apples-to-Oranges (Table 4):** The comparison against NoCap (Spartan+Orion) in Table 4 is methodologically suspect. NoCap uses 64-bit Goldilocks primes; zkSpeed uses 255/381-bit fields. NoCap achieves 38.73mm² because it doesn't need MSMs at all. Claiming "10× area cost in return for three orders-of-magnitude reduction in proof size" is technically true but misleading—these are fundamentally different protocols targeting different applications.

**4. Verification Cost Understated:** Table 4 shows HyperPlonk verification at 26ms versus Groth16 at 4.2ms—a 6× penalty. In blockchain consensus (the primary application), verification happens at *every node*. The paper handwaves this by emphasizing "universal setup" but doesn't quantify the aggregate cost.

**5. HBM PHY Area Accounting:** The authors include 59.2mm² for 2 HBM3 PHYs (Table 5), which is significant (16% of total). However, Section 7.3.2 states they *exclude* PHY cost when comparing to CPU ("since the AMD EPYC processor has its own separate die for I/O"). This is inconsistent—either include I/O costs for both or neither.

---

## Q4: What the Authors Didn't Tell You

**1. The SHA3 Serialization Problem:** Section 3.3.6 reveals that SHA3 acts as an "order-enforcing mechanism"—protocol steps *must* execute in series because challenges depend on previous transcript hashes. The SHA3 unit has <0.01% utilization (Figure 13) but sits on the critical path between every major step. The paper doesn't discuss whether pipelining across proof instances could amortize this, or whether alternative hash functions (like Poseidon, cited in [24]) could reduce this bottleneck.

**2. The O(n) vs O(n log n) Claim Is Nuanced:** The abstract claims HyperPlonk's SumCheck is O(n) versus NTT's O(n log n). This is asymptotically true, but Table 1 shows HyperPlonk's CPU prover (145.5s for 2²⁴ gates) is *slower* than Groth16 (51.18s). The constant factors are brutal—255-bit modular multiplications versus 64-bit NTT butterflies. The asymptotic advantage only materializes at scale, which the paper doesn't characterize.

**3. The MLE Storage Compression Assumption:** Section 4.6 claims 10-11× compression on MLE storage because "control MLEs are binary" and witness MLEs are "90% 1s and 0s." This is application-dependent. For neural network verification workloads (increasingly important for ZKPs [4]), witness values are *not* sparse—they're quantized activations. The compression ratio would collapse.

**4. Power Density Is Borderline:** Table 5 reports 170.88W total power for 366.46mm², yielding 0.46 W/mm². The paper claims this is "within that of our CPU" but doesn't acknowledge this is at the high end for sustained operation. The MSM unit alone consumes 76W for 105mm² (0.72 W/mm²), which is aggressive.

**5. The Jellyfish Escape Hatch (Section 8):** The paper briefly mentions Jellyfish—a HyperPlonk variant with higher-arity gates—could improve the MLE table count/size ratio. This sounds like an implicit acknowledgment that HyperPlonk's polynomial structure isn't optimal for hardware. The authors punt this to "future work."

**6. Modular Multiplier Sharing Across Units:** Section 4.5 mentions MLE Combine shares resources with other operations, saving 41% area. But Figure 13 shows MLE Combine has only 5.85% area utilization and runs sequentially with OpenCheck. The actual scheduling constraints for this sharing aren't fully characterized—if any step takes longer than expected, the sharing breaks down.

**The Bottom Line:** zkSpeed is a solid first-generation accelerator for HyperPlonk with thoughtful microarchitecture. But the evaluation strategy—CPU-only baseline, synthetic workloads, and cross-protocol comparisons—leaves open questions about competitiveness with GPUs and real-world deployment. The 801× speedup is real but contextual: it's against single-threaded CPU reference code, not optimized multi-core or GPU implementations.