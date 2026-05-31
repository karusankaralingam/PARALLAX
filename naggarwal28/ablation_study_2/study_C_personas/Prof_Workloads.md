# Paper Analysis: C³: CXL Coherence Controllers for Heterogeneous Architectures

## Q1: Whiteboard Explanation

Let me draw this out for you conceptually.

**The Problem:** Imagine you have an Intel x86 server and an ARM server, and you want them to share memory through CXL (Compute Express Link). Sounds simple, right? Just plug them in and go. Wrong.

Here's the issue: Each architecture speaks a different "language" when it comes to cache coherence. x86 uses MESI with TSO (Total Store Order) memory consistency. ARM uses MOESI with a relaxed memory model. CXL itself uses yet another MESI-like protocol but with different transaction flows (see Figure 2 - notice the BIConflict handshaking that doesn't exist in textbook MESI).

When a store operation from one host races with an invalidation from the CXL directory, you get three different possible scenarios (Figure 2, left/middle/right). The hosts literally can't agree on what order things happened without explicit handshaking.

**The Solution - C³:** Think of C³ as a universal translator sitting at the border between each host's coherence domain and CXL. It follows two simple rules:

1. **Flow Delegation (Rule I):** If something can't be handled locally or affects global state, forward it to the other domain. Conceptually, C³ "simulates" a load/store that would trigger the equivalent coherence flow in the target protocol (see Figure 6a/6b).

2. **Atomicity (Rule II):** When forwarding a request, don't produce any effects in the origin domain until you get confirmation from the target domain. This prevents race conditions like Figure 4 where an invalidation acknowledgment could race with a write completion.

The compound state machine tracks both local and global protocol states simultaneously (e.g., (M,M) means Modified in both host and CXL views). This allows C³ to decide what cross-domain actions are needed.

**The Result:** Programs compiled for each architecture continue working correctly with their native memory ordering guarantees, even when sharing CXL memory with heterogeneous hosts.

## Q2: The Key Insight

The fundamental insight is this: **You don't need to merge heterogeneous coherence protocols into a single unified protocol; instead, you can bridge them by delegating operations across domain boundaries while enforcing atomicity at those boundaries.**

This is profound because previous approaches like HeteroGen [68] required fusing directory controllers into a unified state machine - which breaks CXL's dynamic topology where devices can be added/removed at runtime. HieraGen [67] assumed snoops must be fully resolved locally before global response - which conflicts with CXL's conflict resolution transactions.

