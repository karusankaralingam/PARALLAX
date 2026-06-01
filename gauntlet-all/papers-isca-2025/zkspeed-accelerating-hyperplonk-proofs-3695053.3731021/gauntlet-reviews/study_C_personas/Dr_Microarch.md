## Q1: Whiteboard Explanation

Let me walk you through what zkSpeed actually does at the hardware level.

**The Problem:** HyperPlonk is a Zero-Knowledge Proof protocol that eliminates the expensive NTT (Number Theoretic Transform) found in protocols like Groth16, replacing it with SumCheck. This drops asymptotic complexity from O(n log n) to O(n). But here's the catch—the prover still works on polynomials with 2^17 to 2^24 terms, each element being 255-381 bits wide. The protocol has four sequential phases, each requiring different compute kernels.

**The Architecture (Figure 2B):**

zkSpeed is essentially a **streaming accelerator with 8 specialized units** connected via a shared multi-channel bus:

1. **SumCheck Round Unit** - The workhorse for three polynomial variants (ZeroCheck, PermCheck, OpenCheck). Each PE contains 94 modular multipliers (down from 184 without resource sharing). The key insight here is that they compute all polynomial evaluations in parallel rather than term-by-term as the CPU does (Section 4.1.1).

2. **MLE Update Unit** - Halves MLE tables between SumCheck rounds using Equation 2: `t'[i] ← (t[2i+1] - t[2i])r + t[2i]`

3. **MSM Unit** - Multi-Scalar Multiplication using Pippenger's algorithm. They reuse SZKP's design but optimize two things: (a) only fetch X,Y coordinates since Z=1 initially, (b) faster bucket aggregation (Figure 5 shows 92% latency reduction).

4. **Multifunction Tree Unit (MTU)** - Handles Build MLE, MLE Evaluate, and Product MLE construction. Uses a **hybrid DFS/BFS traversal** instead of pure BFS to reduce SRAM pressure from 128MB to manageable levels.

5. **Construct N&D + FracMLE** - Computes fraction polynomials φ = N/D requiring modular inversion. Uses Montgomery batching with batch size b=64 to amortize the 509-cycle BEEA inversion latency (Figure 7-8).

6. **MLE Combine** - Linear combinations of MLEs for Polynomial Opening.

7. **SHA3** - Generates challenges and maintains transcripts (order-enforcing).

**The Data Flow:** The protocol is sequential across phases (enforced by SHA3), but units overlap computation within phases. The streaming approach writes intermediate MLE tables to HBM between SumCheck rounds because table sizes explode >100× after the first round (Section 4.1.2).

---

## Q2: The Key Insight

**The "Magic Trick":** zkSpeed's fundamental hardware insight is that **SumCheck polynomials in HyperPlonk have repeating multilinear polynomials across terms** (see Equations 3-5), and the CPU baseline computes these redundantly.

Looking at Figure 4, the ZeroCheck polynomial `f_zero = qL*w1*fz1 + qR*w2*fz1 + qM*w1*w2*fz1 - qO*w3*fz1 + qc*fz1` has `fz1` appearing in every term, and `w1`, `w2` appearing multiple times. The CPU iterates term-by-term, recomputing polynomial extensions for each appearance.

zkSpeed's SumCheck PE **computes all per-polynomial evaluations once**, then reuses them across all term products. In Figure 4's dataflow, you see each MLE (a-i) extended to X1=0,1,2,3 independently, then products p_{j,k} computed, then summed. This eliminates redundant computation of extensions.

**The Second Trick:** The Multifunction Tree Unit uses hybrid DFS/BFS traversal (Section 4.3.2). Standard BFS for a 2^23 problem requires 128MB of intermediate storage at a single tree level. By doing DFS at upper levels and BFS at lower levels (Figure 6), they consume intermediate results immediately, achieving >99% PE utilization while fitting in reasonable SRAM.

**The Third Trick:** For modular inversion in FracMLE, they use Montgomery batching with a multiplier tree instead of sequential partial products. This changes complexity from O(b) to O(log₂b) for batches, and they overlap the inversion with multiplication to mask the 509-cycle latency. Figure 8 shows b=64 minimizes both latency imbalance and area.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **Comprehensive Design Space Exploration (Figure 9, Table 2):** They sweep thousands of configurations across 7 bandwidth levels, analyzing Pareto frontiers. This is rigorous—they don't just pick one design point arbitrarily.

