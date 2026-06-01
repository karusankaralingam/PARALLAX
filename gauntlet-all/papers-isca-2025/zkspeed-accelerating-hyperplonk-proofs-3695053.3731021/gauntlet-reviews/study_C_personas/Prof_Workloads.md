Q1: Whiteboard Explanation

Let me walk you through what zkSpeed actually does, as if I'm sketching this on a whiteboard.

**The Problem Setup:**
Zero-Knowledge Proofs (ZKPs) let a "prover" convince a "verifier" that a computation was done correctly—without revealing the secret inputs. HyperPlonk is a specific ZKP protocol that's attractive because: (1) it has small proof sizes (~5KB), (2) low verification costs, and (3) uses a "universal trusted setup" (do it once, reuse forever), unlike Groth16 which needs a new ceremony for each application.

**Why HyperPlonk is Hard to Accelerate:**
The prover phase is brutally expensive. You're computing over:
- Polynomials with 2^17 to 2^24 terms
- Field elements that are 255-381 bits wide (not your friendly 64-bit integers)
- The protocol has four sequential phases, each with different computational kernels

**The Two Main Computational Bottlenecks:**

1. **SumCheck** (memory-bound): An interactive protocol where the prover demonstrates it correctly computed a sum over a "boolean hypercube" (all 0/1 assignments of variables). The key insight: in each round, you're streaming through massive MLE (multilinear extension) tables, doing modular multiplications, then halving the table size. Data expands 100× after round 1 (binary → 255-bit values), so you can't store intermediate tables on-chip.

2. **MSM (Multi-Scalar Multiplication)** (compute-bound): Dot products between scalar vectors and elliptic curve points. Used for "commitments"—cryptographically binding the prover to their polynomials. They use Pippenger's algorithm which converts expensive point multiplications into many point additions (PADDs).

**zkSpeed's Architecture (Figure 2B):**
Eight specialized accelerator units connected via a shared bus:
- **SumCheck Unit**: Unified PE handling three SumCheck variants (ZeroCheck, PermCheck, OpenCheck)
- **MSM Unit**: Adapted from prior work (SZKP), with optimizations for bucket aggregation
- **Multifunction Tree Unit**: Handles tree-like computations (Build MLE, MLE Evaluate, Product MLE)
- **FracMLE Unit**: Computes fraction polynomials via batched modular inversion
- Plus: MLE Update, MLE Combine, Construct N&D, SHA3

The key architectural insight: SumCheck is bandwidth-starved (needs HBM at 2TB/s), while MSMs are compute-starved. The paper explores the Pareto frontier to balance area allocation between these competing demands.

---

Q2: The Key Insight

The authors' "aha moment" can be stated as:

**HyperPlonk's SumCheck operates on products of multilinear polynomials with shared terms across products, enabling massive data reuse if you compute all polynomial extensions in parallel rather than iterating term-by-term.**

From Section 4.1.1: "In HyperPlonk's CPU baseline, the boolean hypercube summations are performed iteratively term-by-term, incurring redundant computation for these repeating polynomials. We address this by computing all evaluations for each polynomial in parallel."

Look at Equation 3 (f_zero): the polynomial f_z1 appears in *every* term. The CPU implementation evaluates it repeatedly—5 times for 5 terms. zkSpeed's SumCheck PE computes f_z1's extensions once and reuses them across all terms, saving 48.9% in modular multipliers via resource sharing (Section 4.1.4).

**Why prior accelerators couldn't do this:**
NoCap (Section 4.1.5) accelerates Spartan's SumCheck, but Spartan uses simpler polynomials (degree 2-3, up to 2 terms). HyperPlonk's Plonk encoding requires control polynomials (q_L, q_R, q_M, q_O, q_C) that create heterogeneous, higher-degree terms. NoCap's vector architecture with Beneš networks would struggle with the irregular communication patterns and intermediate value pressure that HyperPlonk's structure demands.

