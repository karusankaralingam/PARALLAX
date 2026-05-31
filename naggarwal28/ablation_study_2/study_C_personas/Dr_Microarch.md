# C³: CXL Coherence Controllers for Heterogeneous Architectures — Architecture Deconstruction

## Q1: Whiteboard Explanation

Let me draw you the actual wiring here, because the block diagrams in this paper hide some important details.

**The Setup Problem:**
You have multiple compute nodes (Intel x86, ARM, maybe GPUs) wanting to share memory over CXL. Each node has its own cache coherence protocol (MESI, MOESI, MESIF, RCC) and its own memory consistency model (TSO for x86, weak for ARM). CXL defines its own coherence protocol (CXL.mem) that looks like MESI but isn't quite MESI.

**The Physical Architecture (Figure 5):**
C³ sits at the boundary between each host's internal coherence domain and the CXL interconnect. It consists of:
1. **CXL Cache**: A cache that holds copies of CXL-mapped data (inclusive of all remote data cached by host private caches)
2. **C³-Logic**: A finite state machine that acts as both a local directory controller (from the host's view) and a global cache controller (from CXL's view)

**The State Machine Construction:**
The "magic trick" is actually quite elegant from a protocol synthesis perspective. C³ maintains a *compound state* — the Cartesian product of local protocol state × CXL protocol state. So if you're bridging MOESI (5 stable states) to CXL-MESI (4 stable states), you theoretically have 20 state combinations, though many are unreachable.

**How Message Translation Works (Figure 6):**
When C³ receives a coherence message from one domain, it conceptually "simulates" the memory operation (load/store) that would trigger an equivalent action in the other domain. For example:
- Host sends `GetS` → C³ issues `MemRd,S` to CXL (as if performing a load to CXL cache)
- CXL directory sends `BISnpInv` → C³ issues `Fwd-GetM` to host caches (as if a store invalidation)

**The Two Rules That Make It Work:**
1. **Flow Delegation (Rule I)**: Any operation that can't be satisfied locally or has globally visible effects must be forwarded across the domain boundary.
2. **Atomicity (Rule II)**: When forwarding a request, C³ must not produce any coherence effects in the origin domain until completion is observed in the target domain. This means stalling — the host is blocked while waiting for CXL to respond.

**The BIConflict Handshake (Figure 2):**
CXL has a unique conflict resolution mechanism because it operates over PCIe where messages can reorder. If a host waiting for `CmpM` receives `BISnpInv`, it can't tell which order the directory processed them. The host sends `BIConflict`, directory responds with `BIConflictAck` that cannot be reordered with the completion message, disambiguating the race.

## Q2: The Key Insight

**The Actual Hardware Trick:**
The clever insight is recognizing that you don't need to merge coherence protocol state machines into a single unified FSM (which would require knowing all participants a priori). Instead, you can keep both protocols' FSMs running in parallel within a single controller, with the compound state space being the product of both.

The key realization is that **message translations between domains are deterministic and can be statically pre-computed** (Section IV-C). This means no runtime table lookups — the translation rules are baked directly into the synthesized FSM as combinational logic. The "translation tables" in Table II are conceptual artifacts of the synthesis process, not actual hardware lookup tables.

**Why This Is Non-Obvious:**
Prior approaches like HeteroGen [68] merge protocol directories into a unified state machine, which breaks CXL's dynamic topology support. HieraGen [67] assumes snoops can always be fully resolved locally before responding, which breaks CXL's conflict resolution requirements.

C³'s insight is that by treating cross-domain requests as **nested transactions** — consuming the origin request, entering a transient state, forwarding to the other domain, and only completing when the nested transaction completes — you can preserve both protocols' semantics without modification.

