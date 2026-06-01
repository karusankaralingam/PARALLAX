## Q1: Whiteboard Explanation

FAST is an FHE accelerator designed to support **multiple key-switching algorithms** (Hybrid and KLSS) with **tunable computational precision** (36-bit and 60-bit).

**The Problem:**
FHE operations on encrypted data are brutally slow—key-switching alone consumes ~80% of total execution time (Section 1). Prior accelerators use a single key-switching method regardless of the ciphertext's multiplicative level (ℓ), leaving performance on the table.

**The Core Observation:**
The authors analyzed computational workload across different ciphertext levels (Figure 2):
- **KLSS wins at high levels (ℓ = 25-35):** 15.2% fewer modular multiplications
- **Hybrid wins at low levels (ℓ = 5-12):** 23.5% fewer modular multiplications
- The crossover depends on ℓ, hoisting number, and on-chip memory capacity

**The FAST Solution (Three Components):**

1. **Aether (Offline):** Pre-analyzes the application's operation flow, builds a Methods Candidate Table (MCT), and selects optimal key-switching method per operation considering: computation cost, evk size, and transfer latency (Section 4.1.1, Figure 5a).

2. **Hemera (Online):** Runtime framework that manages evaluation key transfers from HBM to accelerator based on Aether's configuration file (Section 4.1.2, Figure 5b).

3. **Tunable-Bit Multiplier (TBM):** Hardware that supports both 36-bit (for Hybrid) and 60-bit (for KLSS) multiplication using three 36-bit base multipliers with a Booth-like combination scheme (Section 4.2, Figure 6). This enables:
   - Two parallel 36-bit multiplications, OR
   - One 60-bit multiplication
   - With only 28% area overhead vs. dedicated 60-bit multipliers

**Architecture:** Four clusters with 256 lanes each, containing NTT Units, BConv Units, KeyMult Units, and Automorphism Units—all built on TBM primitives (Section 5, Figure 7).

---

## Q2: The Key Insight

**The key insight is that no single key-switching algorithm is optimal across all ciphertext levels during FHE execution, and the hardware's word length must adapt to support this algorithmic flexibility.**

This insight has two coupled components:

**Algorithmic Dimension:** The authors quantified (Figure 2) that KLSS and Hybrid methods have complementary performance profiles across ℓ. KLSS reduces NTT operations but increases KeyMult complexity; Hybrid does the opposite. The hoisting optimization further shifts this tradeoff (Figure 3a). Prior work treated key-switching as a fixed design choice—FAST treats it as a **runtime decision variable**.

**Hardware Dimension:** KLSS requires 60-bit precision for efficient NTT reduction; Hybrid works with 36-bit (Section 3.2). A 60-bit multiplier costs 2.9× the area and 2.8× the power of a 36-bit one (Figure 4). The TBM design elegantly bridges this gap—it's not just "wider is better" but "adaptable is better."

**Why this matters:** Prior accelerators like SHARP (36-bit), ARK (64-bit), and Craterlake (28-bit) each picked a fixed precision. FAST is the first to recognize that algorithmic flexibility demands hardware flexibility. The 1.8× average speedup comes from exploiting this co-design space.

**The deeper architectural principle:** FHE applications have **non-uniform computational characteristics** across their execution lifecycle (dominated by bootstrapping, which consumes most levels—Section 3.1). A static hardware configuration wastes either area (over-provisioned precision) or performance (wrong algorithm). FAST dynamically matches algorithm to level to hardware mode.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Simulation Infrastructure (Section 6.1):**
The authors developed a cycle-accurate simulator, validated individual components through functional simulation, and translated applications into cryptographically structured operation traces. They synthesized RTL using TSMC 7nm PDK and used FinCACTI for SRAM/wiring estimation. This is rigorous methodology.

**2. Apples-to-Apples Comparisons (Tables 4-5):**
They compare against multiple baselines (BTS, Craterlake, ARK, SHARP) with consistent parameters. They even evaluate enhanced SHARP configurations (SHARP_LM, SHARP_8C) that weren't in the original SHARP paper—footnote 2 acknowledges they modeled these themselves.

**3. Diverse Benchmarks (Section 6.2):**
Bootstrap, ResNet-20, HELR256, HELR1024 cover bootstrapping-dominated and application-level workloads. The Tmult,a/s metric (Table 6) enables fair cross-paper comparison.

**4. Sensitivity Analysis (Section 7.7, Figure 13):**
They explore memory capacity and cluster count scaling, demonstrating that performance doesn't always improve with more memory (it's bandwidth-limited). This shows intellectual honesty.

**5. Detailed Breakdown Analysis (Figures 10-12):**
The ablation study in Figure 12 isolates contributions: Aether-Hemera gives 1.3×, TBM adds another 1.45× on top. Section 7.4's utilization analysis (Figure 11a) shows 66.47% NTTU utilization—reasonable for a compute-bound design.

