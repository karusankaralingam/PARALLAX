## Q1: Whiteboard Explanation

Let me explain FAST like I'm sketching it on a whiteboard.

**The Problem:** Fully Homomorphic Encryption (FHE) lets you compute on encrypted data without decrypting it—amazing for privacy, but painfully slow. The killer bottleneck? **Key-switching operations**, which account for ~80% of execution time in bootstrapping (Section 3.1, page 95).

**The Core Tension:** There are two key-switching methods:
1. **Hybrid method**: Uses 36-bit precision, more NTT operations, smaller evaluation keys
2. **KLSS method**: Uses 60-bit precision, fewer NTT operations, but HUGE evaluation keys (up to 295MB at level 35 per Figure 3b)

Here's the twist from Figure 2: **Neither method wins everywhere.** KLSS saves 15.2% ops at levels 25-35, but Hybrid saves 23.5% at levels 5-12. Prior accelerators pick one method and stick with it—leaving performance on the table.

**FAST's Solution (Three Parts):**

1. **Aether-Hemera Framework** (Software): An offline analyzer (Aether) profiles each operation and decides: "Use KLSS here, Hybrid there, enable hoisting with this many rotations." It generates a tiny config file. Online, Hemera manages which evaluation keys to prefetch.

2. **Tunable-Bit Multiplier (TBM)** (Hardware): A clever multiplier design using three 36-bit base multipliers that can either:
   - Process **two 36-bit multiplications** in parallel (for Hybrid)
   - Process **one 60-bit multiplication** (for KLSS)
   
   This is essentially a Booth-like decomposition that reduces the multiplier count by 33% compared to naive approaches (Section 4.2, Figure 6).

3. **Scalable Architecture**: Every computational unit (NTTU, BConvU, KMU) integrates TBMs, so parallelism scales dynamically—512 ops/cycle for 36-bit, 256 ops/cycle for 60-bit.

**The Result:** 1.8× average speedup, 44.4% latency reduction vs. SHARP (Table 5).

---

## Q2: The Key Insight

**The Key Insight:** The optimal key-switching algorithm is **not static**—it depends on the ciphertext's current multiplicative level ℓ and whether hoisting optimizations apply. By dynamically selecting between Hybrid and KLSS methods at runtime, and designing hardware that efficiently supports both precisions (36-bit and 60-bit) without wasting silicon, you can significantly reduce FHE overhead.

**Why This Matters Architecturally:** Prior accelerators like SHARP (36-bit), ARK (64-bit), and CraterLake (28-bit) committed to a single word width. This paper shows that FHE workloads have **phase-dependent precision requirements**—bootstrapping's EvalMod benefits from KLSS, while low-level operations favor Hybrid. The TBM design elegantly sidesteps the traditional "pick one precision" trap by making multipliers reconfigurable at 28% area overhead relative to a fixed 60-bit design (Section 4.2).

**The deeper observation from Figure 2b:** The performance crossover happens because KLSS doesn't reduce NTT complexity at low levels (it actually increases limb groups), while at high levels, KLSS's KeyMult overhead grows faster than its NTT savings. This level-dependent behavior was **not exploited** by any prior accelerator.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Baseline Comparisons (Table 4, Table 5):**
The authors compare against four recent accelerators: BTS (ISCA'22), CraterLake (ISCA'22), ARK (arXiv'22), and SHARP (ISCA'23). This is a legitimate state-of-the-art sweep. The comparison includes multiple configurations of SHARP (base, LM, 8C, LM+8C), which demonstrates robustness—FAST still achieves 1.27× speedup even against SHARP with 8 clusters and large memory (Table 5).

**2. Multi-Application Benchmark Suite:**
They evaluate Bootstrap (the critical operation), ResNet-20 inference, and HELR training with two batch sizes. This covers the major FHE application categories—bootstrapping stress tests, CNN inference, and ML training (Section 6.2).

**3. Ablation Study (Figure 12):**
The paper systematically removes TBM and Aether-Hemera to show individual contributions: Aether-Hemera alone gives 1.3× improvement; adding TBM gives another 1.45×. This decomposition is valuable for understanding where gains come from.

**4. Sensitivity Analysis (Figure 13):**
They vary on-chip memory (180MB-300MB) and cluster count (2-8), showing that FAST's performance doesn't collapse outside the design point. The diminishing returns above 245MB memory is an honest finding—excessive memory doesn't always help due to HBM bandwidth limits.

**5. Utilization Metrics (Figure 11a):**
Component-level utilization (NTTU: 66.47%, HBM: 44.3%) reveals that FAST is both compute-bound and memory-bound simultaneously—a credible characterization of FHE workloads.

### Weaknesses

**1. The "Cherry-Pick" Check — Limited Benchmark Diversity:**
The benchmark suite is **suspiciously narrow**:
- **ResNet-20** on 32×32×3 images is a toy model. Real privacy-preserving ML uses larger models (ResNet-50, BERT). Section 2.2.2 mentions BERT explicitly as a target for FHE, yet no transformer workload appears in evaluation.
- **HELR** (logistic regression) is a decade-old benchmark from 2019 [15]. Where are modern workloads like privacy-preserving LLM inference?
- No **sparse or irregular workloads**. The paper evaluates dense, regular computations. What about graph neural networks or recommender systems with irregular access patterns?

**2. The Baseline Validity — Simulation vs. Silicon:**
All results come from a **cycle-accurate simulator** (Section 6.1), not actual hardware. The paper claims "1 GHz operation" but doesn't report whether timing closure was achieved in synthesis. The RTL was synthesized with TSMC 7nm PDK (predictive, not production), and area/power numbers in Table 3 lack post-layout validation.

