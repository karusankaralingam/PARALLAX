## Q1: Whiteboard Explanation

Imagine you're writing cryptographic code that must be "constant-time"—meaning an attacker watching your program's timing, cache accesses, or control flow can't learn anything about your secret keys. The standard approach is to make sure branches and memory accesses never depend on secrets. This works great... until Spectre.

**The Spectre Problem for Crypto:**
Modern CPUs predict which way branches will go and speculatively execute down the predicted path. If the prediction is wrong, the CPU rolls back—but side effects in caches, timing, etc., remain. An attacker can manipulate the branch predictor to force your crypto code down the *wrong* path, transiently executing code that leaks secrets before the rollback happens.

Consider Listing 1 (page 79): A decryption loop runs `num_rounds` times. An attacker could manipulate the branch predictor to *skip* the loop entirely, causing the secret plaintext `m` to reach `leak()` without decryption. The program is constant-time under sequential semantics, but broken under speculative semantics.

**Cassandra's Radical Idea:**
Instead of trying to "fix" speculation or restrict when secrets can be touched, Cassandra asks: *What if we knew exactly which branches to take, in advance, and never needed to predict?*

The key insight is that constant-time code, by definition, has control flow that **doesn't depend on secrets**. The loop count, the call targets, the return addresses—they're all determined by public parameters (key length, algorithm specification, etc.). So the *entire control flow trace* of a constant-time crypto routine is **the same every time you run it** (for a given algorithm configuration).

**The Mechanism (Figure 3, page 83):**
1. **Offline Analysis (Section 4):** Before deployment, run the crypto code once, record every branch direction/target, and compress this "branch trace" using patterns (inspired by DNA k-mer counting). The compression is massive: average 20 entries per branch vs. millions of raw decisions (Table 1).

2. **Runtime (Section 5):** Add a small hardware unit called the **Branch Trace Unit (BTU)** to the CPU frontend. When fetching a crypto branch:
   - Skip the Branch Prediction Unit (BPU) entirely.
   - Look up the BTU, which tells you *exactly* which way to go next based on the pre-recorded trace.
   - No mispredictions. No speculative execution of wrong paths. No Spectre.

3. **Hint Information:** The binary embeds hints (14 bits per branch, Section 5.2) telling the hardware whether a branch is single-target (trivial), short-trace (fits in one BTU entry), or where to find its trace data.

**Why This Might Be Counterintuitive:**
You'd expect "no speculation = slow." But Cassandra claims a **1.85% speedup**. The trick: branch predictors aren't perfect. On complex crypto loops, even good BPUs mispredicts sometimes, causing expensive pipeline flushes. Cassandra's traces are *always correct*, so zero misprediction overhead for crypto code.

---

## Q2: The Key Insight

**The Real Delta:**
The genuine novelty is the **domain-specific exploitation of constant-time program properties to enable recording-and-replaying** as a practical Spectre defense. Prior work either:
- Restricted speculation dynamically (expensive taint tracking like STT, SPT, DOLMA), or
- Required manual secret annotation (ProSpeCT), or
- Inserted fences/barriers everywhere (software mitigations with 20%+ overhead).

Cassandra recognizes that constant-time programs are *self-documenting* with respect to control flow: the trace is public by construction. This is stated explicitly on page 80: "Sequential control flow of constant-time programs is independent of confidential inputs and is determined by the algorithm and its implementation."

**The Compression Insight (Insight 2):**
The second key insight is that crypto traces are **highly repetitive**—they're dominated by loops. The authors borrow k-mer counting from DNA sequencing (Algorithm 1, page 82) to find repeating patterns. This reduces traces from millions of entries to an average of ~20 entries (Table 1). Without this compression, the recording-and-replaying idea would be impractical due to storage and communication costs.

**The Cost-Hiding Trick:**
The performance claim isn't hiding latency through speculation—it's *eliminating unnecessary work*:
1. **No BPU access/update** for crypto branches (reducing power, avoiding aliasing/pollution).
2. **Zero mispredictions** means zero squash cycles for crypto code.
3. The BTU is a small (1.74 KiB), direct-mapped, low-latency structure (Table 3).

The "trick" is that branch predictors are imperfect even for regular code, and crypto loops can be particularly adversarial for pattern history. By replacing probabilistic prediction with deterministic replay, you trade a complex, power-hungry BPU lookup for a simple table lookup—and win on both correctness and efficiency.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Benchmark Coverage:**
The evaluation (Section 7.2, Figure 7) covers real-world crypto libraries: BearSSL, OpenSSL, and post-quantum primitives (Kyber, SPHINCS+). This isn't cherry-picked microbenchmarks—these are the actual implementations developers use.

**2. Apples-to-Apples Comparison:**
They compare against SPT [15], a prior hardware defense for constant-time code (Figure 7). Cassandra achieves 1.85% speedup vs. SPT's 12.07% slowdown. The variance matters: SPT hits 59.8% slowdown on OpenSSL chacha20, while Cassandra speeds it up.

**3. Mixed Workload Analysis (Section 7.3, Figure 8):**
The SpectreGuard synthetic benchmarks (sandboxed + crypto) show Cassandra scales gracefully. As crypto fraction increases, Cassandra's benefit grows (0.6% → 6.7% speedup for curve25519). ProSpeCT degrades significantly (2.5% → 15% slowdown) because it must conservatively mark the stack as secret.

