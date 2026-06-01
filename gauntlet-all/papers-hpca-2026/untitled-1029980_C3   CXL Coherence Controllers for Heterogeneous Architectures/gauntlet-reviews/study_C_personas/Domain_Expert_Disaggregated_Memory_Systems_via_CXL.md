# C³: CXL Coherence Controllers for Heterogeneous Architectures

## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Imagine you've got two completely different server nodes—say, an Intel x86 box running TSO and an ARM box running a weak memory model—and you want them to share memory over CXL. The CXL spec cheerfully promises "multi-host coherent memory" in version 3.0, but here's the dirty secret: **nobody has actually built this for heterogeneous systems**, and the spec doesn't tell you *how* to make it work.

The problem is twofold:

**Problem 1: Protocol Mismatch.** Each host has its own cache coherence protocol (MESI, MOESI, MESIF). CXL has its own MESI-like protocol that looks similar but behaves differently. Look at Figure 3 (page 4)—when a MOESI host receives a `BISnpData` request (CXL's way of saying "share your data"), the MOESI protocol wants to transition to Owner state (O), keeping dirty data locally. But CXL-MESI has no O-state and expects a writeback. Now you've got the MOESI host thinking it owns dirty data while CXL thinks everything is clean. **Boom—inconsistent state.**

**Problem 2: Memory Model Mismatch.** x86 gives you TSO (stores are ordered). ARM gives you weak ordering (reorder everything unless you fence). When threads on different hosts share memory, whose rules apply? If you're not careful, you either break x86 programs (too weak) or cripple ARM performance (too strong).

**C³'s Solution:** They introduce a hardware component—call it a "coherence controller"—that sits at the boundary between each host's local coherence domain and the CXL fabric. This controller follows two rules:

1. **Flow Delegation (Rule I):** Any memory operation that has globally visible effects gets forwarded to the CXL directory. Incoming CXL snoops get translated into equivalent local protocol actions. The controller "simulates" the conceptual load/store that would trigger the right behavior in each domain (see Figure 6, page 7).

2. **Atomicity (Rule II):** When you forward a request across domains, you **stall** and produce no local effects until you get confirmation from the other side. This prevents the race condition shown in Figure 4 (page 6), where responding to an invalidation too early could let one host read stale data.

The net effect: each host keeps its native protocol and memory model intact. The compound system preserves what's called a "compound memory model"—the strongest guarantees from each domain propagate globally, but neither host is forced to strengthen beyond its own rules.

---

## Q2: The Key Insight

The **real delta** here is not a new coherence protocol, but a **methodology for synthesizing correct bridges** between arbitrary protocols without modifying them.

Prior work falls into two camps:
- **HeteroGen [68]** fuses directory state machines into a single unified FSM. This requires knowing the entire system topology at design time—completely incompatible with CXL's plug-and-play, dynamic topology philosophy.
- **Compound Memory Models [31]** provide the theoretical framework but are too abstract—they describe what ordering guarantees you need but not *how* to handle, say, the `BIConflict/BIConflictAck` handshake when CXL messages arrive out of order (Figure 2, page 4).

C³ bridges this gap with two concrete, implementation-aware rules that are **general enough to apply to any protocol pair** but **specific enough to handle CXL's message reordering and conflict resolution**. The translation logic is pre-computed at synthesis time (Section V describes a generator tool based on Protogen [66]) and embedded as a finite-state machine—no runtime lookup tables, no modifications to existing host protocols.

The insight that makes this tractable: **coherence flow delegation via conceptual load/store simulation**. By treating cross-domain translations as "what load/store would trigger this behavior in the other protocol?", they reduce the problem to composing well-understood local protocol actions. This is elegant because loads and stores are universal across all protocols.

**Contextual fit:** This is conceptually in the HieraGen [67] lineage of hierarchical protocol composition, but adapted for CXL's specific challenges (conflict resolution, asymmetric message flows). It also provides a concrete realization of compound MCMs [31] for the first time in a real interconnect context.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Rigorous Correctness Validation.** They don't just run benchmarks and hope. They use Murφ-based formal verification on the synthesized FSMs (Section VI-A) and run 100,000 iterations of litmus tests (IRIW, MP, 2+2W, etc.) across all protocol/MCM combinations. Table IV (page 10) shows clean results across MESI-CXL-MESI and MESI-CXL-MOESI configurations with both ARM and TSO cores. They also verify negative cases—intentionally removing fences to confirm forbidden outcomes appear—which is good experimental hygiene.

2. **Generality Demonstration.** They test 33 benchmarks from three suites (Splash-4, PARSEC, Phoenix) across multiple protocol combinations (MESI-CXL-MESI, MESI-CXL-MOESI, MESI-CXL-MESIF). Figure 10 (page 12) shows consistent behavior across all configurations, demonstrating the methodology isn't just a one-off MESI-to-CXL hack.

3. **Honest Performance Analysis.** Figure 11 (page 12) provides a latency breakdown by instruction type and latency bucket. They correctly identify that CXL slowdowns stem from **CXL's own protocol design** (6 message delays vs. MESI's 3 for dirty-owner write invalidation, directory blocking transient states causing convoy effects), not from C³'s translation overhead. This is intellectually honest—they're not hiding that CXL has inherent costs.