Critical question: Did the TBM's "latency-critical variant of the Booth algorithm" (Section 4.2) actually meet timing? The combiner unit accumulating three 72-bit partial products in a cycle is aggressive.

**3. The "Zero-Event" Reality — Hoisting Frequency:**
The paper claims hoisting "has proven to be effective across various applications" (Section 2.2.3), but Figure 3a shows hoisting benefits diminish as the hoisting number increases beyond h2-h4. More critically, **how often does hoisting actually trigger in real workloads?** 

The Aether analysis (Section 4.1.1) makes decisions based on static operation flows, but the paper never reports **what percentage of operations used KLSS vs. Hybrid** in practice, or **average hoisting depth achieved**. Without this, we can't verify that the dynamic selection mechanism activates meaningfully.

**4. Evaluation Key Transfer Overhead Buried:**
Figure 11a shows HBM utilization at 44.3% average—meaning nearly half the time is spent moving evaluation keys! The paper claims this is addressed by Aether's prefetching, but Section 4.1.2 admits prefetching only works when "key transmission time is shorter than the execution time of the preceding ciphertext's key-switching operation." 

What's the **prefetch hit rate**? What happens when consecutive operations need different keys? The 80μs key transfer latency vs. 900ns config file access (Section 7.2) suggests potential pipeline bubbles that aren't fully characterized.

**5. Energy Numbers Lack Context:**
Table 7 reports energy (0.16-6.5J) and EDP, but comparisons to prior work use **assumed** SHARP power (94.7W from [20], footnote 3 on page 103). This is not apples-to-apples—FAST's 160W peak for ResNet-20 is 1.7× higher than SHARP.

**6. Missing Workload: Pure Bootstrapping Throughput:**
Table 6's Tmult,a/s metric (5.4ns for FAST60) is excellent, but this is **amortized** over 2^15 slots. What's the raw bootstrapping latency variance? The 1.38ms Bootstrap time (Table 5) is a single point—no error bars, no min/max across parameter sweeps.

**7. Area Comparison Normalization:**
Table 4 shows FAST at 283.75mm² vs. SHARP at 178.8mm²—that's **1.58× larger**. The "1.13× performance-per-area" claim (Section 7.2) is achieved only because of speedup; if you just need raw throughput and have budget constraints, SHARP8C (250mm², 2.16ms Bootstrap) might be more efficient for your dollar.

---

## Q4: What the Authors Didn't Tell You

**1. The KLSS Method's Dirty Secret:**
Section 2.1.3 and Figure 3b reveal that KLSS evaluation keys can reach **295MB at level 35**. The authors chose 245MB on-chip memory (Section 5.6) specifically to "provide opportunities to support KLSS" at mid-levels, but this means **KLSS cannot be used at the highest levels** where its computational benefits are greatest (per Figure 2a). The paper quietly admits: "KLSS method is not a good choice at the highest level" (Section 5.6). This is a significant limitation buried in the architecture section.

**2. The Bootstrapping Breakdown They Didn't Show:**
Figure 10 shows execution time breakdown with Hybrid/KLSS/Other, but **which stages of bootstrapping** (ModRaise, CoeffToSlot, EvalMod, SlotToCoeff) benefit from which method? Section 7.2 vaguely mentions "hoisting technology in CoeffToSlot and SlotToCoeff" and "KLSS method in EvalMod and SlotToCoeff," but no quantitative breakdown per stage exists. This is critical for understanding whether the approach generalizes to other bootstrapping algorithms.

**3. The 36-bit Precision Assumption:**
The paper inherits SHARP's claim that "36-bit word length is sufficient for FHE applications" (Section 3.2, referencing [20]). But this precision requirement depends on **application-specific error budgets**. For high-precision ML inference or scientific computing on encrypted data, 36-bit may not suffice. The paper never validates that the precision trade-off doesn't impact result accuracy for ResNet-20 or HELR.

**4. Aether's Offline Limitation:**
Aether runs **offline** as "preprocessing on the server side" (Section 4.1.1). This means it cannot adapt to runtime conditions—different input data sizes, varying network latencies, or dynamic workload mixes. The "Aether configuration file (about 1KB)" is generated once and assumed valid forever. What if the application's FHE circuit changes dynamically (e.g., conditional branches in encrypted computation)?

**5. Security Analysis Handwaved:**
Section 4.1.1's "Security" paragraph claims "leakage of key-switching methods does not compromise confidentiality" and cites [9]. But revealing which key-switching method is used, at which level, could constitute a **side-channel**. An adversary observing accelerator behavior (timing, power signatures from different methods) might infer ciphertext structure. This deserves more than a single-sentence dismissal.

**6. The Four-Cluster Sweet Spot:**
Why exactly four clusters? Figure 13b shows 8-cluster configuration gives 1.7× performance but with 12% more pipeline stalls "as the HBM fetches evaluation keys." This suggests the memory subsystem—not compute—becomes the bottleneck. The 4-cluster design was likely chosen to **mask these stalls**, but this limits scalability claims.

**7. No Comparison to GPU Baselines:**
References [13, 18, 19] are GPU-based FHE implementations from the same author group. Why no direct comparison? A modern GPU (A100, H100) with HBM3 might outperform FAST for certain workloads at lower development cost. The paper avoids this comparison entirely, only citing "GPU platforms to accelerate key-switching" in passing (Section 1).

**8. The Evaluation Key Generator Assumption:**
Section 5.7.2 claims the EKG module generates "part b" of evaluation keys from "part a" using PRNG, halving storage. But this requires **deterministic key generation**—any accelerator supporting this optimization needs the same PRNG seed and algorithm. This dependency isn't discussed as a system integration challenge.