**4. Realistic Microarchitecture (Table 3):**
They model a Golden-Cove-like OoO core in gem5, not a toy in-order pipeline. The BTU adds only 1.26% area (Figure 9, page 88).

**5. Power Reduction:**
Figure 9 shows 2.73% power reduction by avoiding BPU accesses—a genuine win, not just "acceptable overhead."

### Weaknesses

**1. Simulation-Only Evaluation:**
All results come from gem5 in Syscall Emulation mode (Section 7.1). There's no FPGA prototype, no silicon, no real OS interaction. Context switch costs are *estimated* (Section 8, Q4: flushing BTU at 250Hz reduces benefit from 1.85% to 1.80%), not measured.

**2. Threat Model Admits Large Gaps (Section 3, Table 2):**
The paper explicitly states Cassandra doesn't protect non-crypto code (Scenario 8, page 86). A Spectre gadget in the calling code *can still leak arbitrary memory*, including crypto secrets loaded into registers. The authors punt to "integrate with DOLMA/STT for comprehensive protection," but don't evaluate this integration cost.

**3. What Breaks?**
- **Input-dependent branches:** Section 4.3 (lines 4-5 of Algorithm 2) detects branches whose traces change with input and "stalls fetch until the branch resolves." They claim "negligible penalty since they are not frequent," but don't quantify this for stream loops in ChaCha20 or rejection sampling in Kyber (footnote 2).
- **JIT/Interpreters:** Not discussed. If crypto is JIT-compiled, PCs change, traces are invalid.
- **Multithreading:** No mention of how BTU handles concurrent threads or hyperthreading. Is the BTU per-core? Per-hardware-thread?

**4. Upfront Analysis Cost (Section 7.5):**
Branch detection takes 388 seconds *per application*; trace collection takes 14 seconds *per branch*. For a large library with hundreds of static branches, this is hours of offline analysis. The paper treats this as acceptable ("one-time"), but doesn't discuss CI/CD integration or recompilation workflows.

**5. Compression Sensitivity:**
The k-mers compression works beautifully for evaluated benchmarks, but what about edge cases? RSA's maximum trace is still 2,312 entries (Table 1)—that's 145 BTU entries (at 16 entries/BTU entry), far exceeding the 16-entry BTU. The paper doesn't discuss BTU miss rates or eviction penalties for such outliers.

**6. No Formal Verification:**
The "formal security analysis" is relegated to an arXiv extended version [26]. The main paper only provides an informal security argument (Table 2).

---

## Q4: What the Authors Didn't Tell You

**1. The "Single-Target" Shortcut Does Most of the Work:**
Section 5.2 reveals that 79% of static branches in RSA are "single-target" (always jump to the same place). For these, Cassandra embeds the target in hint bits—no BTU lookup needed. The impressive compression numbers in Table 1 exclude single-target branches. The paper doesn't break down how much of the performance/storage benefit comes from this simple optimization vs. the sophisticated k-mers compression.

**2. Cassandra-lite Reveals the True Cost:**
Section 8, Q3 admits that a BTU-less variant ("Cassandra-lite") that handles only single-target branches and stalls on multi-target branches incurs 2.7–6.7% slowdown vs. full Cassandra, with 22% slowdown for OpenSSL sha256 and 8% for kyber512. This suggests the BTU is load-bearing for complex applications—but the paper doesn't evaluate BTU sizing sensitivity or what happens when traces exceed BTU capacity.

**3. The 12-bit Target Offset Limits Jump Distance:**
Pattern elements use a 12-bit signed offset (Figure 4a). This limits relative jumps to ±2KB from the branch PC. Crypto code with large functions or libraries with spread-out code sections might exceed this. The paper doesn't discuss fallback mechanisms or how common this limitation is.

**4. Security Relies on Software Being Actually Constant-Time:**
If the crypto code has a *bug* and is secretly not constant-time, Cassandra will faithfully replay the recorded trace—potentially diverging from actual execution if different inputs take different paths. The paper assumes "programs that adhere to a constant-time policy" (Section 3, page 80), but doesn't discuss verification or what happens on trace mismatch.

**5. Non-Crypto → Crypto Transition Creates a Speculation Window:**
Scenario 5 (Table 2, page 86) reveals that when non-crypto code branches *into* crypto code, Cassandra must stall until the non-crypto branch resolves. This "integrity check" prevents speculative entry into crypto, but the cost isn't evaluated. If crypto functions are called frequently from non-crypto code (e.g., an encryption API called in a loop), this could add significant overhead.

**6. Trace Tampering Isn't Discussed:**
Traces are stored in data pages (Section 5.2–5.3). What if an attacker corrupts the trace data? The paper doesn't discuss integrity protection for traces. A malicious trace could direct the CPU down arbitrary paths—potentially a worse vulnerability than Spectre itself.

**7. Checkpoint Table Lives in Memory:**
Section 5.3 states CPT checkpoints are "stored in data pages" to handle evictions and interrupts. This means every BTU eviction or context switch requires memory writes. The paper evaluates only flush frequency (250Hz), not eviction rates under BTU pressure.

**8. The Threat Model Excludes Meltdown-type Attacks:**
Section 3 explicitly scopes out Meltdown, L1TF/Foreshadow, MDS (ZombieLoad, RIDL, Fallout). These are "efficiently mitigated in recent CPUs via microcode updates." But this means Cassandra is only useful on CPUs with those mitigations—which already have significant Spectre mitigations built in. The incremental value vs. existing hardware defenses (IBRS, STIBP, SSBD) isn't discussed.