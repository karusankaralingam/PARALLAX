## Q1: Whiteboard Explanation

Let me walk you through what Cassandra actually does at the hardware level.

**The Core Problem:**
Constant-time crypto code assumes sequential execution—branch `X` always goes to path `Y` before declassifying secrets. But modern CPUs speculatively execute the *wrong* path (e.g., skip the encryption loop in Listing 1, page 79), potentially leaking secrets before they're properly processed. Existing defenses either stall the pipeline or add complex taint tracking, both killing performance.

**The "Recording-and-Replaying" Trick:**
Cassandra's radical insight is this: *for constant-time crypto, you don't need a branch predictor—you already know the answer*. The control flow is deterministic with respect to secrets (that's what constant-time means!). Public parameters like key length and round counts are fixed by standards.

So the mechanism is:
1. **Offline Analysis (Section 4.2, Figure 1):** Run the crypto binary once, collect the raw branch trace (every branch outcome in order), then compress it using k-mers counting (borrowed from DNA sequencing). A branch trace like `Taken×255, Taken×45, NotTaken×1` gets encoded as a pattern that fits in ~20 entries on average (Table 1, page 81).

2. **Hardware Replay (Section 5.3, Figure 3):** Add a small **Branch Trace Unit (BTU)** to the frontend. When a crypto branch is fetched, instead of querying the BPU, the fetch unit queries the BTU. The BTU looks up the pre-computed next PC from the **Trace Cache (TRC)**, which holds compressed trace elements, and the **Pattern Table (PAT)**, which holds the decompressed pattern elements (Figure 4, page 84).

3. **The Data Structures (Figure 4):**
   - **Pattern Element:** 12-bit target offset + 8-bit repetition count = 20 bits per outcome
   - **Trace Element:** 4-bit pattern index + 4-bit pattern size + 8-bit pattern counter + 16-bit trace counter = 32 bits
   - The BTU has 16 entries each for PAT/TRC/CPT, totaling **1.74 KiB** (Table 3, page 87)

4. **Commit-time Shifting:** When a crypto branch commits (step 3 in Figure 3), the TRC entry shifts—head element is removed, and either (a) a refreshed copy is inserted at the back for short traces, or (b) the next trace segment is prefetched from memory.

**The Key Hardware Bypass:**
Crypto branches *never touch the BPU*. No read, no update. This is enforced by checking the PC against a **Crypto PC Ranges** status register (Section 5.3). For non-crypto branches predicting *into* crypto code, an integrity check stalls fetch until resolution (Scenario 5 in Table 2, page 86).

---

## Q2: The Key Insight

**The "Magic Trick":** The paper exploits two domain-specific properties simultaneously:

1. **Insight 1 (Section 4.1):** Constant-time programs have *input-independent* control flow by definition. The sequential branch trace is a function of the *algorithm*, not the *secret data*. This means one trace rules them all—you can record it once and replay forever.

2. **Insight 2 (Section 4.1):** Crypto code is loop-intensive. Those million-entry raw traces? They're actually just a few patterns repeating. The k-mers compression (Algorithm 1, page 82) detects these patterns and achieves **163,371× average compression** (Table 1).

**Why this is clever:** Branch predictors exist because we *don't know* the future. But for constant-time crypto, we *do* know it—we just hadn't thought to exploit that. By replacing probabilistic prediction with deterministic lookup, you get:
- Zero mispredictions for crypto branches
- No squash penalties
- No BPU power consumption for crypto
- A *speedup* instead of a slowdown (1.85%, Figure 7)

**The structural delta from baseline:** This isn't "branch predictor but better"—it's *branch predictor bypass*. The BTU is architecturally a trace cache, but functionally it's a contract enforcement mechanism. The BPU still exists for non-crypto code; Cassandra just routes around it for marked regions.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Counterintuitive Result is Well-Supported:** The claim of 1.85% *speedup* (not slowdown) is extraordinary for a security mechanism. Figure 7 (page 87) shows this holds across BearSSL, OpenSSL, and PQC workloads. The explanation—eliminating misprediction penalties—is mechanistically sound given the ~99.9%+ "prediction accuracy" achieved by deterministic traces.

2. **Comprehensive Workload Coverage:** Table 1 (page 81) evaluates 15 distinct crypto primitives across three major libraries. The k-mers compression works consistently, with average trace sizes of ~20 entries. This isn't cherry-picked; they show worst cases (RSA max: 2,312 entries).

3. **Head-to-Head with State-of-the-Art:** Figure 8 (page 88) directly compares against ProSpeCT on the SpectreGuard synthetic benchmarks. ProSpeCT shows 15% slowdown on curve25519 (all-crypto); Cassandra shows 6.7% *speedup*. The analysis of *why* (secret stack spills forcing conservative tainting) is insightful.

4. **Power Reduction Makes Physical Sense:** Figure 9 (page 88) shows 2.73% power reduction. Bypassing the large, power-hungry BPU tables (LTAGE has massive storage) in favor of a 1.74 KiB BTU is physically plausible.

### Weaknesses

1. **gem5 SE Mode Limitations:** All results use Syscall Emulation mode (Section 7.1), not Full System. This means no context switches, no OS noise, no kernel crypto paths. The authors briefly mention a 250Hz BTU flush experiment (Section 8, Q4) showing only 0.05% degradation, but this doesn't capture realistic system effects.

2. **SimPoint Methodology for Long Workloads:** Applications with >1B instructions use SimPoints (Section 7.1). For crypto with highly repetitive loops, representative sampling might miss cold-start effects when traces first load into the BTU. BTU miss rates are never explicitly reported.

3. **Trace Generation Overhead Buried:** Section 7.5 admits branch detection takes **388 seconds** per application and raw trace collection takes **14 seconds per branch**. For a library with hundreds of functions, this is hours of offline analysis. The claim "one-time cost" assumes no recompilation ever changes PCs.

4. **The 79% Single-Target Assumption:** Section 5.2 notes that 79% of RSA branches are single-target (always jump to same PC). These don't need BTU entries at all. The "average 20 entries" statistic in Table 1 excludes these branches (footnote, page 81), potentially understating the complexity for the remaining 21%.

5. **Scenario 8 is a Gaping Hole:** Table 2 (page 86) admits non-crypto→non-crypto memory leaks are "out of scope." The paper handwaves that "Cassandra can be integrated with STT/DOLMA/Levioso," but provides zero evaluation of this combined overhead. A realistic deployment *requires* another defense for non-crypto code.

---

## Q4: What the Authors Didn't Tell You

### Hidden Hardware Costs

1. **The Checkpoint Table Lives in Memory:** Section 5.3 says "CPT is stored in data pages which keeps the checkpoints for all branches." This means every BTU eviction triggers a *memory store* to save progress, and every BTU miss on a returning branch triggers a *memory load* to restore it. With 16 BTU entries and crypto functions calling each other, this could generate significant traffic. The paper never quantifies CPT miss/eviction rates.

2. **The "Short-Trace Mark" is a Lie Detector:** Section 5.2 mentions traces <16 entries are marked "short-trace" to avoid prefetches. But what happens when a trace is *exactly* 17 entries? Now every 16 commits, you need to prefetch the next segment. The latency of this prefetch relative to branch commit rate is never characterized.

3. **14-bit Hint Embedding via x86 Prefix Reuse:** Section 5.2 proposes repurposing "previously-ignored prefix bytes" like XRELEASE. This is architecturally fragile—Intel could assign meaning to those bytes in future microarchitectures. The paper cites [85] for precedent but doesn't address forward compatibility.

### The Trace Integrity Problem

The paper assumes traces are trusted. But traces are stored in data pages (Section 5.2). What if an attacker corrupts the trace to redirect crypto branches to attacker-chosen gadgets? Section 6 never mentions trace authentication. A malicious trace could cause Cassandra to *enforce* the wrong path.

### The "Static Over Different Runs" Claim Has Exceptions

Section 4.3 admits:
- Stream ciphers have input-length-dependent loop counts (e.g., ChaCha20 plaintext length)
- Kyber has **rejection sampling branches** that are random across runs

For these, the paper falls back to "stall fetch until branch resolves." But rejection sampling in Kyber can dominate runtime. The performance impact of these stalls is never isolated.

### What SPT Comparison Doesn't Show

Figure 7 compares against SPT [15] showing 12% vs -1.85% overhead. But SPT is a 2021 MICRO paper designed for *all* Spectre attacks on *all* programs. Comparing a general-purpose defense against a crypto-specific one is apples-to-oranges. The fair comparison would be SPT *only protecting crypto code*, which would presumably have lower overhead.

### The Formal Security Analysis is in an "Extended Version"

Section 6.2's last paragraph says "We provide a formalization of Cassandra in an extended version of this paper [26]." The security proof is not in the published paper. You're asked to trust the mechanism without seeing the contract satisfaction proof.