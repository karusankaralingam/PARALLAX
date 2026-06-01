## Q1: Whiteboard Explanation

Imagine you're a cryptographer who wrote perfectly constant-time AES code—no secret-dependent branches, no secret-dependent memory accesses. You verified it. Then Spectre happens, and an attacker manipulates your branch predictor to *transiently* skip your encryption rounds, leaking raw plaintext before decryption completes (see Listing 1, page 79).

**The Problem:** Modern CPUs *speculate* on branches. The branch predictor can be poisoned to redirect execution down paths your code would *never* take sequentially. Your "constant-time" guarantees assume sequential execution, but the hardware doesn't provide that.

**Cassandra's Radical Idea:** Don't predict crypto branches at all. Instead:

1. **Pre-compute the control flow trace** of your crypto code. Since constant-time code has no secret-dependent branches, the sequential control flow is *deterministic* for a given algorithm (e.g., AES-128 always does 10 rounds).

2. **Compress these traces** using k-mers counting (borrowed from DNA sequencing). Crypto code is loop-intensive, so traces like "Taken×255, Taken×255, NotTaken×1" compress down to a few pattern entries. Table 1 shows average k-mers trace size of just **19.9 entries** versus vanilla trace sizes of 637K.

3. **Add a Branch Trace Unit (BTU)** to the frontend. When a crypto branch is fetched, look up its pre-recorded outcome in the BTU instead of consulting the BPU. The BTU stores compressed patterns (Pattern Table) and current trace progress (Trace Cache).

**The Counterintuitive Result:** By removing prediction entirely for crypto branches, you get *perfect* fetch redirections—no mispredictions, no squashes. This actually **speeds up** execution by 1.85% over an unsafe baseline (Figure 7), while guaranteeing sequential semantics.

---

## Q2: The Key Insight

The paper's core insight is stated on page 79:

> **"Insight 1:** Sequential control flow of constant-time programs is independent of confidential inputs and is determined by the algorithm and its implementation, which are known before execution."

Combined with:

> **"Insight 2:** Sequential control flow of cryptographic programs is highly regular and loop-intensive, allowing for significant compression of control flow traces."

**Why this matters from a simulation/tooling perspective:**

The authors realized that the *fundamental property* that makes constant-time programming secure (no secret-dependent control flow) is the same property that makes pre-recording traces *feasible*. If your branch outcomes never depended on secrets, they can be computed offline.

The DNA sequencing connection (§4.2.1) is clever: crypto loops produce repetitive branch patterns, just like tandem repeats in DNA. Using k-mers counting to detect these patterns achieves compression rates of **163,371×** on average (Table 1, page 81). This transforms an impractical "record millions of branch outcomes" approach into something that fits in a 1.74 KiB BTU structure (Table 3, page 87).

**The hidden assumption worth scrutinizing:** The traces are generated with specific public parameters. Section 8, Q1 acknowledges that different key sizes (AES-128/192/256) would need separate traces. The authors hand-wave this with "generate separate traces for each mode," but this multiplicative complexity isn't evaluated.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real crypto workloads from production libraries** (§7.1): BearSSL, OpenSSL, Kyber, SPHINCS+ are actual deployed implementations, not toy benchmarks. The authors evaluate 15 distinct crypto primitives.

2. **SimPoint methodology for long-running workloads** (§7.1): For applications exceeding 1B instructions, they use SimPoint to identify representative regions (average 6 SimPoints per app, 50M instructions each). This is proper methodology for trace-based simulation.

3. **Comparison against meaningful baselines** (§7.2): They compare against SPT [15], a hardware-only defense, showing Cassandra achieves 1.85% speedup versus SPT's 12.07% slowdown. The ProSpeCT comparison (§7.3) using SpectreGuard synthetic benchmarks is particularly strong.

4. **Power/area analysis** (§7.4): They use McPAT 1.3 and CACTI 6.5 for power/area estimation. The 1.26% area overhead and 2.73% power reduction claims are supported by standard tools.