C³'s approach hinges on the observation that **compound memory models** [31] provide theoretical guarantees but are too abstract for implementation. The paper bridges this gap by translating abstract propagation axioms into two concrete implementation rules. The key realization (Section III-C) is that if any two memory operations (o, o') have an ordering constraint (o → o'), then o must propagate before o' to all affected threads, and o' must be stalled until o completes.

The second critical insight is that **coherence flow delegation can be made protocol-agnostic** by conceptually mapping cross-domain requests to the load/store operations that would trigger equivalent flows (Figure 6). Since loads and stores are universal primitives, this creates a generic translation mechanism that doesn't require modifying existing coherence state machines.

**Why it matters:** This enables CXL 3.0 multi-host coherence to actually work with heterogeneous architectures without requiring vendors to redesign their coherence implementations or users to recompile their software.

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Correctness Validation is Thorough:**
- Murφ-based formal verification of FSMs using herd7-generated litmus tests (Section VI-A)
- 7 litmus tests (MP, IRIW, 2_2W, R, S, SB, LB) run 100,000 times each in gem5 (Table IV)
- Crucially, they verified that removing fences on TSO cores still passes tests (TSO naturally enforces store-store order), while removing fences on ARM cores produces forbidden outcomes. This validates that C³ propagates but doesn't artificially strengthen memory guarantees.
- The "control" experiments that intentionally removed synchronization to verify tests can detect forbidden outcomes is methodologically sound.

**2. Good Protocol Coverage:**
- Table IV shows 6 different combinations: MESI-CXL-MESI and MESI-CXL-MOESI, each with Arm-Arm, TSO-Arm, TSO-TSO configurations
- Figure 10 tests 4 protocol combinations: MESI-MESI-MESI baseline plus three heterogeneous variants
- This covers the most likely real-world deployment scenarios

**3. Reasonable Benchmark Diversity:**
- 33 applications from three suites (PARSEC, Splash-4, Phoenix)
- These are standard parallel benchmark suites for multi-core evaluation

### Weaknesses

**1. The Baseline is Artificially Weak (The "Strawman" Problem):**

The MESI-MESI-MESI baseline uses the **same high-latency CXL link** as the heterogeneous configurations (Table III: 70ns link latency). This is curious because a fair "native" baseline would be a unified on-chip MESI with lower latency. The 5.5% average overhead claim (Section I, Section VI-C) is relative to an already-CXL-penalized system.

What's the overhead compared to a system without CXL at all? They explicitly state they "deliberately evaluate a worst-case scenario with all data in remote CXL memory" (Section V) but then claim this shows C³ has "minimal performance overhead."

**2. Missing Real-World Workloads:**

The benchmarks are all traditional HPC/parallel computing workloads. Where are:
- Datacenter workloads (key-value stores, databases)?
- ML inference workloads (the motivation mentions "diverse compute platforms" including TPUs)?
- Producer-consumer patterns between heterogeneous devices?

The paper's motivation (Section I) discusses GPU/FPGA/TPU integration, but the evaluation only covers CPU-CPU scenarios.

**3. The Cache Miss Analysis (Figure 11) Cherry-Picks:**

Figure 11 shows only 4 workloads: 3 "most impacted" (histogram, barnes, lu-ncont with 19-25% more miss cycles) and 1 "least impacted" (vips with 2.2%). What about the distribution of the other 29 workloads? The bimodal selection obscures whether these extremes are representative.

**4. Scalability is Completely Unaddressed:**

The evaluation uses only 2 nodes (Table III: two-node heterogeneous system). CXL 3.0 is designed for much larger fabrics. How does C³'s overhead scale with:
- Number of hosts (4, 8, 16)?
- Degree of sharing/contention?
- Size of sharer lists in the CXL directory?

Section VI-C identifies that CXL slowdowns come from "convoy effect from blocking transient states" and "cache lines that are hot-spots." These effects would worsen significantly at scale.

**5. Missing Workload Characterization:**

The paper doesn't characterize:
- Cross-cluster vs. intra-cluster coherence traffic ratios
- Sharing patterns (read-sharing vs. write-sharing vs. false sharing)
- Working set sizes relative to CXL cache capacity

Without this, we can't extrapolate to other workloads or configurations.

**6. The MPKI Matching Methodology is Suspicious:**

Section V states they "scale the cache sizes and number of cores for each workload to achieve a similar number of misses per kilo-instructions (MPKI) as observed in real hardware." This means:
- Different workloads run with different core counts (8-30)
- Cache sizes may differ per workload

This makes cross-workload comparisons questionable. Are barnes and vips running with the same configuration? The methodology obscures this.

**7. No Comparison to Alternative Approaches:**

Where's the comparison to:
- HeteroGen [68] (even if it doesn't support dynamic topology, what's the performance difference when topology is static)?
- Memglue [21] (the alternative approach of creating a unified MCM)?
- Simple serialization approaches (e.g., always flush caches at domain boundaries)?

The paper positions C³ as superior architecturally but provides no performance comparison to alternatives.

## Q4: What the Authors Didn't Tell You

**1. The CXL Cache Creates a Hidden Scaling Problem:**

Section IV-D4 mentions "the CXL cache must be inclusive of all CXL data cached by a host." This inclusivity requirement means the CXL cache must grow proportionally with the total host cache capacity. For a system with many hosts, each with large LLCs, this becomes a significant area overhead. The paper handwaves this by saying it "can be integrated with the LLC" but doesn't quantify the tag storage overhead or the conflict rate when multiple addresses map to the same LLC set.

**2. The 400ns Latency Assumption May Be Outdated:**

Table III uses 400ns CXL memory access latency based on [57] (TPP paper from 2023). But the paper also references more recent measurements showing 50-100ns for CXL access (Section I). The gap between 100ns and 400ns is significant - the evaluation's conclusions about relative overhead may change substantially with lower-latency CXL implementations.

**3. RCC (Release Consistency) Support is Incomplete:**

Section IV-D2 describes how C³ handles RCC protocols (GPUs), but this is never evaluated. The litmus tests and performance benchmarks use only MESI/MOESI hosts. The RCC discussion in Section IV-D2 mentions C³ "does not keep the CXL cache strictly inclusive" for RCC - this is a significant deviation from the SWMR protocols that could introduce correctness issues not covered by the formal verification.

**4. The Generator Tool is the Real Contribution:**

The paper buries the fact that their "generator tool" [47] (reference to a companion paper) does most of the heavy lifting. Section V mentions it "parses SSP specifications... generates concurrent FSMs... generates translation tables... merges the two FSMs into a single compound FSM." The C³ design rules are useful, but the automation that makes them practical is in a separate paper.

**5. CXL's Protocol Overhead is Inherent, Not a C³ Problem:**

Section VI-C1's root cause analysis reveals that CXL requires "6 remote message delays when the owner is dirty (4 when clean)" vs. MESI's 3 delays, plus "2 blocking transient states at the directory, preventing pipelining." This is a CXL protocol design issue, not something C³ introduces. The paper is honest about this but the framing in the abstract ("minimal performance overhead") obscures that C³ can't fix CXL's fundamental inefficiencies.

**6. Memory Barriers are Hand-Waved:**

Section IV-D3 states "C³ does not directly handle barriers, but indirectly through the coherence messages and events that the core generates." For SWMR protocols this works, but for RCC it requires cores to "translate them into existing cache maintenance events (flush, invalidate) or specific coherence messages." This translation is left to the host, not C³, meaning RCC hosts need modification to work correctly with C³ - contradicting the "non-intrusive" claim.

**7. The "Compound State Machine" Complexity is Hidden:**

The Cartesian product of states creates exponential state space. For MOESI × CXL-MESI, that's 5 stable states × 5 stable states = 25 combinations, plus all transient states. Section IV-B mentions "many state combinations are never reachable" but doesn't quantify the final state machine complexity. This affects verification effort, synthesis time, and potentially controller latency.

**8. No Power or Area Numbers:**

Section IV-D4 claims "purely combinational and sequential logic, incurring minimal area and power overhead" but provides no synthesis results, gate counts, or energy measurements. For a hardware contribution, this is a significant omission.