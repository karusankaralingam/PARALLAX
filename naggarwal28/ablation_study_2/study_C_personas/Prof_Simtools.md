# C³: CXL Coherence Controllers for Heterogeneous Architectures

## Q1: Whiteboard Explanation

Let me draw you the core problem and solution.

**The Problem Setup:**
Imagine two compute nodes—one Intel x86 server, one ARM server—both wanting to share memory through CXL (Compute Express Link). Sounds simple, right? It's not.

Each node speaks a different "language":
- Intel uses MESI/MESIF coherence protocol with TSO (Total Store Order) memory model
- ARM uses MOESI coherence with a weak/relaxed memory model
- CXL has its own MESI-like protocol with subtle but critical differences (like explicit conflict resolution handshakes via BIConflict messages)

The semantic gap is brutal. Look at Figure 3: when a MOESI host receives a BISnpData (requesting shared access), it wants to downgrade to O-state (dirty sharer). But CXL expects a writeback and S-state. Now you have inconsistent global state—MOESI thinks it holds dirty data requiring writeback, CXL thinks everything's clean.

**The C³ Solution:**
C³ is a "coherence controller" that sits at the boundary between each host's local coherence domain and CXL's global domain (Figure 5). It's essentially a protocol translator with two fundamental rules:

1. **Flow Delegation (Rule I)**: Any memory operation with global visibility must be forwarded across domains. You can't satisfy a GetM locally if remote hosts have shared copies—the CXL directory must serialize everything.

2. **Atomicity (Rule II)**: When you forward a request, you MUST NOT produce any coherence effects in the origin domain until the target domain confirms completion. Otherwise, you create race conditions (Figure 4 shows exactly what goes wrong).

The implementation trick is "transaction nesting"—C³ translates coherence messages by conceptually simulating the memory access that would trigger equivalent flows in the target protocol. A host's GetS becomes a "load" to the CXL cache; a CXL BISnpInv becomes a "store" that triggers local invalidations.

The state machine tracks compound states—Cartesian products of both protocol states (e.g., (M,M) or (I,S))—enabling context-aware translations.

## Q2: The Key Insight

The key insight is that **heterogeneous coherence protocols can be correctly bridged without modifying existing protocol state machines by enforcing two simple rules that guarantee compound memory model semantics**: forward all globally-visible operations across domain boundaries, and ensure atomic completion before producing any local effects.

This is consequential because prior approaches required either:
1. **Merged directories** (HeteroGen [68])—requires knowing the entire system a priori, incompatible with CXL's dynamic plug-and-play topology
2. **Custom unified protocols** (Memglue [21])—requires recompilation and new compiler mappings
3. **Abstract formal models** (Compound MCMs [31])—theoretically sound but don't address practical race resolution like CXL's BIConflict handshakes

C³'s innovation is translating the *abstract* compound memory model principles into *concrete, implementation-aware* rules that handle real distributed system complexities: message reordering, conflict resolution handshakes, and transient state management.

The practical consequence: you can connect an x86 TSO cluster and an ARM weak-ordering cluster to shared CXL memory, and **each cluster's existing binaries run correctly without recompilation**. The compound MCM ensures local ordering constraints propagate globally—TSO threads maintain TSO guarantees, ARM threads see their expected relaxed behaviors, and cross-cluster synchronization just works.

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Strong Correctness Methodology:**
The dual-pronged verification approach is commendable. They use Murφ-based formal verification on the generated FSMs (Section VI-A) covering standard litmus tests (IRIW, MP, 2+2W, etc.) with all thread-to-cluster assignments. Then they empirically validate the SLICC controllers in gem5 with 100,000 iterations per test (Table IV). Critically, they include **negative controls**—intentionally removing fences to confirm forbidden outcomes *can* be observed when they should be. This is rigorous methodology that many papers skip.

**2. Artifact Availability and Reproducibility:**
The artifact appendix is detailed. They provide:
- Complete gem5 source with all protocol variants
- Generator tool for synthesizing C³ from SSP specifications
- Docker containers (both build-from-source and prebuilt)
- Estimated times (~4-12 hours for full evaluation on 32-core server)
- DOI: https://doi.org/10.5281/zenodo.17828238

**3. Honest Performance Analysis:**
Section VI-C1 and Figure 11 transparently attribute the 3.8-25.4% slowdown to CXL protocol overhead, **not** C³ itself. They explain CXL requires 6 message delays vs MESI's 3 for dirty writes, plus blocking transient states that prevent pipelining. This is intellectually honest—the overhead is inherent to CXL's design for handling network reordering.

### Weaknesses

**1. Simulation Validity Concerns:**
This is my primary concern. They use **gem5's Garnet network model** (an on-chip network simulator) to model CXL communication (Section V): *"Although Garnet was originally designed as an on-chip network and real CXL systems communicate over a PCIe fabric..."*

This abstraction is risky. CXL runs over PCIe 5.0/6.0 with specific characteristics:
- Credit-based flow control
- FLIT-level error correction
- Specific retry mechanisms
- Asymmetric bandwidth (x16 lanes typically)

