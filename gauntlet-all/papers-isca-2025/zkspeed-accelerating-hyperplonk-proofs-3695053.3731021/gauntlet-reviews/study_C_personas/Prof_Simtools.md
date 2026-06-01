## Q1: Whiteboard Explanation

Let me walk you through what zkSpeed actually *builds* and why it matters.

**The Problem They're Solving:**
Zero-Knowledge Proofs (ZKPs) let you prove a computation is correct without revealing the inputs. HyperPlonk is a specific ZKP protocol with nice properties (small proofs ~5KB, universal setup), but the prover is *painfully slow*—minutes to hours on CPU. The paper builds custom hardware to accelerate this.

**The Core Computational Bottlenecks:**
HyperPlonk's prover has four major phases (Figure 2), each dominated by different kernels:

1. **MSMs (Multi-Scalar Multiplications):** Dot products between huge vectors of scalars (255-bit) and elliptic curve points (381-bit). These are compute-bound—expensive point additions on the BLS12-381 curve.

2. **SumCheck:** An interactive protocol where the prover repeatedly evaluates/updates polynomials stored in "MLE tables" (up to 2^24 entries). This is *bandwidth-bound*—you're streaming through terabytes of data with limited reuse.

3. **Fraction MLE:** Computing modular inverses for every element of a 2^20+ element table. Modular inversion is expensive (509 cycles each).

**The zkSpeed Architecture (Figure 2B):**
They build eight specialized units connected via a shared bus:
- **MSM Unit:** Based on prior work (SZKP), uses Pippenger's algorithm, optimized for both sparse (90% zeros/ones) and dense MSMs
- **SumCheck Unit:** Unified PE handling three polynomial flavors (Equations 3-5), with 94 modular multipliers per PE
- **FracMLE Unit:** Batched inversion (batch size 64) using Montgomery's trick to amortize one expensive inversion across 64 elements
- **Multifunction Tree Unit:** Handles Build MLE, MLE Evaluate, and Product MLE—all binary-tree patterns—using a hybrid DFS/BFS traversal

**The Key Design Insight:**
SumCheck is memory-bound, MSM is compute-bound. They use HBM (2 TB/s) to feed the SumCheck units while carefully rate-matching units to pipeline across protocol phases. On-chip MLE compression (10-11× savings) reduces HBM pressure for the reused input tables.

---

## Q2: The Key Insight

The paper's central insight is **recognizing that HyperPlonk's computational heterogeneity—spanning compute-bound MSMs and bandwidth-bound SumChecks with distinct polynomial structures—requires co-designed, specialized hardware units that can be rate-matched across protocol phases**.

Unlike NTT-based protocols (Groth16) where you have one dominant kernel type, HyperPlonk has *three different SumCheck polynomials* (Equations 3-5) with varying degrees and term structures, *three MSM invocation patterns* with different sparsity, and an expensive fraction computation requiring mass modular inversion.

The authors' solution is a unified SumCheck PE that handles all three polynomial types with 48.9% area savings via resource sharing (Section 4.1.4), combined with the Montgomery batching optimization for inversion (Section 4.4.2-4.4.4) that amortizes one modular inverse across 64 elements using a tree-structured partial product computation.

The architectural manifestation is a streaming design with:
1. Global SRAM storing compressed input MLEs (reused across phases)
2. HBM feeding the bandwidth-hungry SumCheck/MLE Update pipeline
3. A multi-channel shared bus (not crossbar) exploiting the observation that "at most 4 independent bus channels are needed" (Section 5)

This explains their Pareto analysis (Figure 9): below 300mm², the designs are MSM-compute-limited; above that with sufficient bandwidth (≥1TB/s), SumCheck speedups dominate and you get 2× gains over 512 GB/s designs.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Design Space Exploration (Table 2, Figure 9):**
They sweep ~thousands of configurations across 7 bandwidth levels, generating per-bandwidth Pareto curves and a global Pareto front. This is rigorous methodology—not cherry-picking one design point. The highlighted observation that "HBM3-scale bandwidths do yield significant performance gains" (Section 7.1) is backed by data showing 2× speedups at iso-area when moving from 512 GB/s to 2 TB/s.

**2. Honest Utilization Reporting (Figure 13):**
They explicitly show utilization ranging from 70% down to 5% for some modules. The justification—"cores taking up most area, notably MSM at 64.6%, are the most used"—and the argument that low-utilization units are still necessary to avoid Amdahl bottlenecks (e.g., SHA-3 providing 300× speedup) is intellectually honest.

**3. Detailed Breakdown of Area/Runtime Contributions (Figures 10, 12):**
The runtime breakdown (Figure 12) showing CPU vs. zkSpeed at 2^20 gates directly maps to the area breakdown in Figure 10. This lets readers understand *where* the speedups come from and verify the design isn't over-provisioned.

**4. Valid Artifact Linkage:**
Table 1 provides direct links to the source code for each kernel profiled. This is excellent practice for reproducibility.

### Weaknesses