**The Theoretical Foundation:**
The authors derive their design from compound memory models [31], which define that operations with ordering constraints (o → o') must propagate in the same order to all threads. The two rules are the concrete implementation-level manifestation of this abstract model for CXL's distributed, asynchronous environment.

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Verification Methodology is Solid**: They use both Murφ-based formal verification (extending the HeteroGen approach) and empirical litmus testing in gem5. Table IV shows comprehensive coverage of protocol×MCM combinations. The control tests (removing synchronization to induce forbidden outcomes) demonstrate the tests can actually fail.

2. **Generality is Demonstrated**: Figure 10 shows they tested MESI-CXL-MESI, MESI-CXL-MOESI, and MESI-CXL-MESIF combinations. Figure 9 shows ARM-ARM, TSO-ARM, and TSO-TSO MCM combinations. They run 33 benchmarks across three suites.

3. **Honest Performance Analysis**: Section VI-C1 actually explains *why* CXL is slower, attributing it to CXL's protocol design (6 message delays vs 3 for MESI writes, blocking transient states) rather than C³ overhead. Figure 11's latency breakdown is particularly useful.

**Weaknesses:**

1. **Simulation Fidelity Concerns**: They use gem5's Garnet network model instead of actual CXL/PCIe transport (Section V: "rather than using dedicated PCIe-based CXL simulation models"). They acknowledge Garnet "was originally designed as an on-chip network" — this likely underestimates real CXL latency variability, jitter, and bandwidth constraints.

2. **Scaled-Down System Parameters**: Table III shows 8-30 cores, 4MB LLC, 128KB L1. The footnote admits they "scale the cache sizes and number of cores for each workload to achieve a similar number of misses per kilo-instructions (MPKI) as observed in real hardware." This makes absolute performance numbers meaningless — only relative comparisons matter.

3. **Worst-Case Scenario Admitted but Potentially Misleading**: Section V states they "deliberately evaluate a worst-case scenario with all data in remote CXL memory." This maximizes coherence traffic but may not reflect realistic deployments where hybrid local/remote configurations dominate.

4. **Missing Scalability Analysis**: They only evaluate 2-node configurations. What happens with 4, 8, 16 hosts? CXL 3.0's multi-host coherence is designed for larger fabrics. The directory-based blocking in CXL (Section VI-C1 mentions "convoy effect from blocking transient states") could become a severe bottleneck.

5. **RCC Support is Underspecified**: Section IV-D2 mentions RCC but the evaluation (Table IV, Figures 9-10) never includes an RCC cluster. The claim that C³ handles self-invalidation protocols is not empirically validated.

6. **Litmus Test Repetition Count**: 100,000 iterations per test (Section VI-A) may be insufficient for rare memory ordering bugs. Industry practice often uses millions of iterations.

## Q4: What the Authors Didn't Tell You

**The Hidden Hardware Costs:**

1. **CXL Cache Sizing**: Section IV-D1 states the CXL cache "must be inclusive of all CXL data cached by a host." This is a **significant SRAM overhead**. If your L1s and LLC can hold N lines of remote data, your CXL cache must hold at least N lines. They handwave this by saying "in practice, it can be integrated with the LLC" and pointing to Intel's CHA — but this assumes spare LLC capacity exists.

2. **Compound State Explosion**: The paper mentions the Cartesian product of states (Section IV-B) but never quantifies the actual state machine size. MOESI has 5 stable states but ~20+ transient states in real implementations. CXL-MESI similarly has many transients (Figure 2 shows at least 3 transient flows for just one operation). The synthesized FSM could be much larger than they imply.

3. **The Stall Cost**: Rule II requires stalling the host until CXL completion. Section III-C4 says "effectively stalling" but doesn't quantify this. Figure 11 shows 400+ ns for high-latency misses — during which the host coherence domain is blocked for that cache line. This could cause significant contention on hot lines.

**What the Performance Numbers Actually Mean:**

The 5.5% average overhead (Section I, Figure 10 Mean) is comparing MESI-CXL-MESI against MESI-MESI-MESI — but the baseline MESI-MESI-MESI is **also going through C³** (just with one-to-one forwarding). The real comparison should be against a system without any protocol bridge, which they don't provide.

The 22-43% slowdown for TSO on ARM clusters (Figure 9 right) is substantial but expected — this is the cost of enforcing stronger memory ordering, not a C³ artifact.

**The CXL Protocol's Limitations They Highlight But Underemphasize:**

Section VI-C1 reveals that CXL's protocol is fundamentally more expensive:
- Write invalidation: 6 message delays vs 3 for MESI
- No request pipelining due to blocking transient states
- No peer-to-peer responses (everything goes through directory)

These are CXL specification limitations, not C³ limitations, but they significantly impact the usefulness of CXL for fine-grained sharing workloads.

**The Generator Tool:**

Section V mentions a "generator tool [47]" that takes SSP specifications and outputs SLICC code. Reference [47] is a future ASPLOS'26 paper by partially the same authors. The synthesis methodology — how translation tables are generated, how unreachable states are pruned — is offloaded to that paper. This makes reproduction difficult.

**Missing Implementation Details:**

1. How does C³ handle back-pressure when the CXL cache is full and needs eviction (Figure 7 shows the flow but not timing)?
2. What's the latency of the C³-logic itself? They claim it's "purely combinational and sequential logic" (Section IV-D4) but never provide cycle counts.
3. How are multiple outstanding requests to different cache lines handled? The paper implies single-address blocking but what about the Miss Status Holding Registers (MSHRs)?