They configured Garnet with "70ns link latency" to match 400ns round-trip memory access from [57], but this is just latency matching—it doesn't capture PCIe's flow control dynamics, credit stalls, or congestion behavior. For a paper about protocol translation, this seems adequate, but performance claims should carry an asterisk.

**2. Limited Scale Testing:**
Table III shows 8-30 cores, calibrated per-workload to match MPKI observed on "real hardware" (Intel Sapphire Rapids). But the paper claims CXL's value is in large-scale disaggregated systems. Figure 1 shows the motivating vision of datacenter-scale memory pooling. Testing at 8-30 cores with "small input sizes" (Section V) doesn't validate scalability claims.

**3. Syscall Emulation Mode Limitations:**
They use gem5 SE mode (Section V), not full-system simulation. This means:
- No OS overhead (context switches, page faults, TLB misses)
- No interrupt handling across CXL boundaries
- No NUMA-aware memory allocation policies

For a system targeting datacenter memory pooling, these omissions matter. The paper acknowledges CXL memory appears as "a separate NUMA node" (Section I), but they never model NUMA effects.

**4. Missing DRAM Timing Model:**
Table III shows "DDR5, 4400 MHz, 1-channel, 10ns latency"—but this is a fixed latency, not a DRAM timing model. Real DDR5 has refresh pauses, bank conflicts, row buffer effects, and queue depths that significantly impact tail latency. For coherence-heavy workloads with bursty traffic, this matters.

**5. No RTL Validation:**
The C³-logic FSM is purely "combinational and sequential logic" (Section IV-D4), but they provide no synthesis results, area estimates, or critical path analysis. They claim it's "comparable to conventional hierarchical coherence controllers" but offer no evidence. Given they're adding compound state tracking, this claim deserves validation.

## Q4: What the Authors Didn't Tell You

**1. The Benchmark Selection Hides Worst-Case Behavior:**
The workloads (PARSEC, Splash-4, Phoenix) are *intra-node* parallel benchmarks designed for shared-memory multiprocessors. They have well-tuned synchronization patterns. Real heterogeneous CXL deployments would have *distributed* applications with potentially pathological sharing patterns. The authors carefully calibrated core counts and input sizes to match MPKI on real hardware—this is good methodology for reproducibility but potentially cherry-picks scenarios where CXL overhead is bounded.

Figure 11 reveals the truth: workloads sensitive to CXL (barnes, lu-ncont, histogram) show 2.9× increases in high-latency (>400ns) accesses. The "Mean" in Figure 10 averages over workloads where ~20 benchmarks show <10% overhead, hiding the outliers.

**2. The "Hot Spot" Problem is Buried:**
Section VI-C1 mentions they "detected some cache lines are hot-spots for both read and write across the two clusters, in CXL-sensitive applications." This convoy effect from CXL directory blocking is a fundamental scalability concern that deserves more attention than a single sentence.

**3. The Simulation Warm-Up Problem:**
They never mention warm-up methodology. With 100K litmus test iterations (Section VI-A), cache and directory state from previous iterations could affect results. For performance experiments, cold-start vs. warm-cache results could differ significantly, especially with the relatively small workload sizes used.

**4. The Generator Tool is Protogen-Based with Known Limitations:**
Their generator "is based on Protogen [66]" (Section V), which generates directory protocols from SSP specifications. But Protogen has documented limitations—it assumes specific directory structures and message types. The paper mentions their tool "has one current limitation: it does not support separate instruction and data caches" (footnote 7). This suggests the generator may have other undocumented constraints.

**5. RCC (Release Consistency Coherence) Treatment is Hand-Wavy:**
Section IV-D2 describes RCC handling in one paragraph, noting "C³ can directly respond to invalidations from the CXL directory without host cache involvement." But RCC is complex—what about store buffers? What about the TSO-to-RCC boundary where TSO expects SWMR but RCC uses self-invalidation? Figure 8 shows one example, but the "simplified for brevity" note suggests complexity they're not exposing.

**6. No Power Modeling:**
Memory disaggregation papers often claim energy efficiency benefits from reducing stranded memory. The introduction cites "billions of dollars in wasted energy" (Section I), but they provide zero power analysis. The C³ controller adds logic at every host interface—what's the power overhead?

**7. The "Realistic" CXL Latency is Based on One Citation:**
The 400ns CXL round-trip latency comes from [57] (Maruf et al., TPP paper). But that's a single measurement from one CXL memory expander. CXL 3.0's switch-based topologies could have much higher latencies. Their "70ns link latency" was "determined empirically to match" this—it's curve-fitting, not characterization.

**8. Missing Discussion of CXL.cache:**
The paper focuses entirely on CXL.mem (Section II-A). But CXL also defines CXL.cache for accelerator-attached caches. A complete heterogeneous CXL system might need both. The interaction between CXL.cache devices and C³-bridged CXL.mem hosts is unexplored.