### Weaknesses

**1. Simulation-Only Validation—No Silicon, No RTL Timing Closure:**
All results come from their cycle-accurate simulator. Section 6.1 states they synthesized to "estimate area and power," but there's no mention of timing closure at 1 GHz, no place-and-route, no post-layout verification. The 1 GHz target at 7nm is plausible but unverified. **This is paperware.**

**2. Memory System Modeling is Underspecified:**
Table 4 shows 1 TB/s HBM bandwidth across all designs, but DRAM refresh, bank conflicts, and scheduling are never mentioned. Section 7.4 admits "44.3% of time is consumed by data transfer from off-chip memory"—but was this modeled with realistic HBM contention? They assume perfect prefetching overlap (Section 4.1.2 claims Hemera's latency is "significantly lower than HBM transfer latency"), but the actual overlap efficiency is never quantified.

**3. Power Numbers Lack Validation Path:**
Table 7 reports 120-160W average power, Table 3 shows 337.5W peak. These come from synthesis estimates, not thermal simulation or power integrity analysis. The 29.4W for 281MB of register files (Section 5.6) seems optimistic for SRAM at scale.

**4. The Aether Configuration Overhead is Hand-Waved:**
Section 4.1.1 says the Aether configuration file is "about 1KB" and Hemera reads it in "less than 900ns" (Section 7.2). But Aether runs offline on the server—what's its runtime? For dynamic workloads, recomputation cost matters. They claim "security is preserved" but don't analyze information leakage from method selection patterns.

**5. Limited Process Node Validation:**
They cite prior work's parameters (SHARP, ARK) but those were designed for potentially different assumptions. The 36-bit vs 60-bit area scaling in Figure 4 is critical to their argument, but the multiplier microarchitecture isn't detailed enough to verify the 2.9× claim independently.

**6. Bootstrapping Dominates Everything:**
Section 7.2 notes bootstrapping consumes up to 94.5% of HELR256's execution time. This means the non-bootstrap optimizations barely move the needle. The 1.8× speedup is heavily bootstrapping-dependent—what happens with different Leff values?

---

## Q4: What the Authors Didn't Tell You

**1. The On-Chip Memory Increase is Substantial and Unexplained:**
FAST requires 281MB on-chip SRAM (Table 4) compared to SHARP's 198MB—a 42% increase. Section 5.6 justifies this vaguely ("to support evk incremental of the KLSS method and the hoisting technology") but the exact capacity calculation is missing. Figure 3(b) shows KLSS evk can reach 295MB at L=35, yet they claim 245MB (Section 5.6) is sufficient. **How does 245MB support a 295MB key?** The answer must involve selective key loading, but the scheduling algorithm isn't formalized.

**2. The TBM's 33% Multiplier Reduction Claim Needs Scrutiny:**
Section 4.2 claims the Booth-like decomposition "reduces multiplication requirement by 33% compared to conventional implementations." But conventional Karatsuba for 60-bit using 36-bit pieces needs 3 multiplications—which is exactly what TBM uses (M-A, M-B, M-C in Figure 6). The 33% reduction is against the naïve 4-multiplication approach, not the state-of-the-art. **They're comparing against a strawman.**

**3. Hemera's History Recorder is a Black Box:**
Section 4.1.2 mentions a "history recorder" that "tracks key-switching patterns across subsequent computational levels, enabling proactive adaptation." This sounds like a predictor, but: What's the prediction accuracy? What happens on misprediction? Is there a fallback? This is architecturally significant but completely unspecified.

**4. The Hybrid→KLSS Crossover Level is Application-Dependent:**
Figure 2 shows crossover around ℓ=12-13 and ℓ=24-25, but these are for specific parameter sets (Set-I and Set-II, Table 2). Different applications with different parameter choices will have different crossover points. **Aether's analysis assumes parameter stability**, but parameter selection itself is an active research area [27].

**5. No Discussion of Correctness/Precision Validation:**
FHE computation accumulates noise. Section 5.7.1 mentions Double-Prime Scaling for "maintaining ciphertext precision," but there's no validation that FAST's outputs match reference implementations. Switching between 36-bit and 60-bit computation could introduce numerical differences. **Where's the functional correctness study?**

**6. The Comparison Against SHARP_8C+LM is Suspicious:**
Section 7.2 compares against SHARP_LM+8C (290mm²) and claims 1.27× speedup with 1.3× performance-per-area improvement. But FAST is 283.75mm² (Table 3). That's nearly identical area. The "improvement" comes entirely from the algorithm, not area efficiency. **The performance-per-area metric flatters FAST because area is similar.**

**7. Artifact Availability is Unmentioned:**
The paper doesn't link to any GitHub repository, RTL source, or simulation framework. For a paper claiming "first accelerator to support hoisting technology and KLSS," reproducibility is critical. This appears to be closed-source research.