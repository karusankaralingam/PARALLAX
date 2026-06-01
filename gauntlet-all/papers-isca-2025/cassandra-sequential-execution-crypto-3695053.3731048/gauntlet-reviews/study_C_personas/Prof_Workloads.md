Q1: Whiteboard Explanation

Let me break down Cassandra's core mechanism as if we were at a whiteboard.

**The Problem Cassandra Solves:**
Modern processors speculatively execute instructions based on branch predictions. For cryptographic code following constant-time programming principles, this creates a vulnerability: even if your AES implementation is mathematically constant-time, the branch predictor can *mispredict* and transiently execute the wrong path, potentially leaking secrets (see Listing 1, page 79 - a misspeculated loop skip can directly leak the secret `m` before decryption completes).

**The Radical Design Point:**
Cassandra says: "For crypto code, let's *disable* the branch predictor entirely and instead *record* the sequential control flow ahead of time, then *replay* it during execution."

**Why This Works for Crypto (The Two Key Insights):**

1. **Insight 1 (Section 4.1):** Constant-time programs have control flow that is *independent of secrets*. Public parameters (key length, rounds, array sizes) are fixed by standards. So the same control flow trace works across all runs.

2. **Insight 2 (Section 4.2):** Crypto code is *loop-intensive*. A branch that executes 90 million times (see Table 1, sphincs-shake-128s) actually follows repeating patterns that compress down to ~20 entries on average using k-mers compression (borrowed from DNA sequencing).

**The Architecture:**
- **Offline:** Collect branch traces → Aggregate into vanilla traces → Apply k-mers compression (Algorithm 1) → Embed compressed traces + hints in binary
- **Runtime:** New *Branch Trace Unit (BTU)* with three tables (Pattern Table, Trace Cache, Checkpoint Table) determines fetch direction for crypto branches by looking up the pre-computed sequential trace instead of predicting

**The Counter-Intuitive Result:**
By guaranteeing 100% correct fetch redirections (no mispredictions → no squashes → no recovery penalties), Cassandra actually *speeds up* execution by 1.85% versus an unsafe baseline (Figure 7).

---

Q2: The Key Insight

**The Central Insight:** Constant-time cryptographic programs have *deterministic, highly compressible control flow* that can be pre-computed and replayed, transforming the speculative execution problem from "predict and verify" to "lookup and replay."

**Why This Insight Matters:**

Prior Spectre defenses (STT, NDA, DOLMA, SPT) impose overhead because they either:
- Restrict speculative execution dynamically (taint tracking, delayed commits)
- Block instructions that might leak secrets

Cassandra flips the paradigm: instead of *restricting* speculation after the fact, it *eliminates* the need for control-flow speculation entirely for crypto code by providing perfect fetch redirections upfront.

**The Compression Insight is Critical:**
Raw branch traces can be enormous—up to 90 million decisions per static branch (Table 1, sphincs-shake-128s vanilla trace max). The k-mers compression technique achieves average 163,371× compression (Table 1, "All" row), reducing traces to an average of 20 entries per branch. Without this compression, the recording-and-replaying idea would be impractical—you'd face similar stalls to a processor without any branch predictor.

**The Assumption That Enables Everything:**
Constant-time policies *already require* that control flow be secret-independent. Cassandra exploits this existing software guarantee to create a hardware optimization that provides security as a byproduct of performance improvement.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive Crypto Benchmark Coverage:** The evaluation spans BearSSL (7 workloads), OpenSSL (3 workloads), and post-quantum crypto including Kyber and SPHINCS+ variants (Table 1, Figure 7). This covers classical symmetric/asymmetric crypto and emerging PQC—a realistic spread.

2. **Honest "All" Aggregation:** Figure 7 reports geometric mean across all workloads, not cherry-picked subsets. The 1.85% average speedup includes workloads where improvement is minimal (AES_CTR, ChaCha20_ct show ~1.0 normalized time).

3. **Comparison Against Meaningful Baselines:**
   - Unsafe Baseline (realistic attacker target)
   - SPT [15] (prior hardware-only defense showing 12.07% slowdown)
   - ProSpeCT comparison in Section 7.3 with synthetic benchmarks showing 15% slowdown for curve25519 vs. 6.7% *speedup* for Cassandra

4. **Sensitivity Analysis Provided:** Section 7.3's synthetic benchmarks vary crypto fraction (90s/10c to all-crypto), showing Cassandra's benefits scale with crypto intensity. Section 8 Q3 evaluates Cassandra-lite (single-target only), showing 2.7-6.7% degradation—honest about limitations.

5. **Hardware Cost Transparency:** 1.26% area overhead and 2.73% power *reduction* (Figure 9) with explicit BTU sizing (1.74 KiB, Table 3).

**Weaknesses:**