### Weaknesses

1. **Simulation abstraction level is underspecified:** The paper uses "gem5 OoO core" in SE mode (Table 3), but doesn't specify whether this is O3 or MinorCPU, what memory model (Classic vs Ruby), or DRAM timings. The claim of a "Golden-Cove-like microarchitecture" with 512 ROB entries is aggressive—but were warmup periods sufficient? The paper never mentions warmup methodology.

2. **Trace generation timing hides critical details** (§7.5): "Branch detection takes 388 seconds on average" and "collecting raw traces takes 14 seconds per branch" are reported, but:
   - What machine? 
   - How many branches per application?
   - For sphincs-shake-128s with millions of dynamic branch outcomes, is this actually practical?

3. **The SpectreGuard synthetic benchmark is not representative** (§7.3): Mixing artificial "sandboxed code" with "crypto code" at fixed ratios (90s/10c, 75s/25c, etc.) doesn't capture real application behavior. No full-system TLS handshake, no web server workload.

4. **No artifact availability mentioned:** The paper never links to a GitHub repository or provides Docker instructions. This is "Paperware" until validated. Given the gem5 modifications and branch analysis tooling, reproducibility is critical.

5. **The k-mers compression evaluation lacks robustness testing:** Table 1 shows worst-case maximum k-mers trace size of 2,312 entries (RSA-2048), but the BTU only has 16 entries (Table 3). What happens when traces exceed BTU capacity during execution? The checkpoint/eviction mechanism (§5.3) is described, but its performance impact isn't isolated.

---

## Q4: What the Authors Didn't Tell You

### 1. The "Trace Validity" Assumption is Fragile

The security argument (§6) assumes traces are *correct*. But who verifies them? An attacker who compromises the trace generation process could insert malicious control flow. The paper punts with "developers can generate traces" (§8, Q2), but there's no integrity mechanism for traces beyond the implicit trust in the binary.

### 2. Multi-Tenancy and Context Switches are Problematic

Section 8, Q4 claims "the OS does not need to store/reload BTU content during timer interrupts" but then admits "it will flush the BTU if there is a context switch between two different crypto applications." They test at 250Hz flush frequency and report only 0.05% performance degradation (1.85% → 1.80%), but this is a single-threaded simulation. In a real cloud environment with frequent preemption, BTU thrashing could be severe.

### 3. The Compiler Assumption is Unstated

The entire approach assumes branch PCs don't change between trace generation and execution. But compiler optimizations, ASLR, and dynamic linking can shift addresses. The hint information (§5.2) uses 12-bit PC offsets, which implies relative addressing, but the paper never explicitly addresses:
- Does ASLR break this?
- How do library updates work?
- What about JIT compilation (irrelevant for crypto, but worth noting)?

### 4. The "Single-Target" Optimization Masks the Real Cost

Section 5.2 notes that 79% of RSA branches are single-target (always jump to the same place). These don't use the BTU at all. This means the impressive compression numbers in Table 1 are somewhat misleading—the *hard* branches (returns, indirect calls) still need full trace storage.

### 5. Gem5 SE Mode Doesn't Model OS Interactions

The evaluation uses Syscall Emulation mode, which doesn't model page faults, TLB misses, or kernel-user transitions. For crypto applications handling large keys (RSA-2048), memory pressure effects are abstracted away. The claim of "real" performance gains should be qualified as "simulation-predicted gains under SE mode assumptions."

### 6. The ProSpeCT Comparison Has Different Compilers

Footnote 7 (page 88) reveals: "we use Clang v14.0.4 for x86 target, while ProSpeCT uses riscv-gnu-toolchain for RISC-V." This isn't an apples-to-apples comparison—different ISAs, different calling conventions, different stack spill behavior. The 15% slowdown attributed to ProSpeCT for curve25519 might be partially due to toolchain differences, not the defense mechanism.