2. **Bandwidth Sensitivity Analysis (Figure 11):** Clearly demonstrates that SumCheck is memory-bound (scales with bandwidth) while MSM is compute-bound (scales with PEs). This justifies their streaming architecture choice.

3. **Honest Area/Utilization Breakdown (Figure 13):** They show some units (SHA3, Construct N&D) have <10% utilization. The paper explains this is intentional—these units take little area but are essential to avoid becoming bottlenecks.

4. **Protocol-Level Comparison (Table 4):** Fair comparison against NoCap and SZKP+ including proof size, setup requirements, and verifier time—not just prover speedup.

5. **On-Chip Compression (Section 4.6):** 10-11× storage savings via MLE compression reduces bandwidth by 84% in Polynomial Opening.

### Weaknesses:

1. **Synthetic Benchmarks Only (Section 6.2):** The authors admit "HyperPlonk was evaluated using mock circuit workloads [11], as there is no publicly available compiler to generate real workloads." While they argue performance is workload-agnostic at iso-problem-size, real applications may have different sparsity patterns than their assumed 10% dense/90% sparse distribution.

2. **No RTL Validation Beyond Critical Path:** They use Catapult HLS for Montgomery multipliers and SumCheck PEs, verified timing at 22nm then scaled to 7nm using factors from prior work. No tape-out, no FPGA emulation of the complete chip. The 1GHz clock assumption may be optimistic for the full integrated design.

3. **HBM3 PHY Area Assumptions:** The 29.6mm² per HBM3 PHY (Section 7.1) is taken from prior work, and two PHYs consume 59.2mm² (Table 5)—16% of total area. The actual integration challenges with HBM3 at 7nm aren't addressed.

4. **Power Model Incomplete:** Table 5 shows 170.88W average power, but the methodology section doesn't detail how power traces were constructed. SRAM power (19.6W) seems low for 143.73mm² of memory.

5. **No Real Application End-to-End:** Table 3 shows workloads like "Zcash" and "Rollup of 10 Pvt Tx" but these are still synthetic circuit sizes, not actual compiled circuits from those applications.

---

## Q4: What the Authors Didn't Tell You

**The Hidden Hardware Tax:**

1. **Modular Multiplier Cost:** Each 255-bit modular multiplier is 0.133mm² and each 381-bit multiplier is 0.314mm² (Table 4). The unified SumCheck PE needs 94 modular multipliers (Section 4.1.4)—that's roughly 12.5mm² just in multipliers *per PE*. With 2 SumCheck PEs, that's 25mm². The paper glosses over this by saying they "saved 48.9% area" through resource sharing, but the absolute cost is still substantial.

2. **Batched Inversion Complexity:** Figure 7 shows the FracMLE unit needs *multiple* batched inverse units in round-robin fashion, each containing a modular inverter (509-cycle latency), plus a shared multiplier tree, plus SRAM for partial products. Section 4.4.4 says "12 units for b=64"—that's 12 parallel BEEA cores.

3. **The Streaming Penalty:** Section 4.1.2 admits "our SumCheck and MLE Update units become memory-bound." After round 1, MLE tables expand >100×, forcing streaming to/from HBM. At 2TB/s bandwidth, this is manageable, but the paper doesn't quantify how much time is *lost* to this streaming compared to a hypothetical infinite-on-chip-memory design.

4. **Sequential Phase Constraint:** Section 3.3.6 states "SHA3 effectively acts as an order-enforcing mechanism. This means the protocol steps must be executed in series." The accelerator can't overlap Wire Identity's MSMs with Gate Identity's SumCheck—limiting Amdahl's Law benefits.

5. **Verifier Cost Not Zero:** Table 4 shows HyperPlonk's verifier takes 26ms vs. Groth16's 4.2ms. For blockchain applications with "thousands of verifiers," this 6× slower verification matters. The paper frames this as acceptable for "universal setup" benefits, but doesn't quantify the system-level cost.

6. **Scaling Beyond 2^23:** Figure 14 shows speedups *drop* from 2354× at 2^17 gates to lower values at larger sizes because "on-chip MLE SRAM area begins to dominate, limiting MSM compute area." At 2^24 (Table 4), the prover takes 171.61ms—not dramatically faster than NoCap's 151.3ms despite 10× more area.

7. **The Jellyfish Escape Hatch:** Section 8 mentions Jellyfish (higher-arity gates) could reduce MLE table sizes, potentially improving performance. But zkSpeed can't support it without hardware changes—the SumCheck PEs are specialized for HyperPlonk's specific polynomial structures.