1. **The "Zero-Event" Concern for Misprediction Rates:** The paper claims speedup comes from eliminating misprediction penalties, but *never reports baseline misprediction rates* for crypto workloads. How frequent are these mispredictions? If LTAGE already achieves 99%+ accuracy on crypto branches (which are typically loop-bound and predictable), the 1.85% speedup needs alternative explanation. The paper states "no ROB squashes and penalties occur due for mispredicting crypto branches" (Section 7.2) but doesn't quantify baseline squash rates.

2. **SimPoint Methodology for Long Workloads:** Applications >1B instructions use SimPoint with "average of 6 SimPoints per application and 50M instructions per region" (Section 7.1). For crypto code with repeating loop structures, SimPoint representativeness is questionable—the *entire* execution pattern may be needed to stress BTU eviction/reload behavior. The 250Hz BTU flush evaluation (Section 8 Q4) only reduces improvement from 1.85% to 1.80%, but this doesn't capture realistic multi-tenant interference.

3. **Synthetic Benchmark Limitations:** The SpectreGuard benchmarks (Section 7.3) mix "non-crypto, sandboxed code, and crypto code" but the non-crypto component is uncharacterized. Is it memory-intensive? Compute-bound? The ProSpeCT comparison shows Cassandra's advantage, but the workload construction isn't reproducible from the paper alone.

4. **Missing Real-World Integration Cost:** The paper assumes traces are "embedded in binaries" with "14 bits per static branch" (Section 5.2), but doesn't quantify:
   - Binary size overhead across workloads
   - I-cache pressure from hint instruction decoding
   - Memory bandwidth for trace prefetching on BTU misses

5. **Baseline Configuration Validity:** The gem5 configuration (Table 3) models "Golden-Cove-like" with 512 ROB entries and LTAGE BPU. However, McPAT 1.3 and CACTI 6.5 are dated tools (pre-2015 technology models). Power/area claims should be interpreted cautiously.

6. **Limited Workload Diversity Within Crypto:** Heavy emphasis on BearSSL's constant-time implementations. What about Intel AES-NI accelerated paths? Hardware crypto instructions are increasingly common and may not benefit from Cassandra.

---

Q4: What the Authors Didn't Tell You

**1. The Trace Generation Is Not Free:**
Section 7.5 reveals trace generation takes "388 seconds on average" for branch detection per application, plus "14 seconds average per branch" for raw trace collection and "3 seconds" for k-mers compression. For a program with hundreds of branches, this is *hours* of preprocessing. The paper brushes this off as "one-time" but doesn't address: What happens when OpenSSL pushes a security patch? Every recompilation triggers re-analysis.

**2. The "Input-Dependent Branches" Escape Hatch:**
Section 4.3 and footnote 2 acknowledge that some branches have traces that "change in different runs"—specifically "stream loops in stream ciphers" and "two branches in rejection sampling of Kyber." For these, Cassandra "stalls fetch until the branch resolves." How frequent are these stalls in practice? The paper claims "negligible penalty since they are not frequent and quickly resolve" but provides no quantification.

**3. Scenario 8 is a Gaping Hole:**
The security analysis (Table 2, Section 6.2) explicitly marks Scenario 8 (non-crypto branch → non-crypto memory leak gadget) as "out of scope." This means Cassandra *alone* does not provide complete Spectre protection. The paper expects integration with STT/DOLMA/Levioso for sandboxing, but this combined overhead is never evaluated. What's the performance of Cassandra+DOLMA?

**4. The BTU Size is Suspiciously Small:**
16 entries for PAT/TRC/CPT (Table 3) seems insufficient for programs with many active branches. The paper doesn't report BTU miss rates or eviction frequency. For sphincs-shake-128s with 348 max k-mers trace size (Table 1), how does a 16-entry TRC handle this without constant thrashing?

**5. The Compression "Works" Because Crypto Is Simple:**
Table 1 shows compression rates vary wildly: AES-128 achieves only 43.8× average compression, while EC_c25519 achieves 321,607×. The paper doesn't explain *why* some workloads compress poorly. If your crypto implementation doesn't follow the "loop-intensive, repeating patterns" assumption (Insight 2), Cassandra's benefits may evaporate.

**6. No Discussion of Compiler Optimization Interaction:**
Different optimization levels (-O0 vs -O3) can dramatically change control flow. Does Cassandra require traces per optimization level? Per compiler version? The paper uses "Clang v14.0.4" (footnote 7) but this constraint isn't highlighted in the deployment discussion.

**7. The Power Reduction Claim Needs Scrutiny:**
Figure 9 shows 2.73% power reduction attributed to "crypto branches avoid accessing and updating the BPU." But the BTU itself consumes power. The net reduction suggests BPU access power dominates—is this realistic for a Golden Cove-class core where BPU is heavily optimized?