The secondary insight: **A hybrid DFS/BFS tree traversal for the Multifunction Tree Unit** (Section 4.3.2) eliminates the need to store entire intermediate levels. The CPU's BFS traversal for a 2^23 problem would require 128MB just for intermediates at one level—intractable. Their hybrid approach uses DFS for upper levels (sequential, reusing results immediately) and BFS for lower levels (parallel).

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive Design Space Exploration (Section 7.1, Figure 9):** They sweep 7 bandwidth scenarios (64 GB/s to 4 TB/s) and thousands of design configurations (Table 2), constructing proper Pareto frontiers. This isn't cherry-picking a single design point—they show *where* different bandwidth technologies make sense. The analysis in Figure 10 actually explains *why* area allocations shift across Pareto points.

2. **Honest Bandwidth Sensitivity Analysis (Figure 11):** They explicitly show that SumCheck speedups plateau after saturating bandwidth, while MSM scales with compute. This matches their analytical claim that SumCheck is memory-bound. The 512 GB/s curve shows diminishing returns at 4+ SumCheck PEs—they're not hiding the knee in the curve.

3. **Fair Baseline Comparison (Table 4):** They compare against SZKP (Groth16) and NoCap (Spartan+Orion), acknowledging that these protocols target *different* application domains with different tradeoffs. They don't claim HyperPlonk is universally superior—they note SZKP+ achieves 6× faster proving at the cost of circuit-specific setup.

4. **Real Utilization Data (Figure 13):** They show utilization varies from 70% to 5% across units, then explain *why* this is intentional (Pareto optimization for performance-per-area). They don't hide that SHA-3 sits nearly idle most of the time.

**Weaknesses:**

1. **The "Cherry-Pick" Check — Synthetic Workloads Only (Section 6.2):** "HyperPlonk was evaluated using mock circuit workloads [11], as there is no publicly available compiler to generate real workloads." Table 3 lists "real-world workloads" but these are scaled-up versions with *assumed* sparsity statistics (10% dense, 45% ones, 45% zeros). The Zcash and Rollup results are extrapolations, not actual circuit traces. The claim that "overall runtimes are effectively workload-agnostic" at iso-problem-size is only true because they've abstracted away the input distribution.

2. **Baseline Validity — CPU Comparison is Unoptimized (Section 6.1):** They compare against a 32-core AMD EPYC 7502, but the HyperPlonk reference implementation (per Table 1 links) is a generic Rust library designed for flexibility, not performance. There's no GPU baseline for HyperPlonk specifically. The 801× speedup claim is against an unoptimized software prover—prior work (GZKP [41]) shows GPU implementations can achieve significant speedups over CPU baselines for ZKPs.

3. **The "Zero-Event" Reality — Verifier Time Buried (Table 4):** They claim HyperPlonk has "low verification complexity," but verification takes 26ms vs. Groth16's 4.2ms (6× slower). For blockchain consensus with thousands of verifiers, this 22ms delta *per verification* accumulates. The paper hand-waves this: "zkSpeed is ideal for many verifiers" (Section 8), but the verifier is entirely in software—no hardware acceleration path is provided.

4. **Missing Power Normalization:** Table 5 reports 170.88W average power, and they claim "total power density of 0.46 W/mm² which is within that of our CPU [14]." But EPYC 7502 is 180W TDP for 296mm² (0.61 W/mm²). Their density is 34% lower, but they're comparing against a general-purpose processor. Against SZKP's ">220W" they look favorable, but SZKP has *much* higher throughput (28ms vs. 171ms).

5. **Area Accounting for PHYs (Section 7.3):** "We exclude the PHY cost, since the AMD EPYC processor has its own separate die for I/O." But EPYC's I/O die handles far more than HBM PHYs—it includes PCIe, memory controllers for 8 channels of DDR4, etc. The 59.2mm² for 2 HBM3 PHYs (Table 5) is non-trivial at 16% of total area.

6. **Jellyfish Discussion Deferred (Section 8):** They mention Jellyfish (HyperPlonk variant with higher-arity gates) could reduce MLE table sizes "super-proportionally" but leave it to future work. Given that their MLE SRAM dominates area at larger problem sizes (Section 7.3.2), this is a significant unexplored optimization.