**1. No RTL Validation or Post-Place-and-Route Numbers:**
The paper uses HLS-generated RTL synthesized with Design Compiler at TSMC 22nm, then applies scale factors (3.6× area, 3.3× power, 1.7× delay) to reach 7nm estimates (Section 6.1). This is industry-standard practice but introduces uncertainty. They clock at 1 GHz at 7nm—aggressive but plausible given the 1.05ns critical path (381-bit PADD) at 22nm scaled by 1.7×. However, there's no post-P&R validation, no wire delay modeling, and no mention of whether the scale factors account for the wide (255/381-bit) datapaths that may route poorly.

**2. MSM Simulator is Cycle-Accurate, But Everything Else is Analytical:**
Section 6.1 states "For the MSM, we use a cycle-accurate simulator" but "SumCheck has a fixed, data-oblivious dataflow, which allows us to model its performance analytically." This is reasonable but means the SumCheck numbers don't capture micro-architectural effects like bank conflicts in the highly-banked global SRAM or bus arbitration delays. Given SumCheck dominates runtime at high-performance design points (Figure 10, design D), this could underestimate latency.

**3. Synthetic Benchmarks Only:**
Section 6.2 acknowledges "HyperPlonk was evaluated using mock circuit workloads [11], as there is no publicly available compiler to generate real workloads." The five workloads in Table 3 (Zcash, Auction, etc.) are scaled synthetic circuits with pessimistic 10% dense scalar assumptions. This is defensible but limits confidence that real-world applications with different gate patterns won't behave differently.

**4. Memory System Modeling:**
They assume 2 TB/s HBM bandwidth as a flat number without modeling DRAM refresh, bank conflicts, or the actual 1024-bit HBM3 interface structure. The PHY area is included (29.6mm² per HBM3 PHY), but latency sensitivity to access patterns isn't explored. For a bandwidth-bound workload like SumCheck, this matters.

**5. Comparison Methodology (Table 4):**
The comparison with NoCap at "iso-prover time" and SZKP+ at "iso-area" with "optimistic" scaling is methodologically weak. SZKP+ is given "the benefit of zkSpeed's improved MSMs" which conflates the contributions. The claim that zkSpeed addresses "different application domains" partially sidesteps direct comparison.

---

## Q4: What the Authors Didn't Tell You

**1. The HBM Bandwidth Assumption is Aggressive and Not Validated:**
They assume 2 TB/s sustained bandwidth (Section 7.3). HBM3 is rated at ~800 GB/s per stack, so 2 TB/s requires ~3 stacks or HBM3E. More critically, they don't model the memory controller complexity or latency. Section 5 mentions "a memory controller that has dedicated point-to-point connections to each module" but this controller's area/power isn't itemized in Table 5. At 2 TB/s, this controller is non-trivial.

**2. The 1 GHz Clock is Unvalidated at 7nm:**
They use technology scaling factors from prior work [14, 43] applied to 22nm synthesis results. The 381-bit PADD's 1.05ns critical path at 22nm becomes ~618ps at 7nm (1.05/1.7), giving theoretical headroom for 1.6 GHz. Yet they clock at 1 GHz without explanation. This is conservative, but the scaling factors don't account for increased wire delays in wide datapaths—381-bit elliptic curve points require significant routing resources.

**3. Power Density is Barely Discussed:**
Table 5 shows 170.88W total power at 366mm², giving 0.46 W/mm². They claim this is "within that of our CPU [14]" (Section 7.4), but CPUs have sophisticated power management. Their power trace methodology isn't described—are these peak or average? What duty cycle assumptions?

**4. The SHA-3 Serialization Problem:**
Section 3.3.6 notes SHA-3 "acts as an order-enforcing mechanism" meaning "the protocol steps must be executed in series." This fundamentally limits parallelism across phases. The SHA-3 unit utilization in Figure 13 is ~0%, yet it gates all inter-phase transitions. They don't quantify how much this serialization costs in total runtime.

**5. No Sensitivity to MLE Table Compression Failure:**
Section 4.6 claims 10-11× compression on input MLEs because "Control MLEs q_L, q_R, q_M, q_O are all binary, and q_C, w1, w2, w3 are roughly 90% 1s and 0s." But what if real applications have denser witness polynomials? The 84% bandwidth reduction in Polynomial Opening depends on this sparsity. They don't explore failure modes.

**6. The "Unified SumCheck PE" Hides Complexity:**
Section 4.1.4 claims the unified PE requires "94 modular multipliers" versus 184 without sharing (48.9% savings). But they don't explain *how* resource sharing works across three distinct polynomial structures (Equations 3-5). The datapath switching logic, mux overhead, and timing implications of this sharing aren't characterized.

**7. Jellyfish Support is Handwaved:**
Section 8 mentions "zkSpeed could be extended to support Jellyfish" (a HyperPlonk variant with higher-arity gates) but provides no concrete analysis. Given the SumCheck polynomial structure changes with gate arity, this isn't a trivial extension—their unified PE may need redesign.