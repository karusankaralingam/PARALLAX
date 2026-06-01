# C³: CXL Coherence Controllers for Heterogeneous Architectures

## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Imagine you're building a data center with an Intel x86 server and an ARM server, and you want them to share a pool of memory over CXL—the hot new interconnect everyone's talking about. Sounds simple, right? CXL 3.0 promises "multi-host coherent memory." Just plug them together and go.

**Here's the problem nobody warned you about:**

The Intel box speaks MESI with x86-TSO memory ordering. The ARM box speaks MOESI (or CHI) with a weak memory model that lets loads and stores fly past each other like cars on a freeway. CXL itself has its *own* MESI-like protocol with quirky handshakes (like `BIConflict`) that neither host protocol understands natively.

So you have three "languages" that need to talk to each other:
- **Host Protocol A** (e.g., Intel's MESI + TSO)
- **Host Protocol B** (e.g., ARM's MOESI + Weak)
- **CXL.mem** (the global arbiter sitting at the memory device)

**The Naïve Approach (and why it fails):**

You might think: "Just translate messages 1:1." But look at Figure 3 in the paper. When MOESI gets a `BISnpData` (someone wants to read your dirty data), the textbook response is to downgrade to the **O-state** (Owner)—you keep a dirty copy, share it, and are responsible for eventual writeback. But CXL doesn't *have* an O-state. It expects a writeback and for you to go to **S-state** (clean sharer). Now you have the MOESI host thinking it owns dirty data while CXL thinks everyone has clean copies. This is a **coherence bomb** waiting to explode.

**C³'s Solution: The "Coherence Controller" as a Protocol Translator**

C³ sits at the boundary between each host's coherence domain and the CXL fabric. Think of it as a specialized embassy that speaks both languages. It has:

1. **A CXL Cache:** This is the host's "representative" in the CXL world. It caches remote data and participates in the CXL protocol.

2. **The C³-Logic:** A finite state machine (FSM) that's a *fusion* of the host's directory controller FSM and the CXL cache controller FSM. Its states are the Cartesian product of both—e.g., (Host=M, CXL=M), (Host=S, CXL=I), etc.

**The two magic rules that make this work (Section III-C):**

- **Rule I (Flow Delegation):** Any request that can't be satisfied locally, or has global side effects, *must* be forwarded across the domain boundary. You can't just ack a write locally if other hosts have copies—you *must* tell CXL to invalidate them first.

- **Rule II (Atomicity):** When you forward a request, you *freeze* the originating domain. No coherence effects happen there until the other domain completes the transaction. This prevents the race condition shown in Figure 4, where a premature ack lets a writer proceed before the invalidation actually completes at remote hosts.

**The "Napkin Diagram" Flow (Figure 6b):**

1. Host 2 wants to write (Store). Its cache sends `GetM` to C³.
2. C³ translates this to CXL's `MemRd,A` (read-for-exclusive).
3. The CXL Directory sees Host 1 has a copy. It sends `BISnpInv` to Host 1's C³.
4. Host 1's C³ translates `BISnpInv` into Host 1's native `Fwd-GetM`, invalidating its local caches and getting the data back.
5. Host 1's C³ does a `CXL WB` (writeback) to the CXL directory.
6. CXL directory sends `CmpM` to Host 2's C³.
7. Host 2's C³ sends `GetMAck` to the requesting cache. *Now* the store can complete.

The key insight: C³ "nests" one protocol's transaction inside the other's, using native flows on each side without modifying either protocol's internal state machines.

---

## Q2: The Key Insight

**The Delta (What's Actually New):**

The *real* contribution here is **not** a new coherence protocol. It's a **systematic methodology**—two concrete design rules (Flow Delegation and Atomicity) derived from the abstract theory of Compound Memory Models [31]—that lets you *automatically generate* a correct-by-construction bridge between *any* host protocol and CXL.

Prior work like HeteroGen [68] fused protocols by merging directories, requiring you to know all participants at design time—a non-starter for CXL's dynamic plug-and-play topology. HieraGen [67] was hierarchical but couldn't handle CXL's `BIConflict` handshake for resolving message races. Compound Memory Models [31] gave the *theory* but not the *implementation* for distributed systems with transient states and message reordering.

C³ bridges this gap. The two rules are simple enough to be a *generator* algorithm (Section V): you feed in SSP (Stable State Protocol) specifications for the host and CXL, and the tool outputs SLICC code for gem5. This generality is the paper's intellectual contribution.

**The "Magic Trick":**

The clever bookkeeping trick is **state compounding**. C³ doesn't just track the host's state or the CXL state—it tracks *pairs* like (Host=M, CXL=M) or (Host=I, CXL=S). This Cartesian product means C³ always knows:
- What the host caches believe.
- What the CXL directory believes.
- Whether a cross-domain transaction is needed.

Rule II (Atomicity) prunes this state space by making many combinations unreachable—you can never have (Host=S, CXL=I) because that would mean the host has data CXL doesn't know about, violating inclusion. This keeps the FSM tractable.

**Why CXL is Hard (The Conflict Handshake):**

Figure 2 is crucial. CXL's `BIConflict/BIConflictAck` handshake exists because CXL runs over PCIe, where messages can be reordered. If a host sends `MemRd,A` (requesting exclusive access) and simultaneously receives `BISnpInv` (invalidation from another request), *neither side knows who won*. The handshake forces agreement. C³ must handle this by entering transient states and stalling until the handshake resolves—this is baked into the generated FSM.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Rigorous Correctness Validation (Section VI-A, Table IV):** This is the strongest part of the evaluation. They use Murφ model checking on the FSMs and run 7 litmus tests (MP, IRIW, 2+2W, etc.) 100,000 times each across 6 protocol/MCM combinations. Crucially, they validate that removing fences on TSO cores still passes (TSO naturally orders stores), while removing fences on ARM cores *does* produce forbidden outcomes. This is the right control experiment—it proves C³ doesn't accidentally strengthen the memory model.

2. **Protocol Generality Demonstrated (Figure 9, Figure 10):** They evaluate MESI-CXL-MESI, MESI-CXL-MOESI, and MESI-CXL-MESIF across 33 benchmarks from PARSEC, SPLASH-4, and Phoenix. Performance differences between protocol combinations are small (avg ~5.5% overhead vs. a baseline *without* CXL's protocol complexities), showing C³ isn't introducing pathological slowdowns for any particular host protocol.

3. **Honest Attribution of CXL Overhead (Section VI-C1, Figure 11):** I appreciate that they diagnose *why* CXL is slower (6 message delays vs. 3 for MESI writes, blocking transient states causing convoy effects on hot cache lines). They don't hide the ball—the slowdown is CXL's fault, not C³'s.

**Weaknesses:**

1. **Simulation-Only, Worst-Case Configuration (Section V):** All data resides in remote CXL memory. No real hardware. Table III shows they calibrated core counts to match MPKI on real Sapphire Rapids hardware, which is a reasonable effort at realism, but gem5 syscall emulation (SE mode) doesn't model OS effects, page table walks, or I/O. They acknowledge hybrid local/CXL configurations would be "more practical" but don't evaluate them.

2. **Workload Selection (Classic Benchmark Syndrome):** PARSEC, SPLASH-4, and Phoenix are 10-20 years old. These are well-behaved parallel workloads with mostly regular access patterns. Where are the workloads with:
   - Heavy lock contention (e.g., migratory sharing patterns)?
   - Irregular pointer-chasing (graph analytics)?
   - High false sharing (malloc-heavy applications)?
   
   The paper's most impacted workloads (histogram, barnes, lu-ncont in Figure 11) show 19-25% miss cycle increases, but these are still relatively tame. I'd want to see what happens with, say, a concurrent hash table or a lock-free queue under heavy contention.

3. **Limited MCM Heterogeneity Testing:** They test ARM-ARM, TSO-TSO, and ARM-TSO combinations. But what about truly exotic combinations? Section IV-D2 discusses RCC (Release Consistency Coherence for GPUs), but the evaluation doesn't include an RCC cluster. The claim of "arbitrary protocol" support isn't fully stress-tested.

4. **No Area/Power/Latency Overhead of C³ Logic:** Section IV-D4 claims "minimal area and power overhead" because the C³-logic is "purely combinational and sequential logic." But there's no synthesis data. How many gates? What's the critical path? Does C³ add cycles to the coherence response path? This matters for integration into real silicon.

5. **Network Model Abstraction (Section V):** They use gem5's Garnet network model, tuned for CXL latencies, but note it's an on-chip network model adapted for off-chip use. They claim this lets them "isolate performance effects stemming from protocol logic and C³ from PCIe transport overheads." Fair enough—but PCIe ordering rules, credit-based flow control, and switch congestion are real things. The 70ns link latency is a single number; real CXL fabrics have variable latency under load.

---

## Q4: What the Authors Didn't Tell You

1. **The Directory Storage Cost is Hidden:** The CXL directory at the memory device must track which hosts have cached copies of each line. For a 256-host system, that's a lot of sharer bits (or a more complex sparse directory). The paper never discusses this scaling issue—they evaluate only 2-cluster systems. Will C³'s compound state tracking blow up when you have 16 hosts with different protocols?

2. **What Happens with Atomic Instructions?** Section IV-D3 mentions memory barriers are handled "indirectly through coherence messages," but there's no discussion of atomic read-modify-write operations (CAS, fetch-and-add). These are the bread and butter of concurrent programming. CXL has specific rules for atomics—how does C³ handle a TSO host doing a `LOCK CMPXCHG` on a cache line owned by an ARM host? The paper is silent.

3. **The "Generator Tool" is the Real Product, but it's Under-Described:** Section V mentions a tool that takes SSP specifications and outputs SLICC code. This is the artifact that would make C³ practically useful, but the paper provides almost no detail on how it works, what its limitations are, or how much manual effort is still required. The artifact appendix says it exists [47], but it's a separate paper (ASPLOS '26).

4. **RCC Support is Hand-Wavy:** Section IV-D2 discusses RCC (self-invalidating protocols for GPUs) and claims C³ handles it because "RCC restores inclusion at each release/acquire via self-invalidation." But Figure 8, which shows RCC-CXL interaction, is the only concrete example, and footnote 5 admits that "CXL invalidations do not update host caches" for RCC, so the CXL cache is "not kept strictly inclusive." This seems like a special case that weakens the generality claim.

5. **CXL 3.0 Multi-Host Coherence Doesn't Exist Yet:** The authors acknowledge (Section I) that "no hardware platform supports multi-host coherence CXL—not even for homogeneous systems." This entire paper is evaluating a feature that has never been built. The simulation parameters (Table III: 400ns round-trip latency) are based on single-host CXL measurements [57] and extrapolation. Real multi-host CXL might have very different characteristics.

6. **The Comparison Baseline (MESI-MESI-MESI) is Idealized:** Their baseline uses a hierarchical MESI protocol with the *same* high-latency link as CXL (70ns, Table III). This makes C³ look good because the overhead is only the protocol translation, not the latency. But a *real* baseline would be a tightly-coupled shared-memory system with lower latency. The 4-26% overhead they report (Figure 10) is relative to an already-slow disaggregated memory baseline.

7. **No Discussion of Livelock/Deadlock in the Compound FSM:** When you fuse two FSMs, you can introduce new cycles that weren't present in either. The Murφ verification presumably checks for this, but the paper doesn't discuss what invariants were verified beyond "no forbidden litmus test outcomes." Were progress properties (livelock freedom) checked?