### Weaknesses

1. **Simulation-Only, No Real Hardware.** This is a gem5 study. Table III (page 9) shows their simulated parameters: 70ns cross-cluster link latency, star topology, 400ns total CXL memory access latency (calibrated to match prior work [57]). There's no validation against real CXL 3.0 hardware—because **none exists with multi-host coherence support** (as they acknowledge in Section I). This is understandable but limits confidence in the absolute numbers.

2. **Worst-Case Memory Placement Only.** Section V explicitly states: "We deliberately evaluate a worst-case scenario with all data in remote CXL memory." This maximally stresses C³ but ignores the hybrid case where hot data lives locally. The 5.5% average overhead (Section I, VI-C) might be pessimistic or optimistic depending on real access patterns.

3. **Syscall Emulation Mode.** They use gem5's SE mode (Section V), meaning no OS, no page tables, no TLB shootdowns. A real CXL system would have kernel involvement for memory mapping and potentially page migration. The 3.8-25.4% overhead claim (Figure 10 mean) doesn't include any OS coherence management costs.

4. **Benchmark Selection.** Splash-4, PARSEC, and Phoenix are classic parallel benchmarks, but they're primarily **compute-bound with regular access patterns**. There's no memcached, no OLTP, no random-access workloads where CXL's latency penalty would be maximally exposed. The workloads with 19-25% degradation (histogram, barnes, lu-ncont) are flagged in Section VI-C1 as having "hot-spot" cache lines—but this isn't explored systematically.

5. **CXL Cache Sizing Unaddressed.** The CXL cache must be inclusive of all CXL data cached by the host (Section IV-D1). What happens when this cache is undersized? Eviction storms triggering cross-domain writebacks? The paper doesn't explore capacity sensitivity.

---

## Q4: What the Authors Didn't Tell You

1. **The Scalability Question.** They evaluate a 2-cluster topology (Figure 1). CXL 3.0 supports fabrics with switches and many hosts. What happens when you have 8, 16, or 64 hosts sharing memory? The directory at the memory device becomes a serialization point. The "convoy effect" they identify in Section VI-C1 (blocking transient states causing load delays) will get worse. The paper is silent on fabric-scale behavior.

2. **The Fair Baseline Problem.** Their baseline is `MESI-MESI-MESI`—a hierarchical MESI system where C³ "functions as a passive device, simply forwarding inter-cluster coherence requests one-to-one" (Section VI-C). This means even the baseline has cross-cluster latency. A truly fair comparison would include a **native all-local memory baseline** to show the absolute cost of memory disaggregation. The 5.5% average overhead is *relative to a system already paying cross-cluster coherence costs*.

3. **RCC (GPU) Protocols Are Simplified.** Section IV-D2 discusses Release Consistency Coherence (RCC) for GPUs, noting that C³'s CXL cache isn't strictly inclusive—GPUs can hold stale data until explicit synchronization. Figure 8 shows a store-release flow. But there's no performance evaluation with RCC hosts, no GPU benchmarks, and the RCC discussion is less than a page. The paper title promises "Heterogeneous Architectures" including "GPUs, FPGAs, TPUs" (Section I), but the evaluation is CPU-only.

4. **Memory Barrier Costs Are Hidden.** Section IV-D3 waves away barriers: "C³ does not directly handle barriers, but indirectly through the coherence messages...the core awaits for completion." For TSO, barriers become load/store completions. For weak models, barriers become RCC acquire/release messages. But what's the overhead of propagating these across CXL? The litmus tests (Table IV) verify correctness but don't measure the *latency* of synchronization operations crossing coherence domains.

5. **The Hardware Complexity Handwave.** Section IV-D4 claims "minimal area and power overhead" because "the resulting hardware is purely combinational and sequential logic." But they provide no gate counts, no synthesis numbers, no area estimates. The C³ FSM is a product of two protocol FSMs (MESI × CXL = many transient states). How many states? How complex is the resulting logic? "Comparable to Arm's CHI protocol" is asserted but not demonstrated.

6. **No Contention Modeling.** The Garnet network model simulates flit latency and router delays, but Section V notes they use "static routing" with no mention of congestion modeling. What happens when multiple hosts simultaneously issue invalidations to shared data? The 70ns link latency is fixed; real CXL fabrics under load would see queuing delays. The "hot-spot" workloads (histogram, barnes) hint at this but the paper doesn't instrument contention.

7. **Verification Doesn't Cover All State Space.** Murφ model checking is state-space exploration. The paper doesn't report the state space size or whether it hit Murφ's memory limits. Complex protocol compositions can have millions of states. Did they verify the *full* compound FSM or a reduced model?