---

Q4: What the Authors Didn't Tell You

1. **The SumCheck "Streaming" Claim Hides a 100× Blowup:**
Section 4.1.2 states: "the data itself grows by over 100× between rounds 1 and 2." This is buried in a paragraph about why they can't store intermediate MLEs. Let's do the math: For 2^20 gates with 9 MLEs in Equation 3, round 1 operates on compressed binary data (~2MB total). After incorporating the first random challenge, every entry expands to 255 bits. Even though entries halve, the net effect is 2^19 × 255 bits × 9 MLEs ≈ 147MB—*per SumCheck round*. At 2TB/s, that's 0.07ms of pure bandwidth time *per round*, and there are µ=20 rounds per SumCheck, with multiple SumChecks per protocol. This is why their Pareto analysis shows SumCheck dominating bandwidth-limited designs.

2. **The 90% Sparse Witness Assumption is Critical:**
Section 3.3.1 and 6.2 state witnesses are "90% 1s and 0s." This assumption flows directly into Sparse MSM performance. If real workloads have higher density (e.g., ML inference circuits with non-trivial weights), the Witness MSM speedup would degrade significantly. The Pippenger dense component scales with the 10% dense portion, but the sparse tree-addition phase (45% ones) contributes constant overhead. Table 3's "pessimistic 10%" claim is actually optimistic for some workloads—cryptographic circuits often have denser witnesses.

3. **SHA-3 is a Serialization Barrier:**
Section 3.3.6: "SHA3 effectively acts as an order-enforcing mechanism. This means the protocol steps must be executed in series." Figure 2 shows SHA-3 between *every* major step. While SHA-3 itself is fast (0.00% Area Utilization in Figure 13), it prevents overlapping execution of protocol phases. Any future pipelining across Gate Identity and Wire Identity would require protocol-level changes—this isn't a pure hardware limitation.

4. **The Modular Inversion Batch Size Optimization (Figure 8) is Fragile:**
Section 4.4.4 picks batch size b=64 as "optimal" based on latency imbalance minimization. But the imbalance curve (blue) has a relatively flat region from b=32 to b=128. The area curve (red) drives the decision. If the modular inverse latency (509 cycles for constant-time BEEA) changes with algorithm updates or prime field changes, the optimal b shifts. The paper doesn't discuss sensitivity to the underlying cryptographic assumptions.

5. **MSM Unit Improvement Over SZKP is Incremental:**
Section 4.2 claims two optimizations: reduced memory footprint (18% SRAM savings) and faster bucket aggregation (92% latency reduction, Figure 5). But bucket aggregation is already a small fraction of MSM time at large problem sizes. The 92% improvement on a 10^4-10^5 cycle operation doesn't move the needle when total MSM takes millions of cycles. The real benefit is consolidating Sparse and Dense MSM into one unit—an engineering simplification, not a performance breakthrough.

6. **Table 4's "CPU Prover" Column is Misleading:**
NoCap's CPU baseline (94.2s) uses Spartan's prover, which the authors acknowledge "is slow due to inefficient implementation." SZKP's baseline (51.18s) is Groth16. zkSpeed's baseline (145.5s) is HyperPlonk. These aren't comparable protocols—the CPU times reflect different algorithmic complexities (O(n log n) vs. O(n)), not just software maturity. The "HW Prover" speedups (NoCap: 623×, SZKP: 1800×, zkSpeed: 848×) are computed against *different* baselines, making cross-row comparisons meaningless.

7. **The "Universal Setup" Advantage is Protocol Marketing:**
Section 1 emphasizes that ZCash moved away from circuit-specific trusted setups. But the practical deployment story is more nuanced: HyperPlonk's universal setup still requires a trusted party to generate the structured reference string (SRS). The difference is the SRS is reusable across circuits. For most enterprise deployments, this distinction matters less than proof size and verification time—where HyperPlonk loses to Groth16.