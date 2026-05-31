# C³: CXL Coherence Controllers for Heterogeneous Architectures

## The "No-BS" Summary

This paper tackles a problem that doesn't exist yet in deployed hardware but will become critical: **what happens when you connect CPUs with different cache coherence protocols and memory consistency models to the same CXL-attached memory pool?**

The authors build **C³**, a hardware translation layer that sits between a host's native coherence protocol (MESI, MOESI, MESIF, or even GPU-style RCC) and CXL's coherence protocol. The key insight is that you can bridge these protocols without modifying either one—you just need a state machine that tracks *both* protocol states simultaneously and translates messages at the boundary.

**What they actually did:** Implemented C³ in gem5 (simulation, not silicon), validated correctness through formal verification (Murφ model checker) and litmus tests, and showed 3.8-25.4% performance overhead (average 5.5%) compared to a hypothetical native all-MESI system without CXL.

**What they did NOT do:** Run on real CXL hardware (none exists with multi-host coherence), test with real heterogeneous CPUs (they simulated x86 vs. ARM by toggling a TSO flag in gem5), or demonstrate this at scale beyond 2 clusters with 8-30 cores total.

---

## The Core Mechanism: A Whiteboard Explanation

Imagine you're a translator at the United Nations, but instead of translating French to English, you're translating "Intel-speak" to "CXL-speak" to "ARM-speak."

**The Problem:**
- Intel CPUs use MESI (or MESIF) coherence with TSO memory ordering
- ARM CPUs use MOESI coherence with weak memory ordering  
- CXL has its own MESI-like protocol with quirks (like explicit conflict resolution handshakes)
- When Host A writes to shared memory, Host B needs to see that write—but the protocols disagree on *how* and *when* that visibility happens

**The C³ Solution:**

1. **Compound State Machine:** C³ maintains a *pair* of states—one for the local protocol, one for CXL. So instead of just tracking "this cache line is Modified," it tracks "(Modified-local, Modified-CXL)." This Cartesian product of states lets it know exactly what both sides think is happening.

2. **Two Design Rules:**
   - **Rule I (Flow Delegation):** If a local cache can't satisfy a request (e.g., needs data from remote memory), forward it to CXL. If CXL sends a snoop (invalidation request), forward it to local caches. Never let one domain think it handled something that affects the other.
   - **Rule II (Atomicity):** When you forward a request across the boundary, *stall everything else to that cache line* until you get a completion. This prevents race conditions where Host B sees a partial update.

3. **Translation Tables:** The authors pre-compute all possible message translations at design time. When C³ receives a `BISnpInv` (CXL invalidation snoop) while in state (M, M), it knows to translate that into a `Fwd-GetM` to the local caches. No runtime lookup—it's baked into the FSM.

**The Clever Trick:** They don't modify existing protocols at all. C³ "simulates" the effect of a load or store in the target domain to trigger the right coherence flow. It's like saying "I need to invalidate this line in the local cluster, so I'll pretend someone did a store that requires exclusive access, which will naturally cause the invalidation."

---

## The Critique: Strengths & Weaknesses

### Why It Got In (The Strong Points)

1. **First Systematic Treatment of a Real Problem:** CXL 3.0 promises multi-host coherence, but the spec punts on how to actually integrate heterogeneous hosts. This paper is the first to provide a principled methodology with formal correctness guarantees. The compound memory model foundation from PLDI '23 gives them theoretical legitimacy.

2. **Formal Verification + Empirical Validation:** They didn't just simulate—they ran the FSMs through Murφ model checking and validated with litmus tests (IRIW, MP, SB, etc.). The fact that they checked all thread-to-cluster assignments for heterogeneous MCM combinations shows rigor.

3. **Non-Intrusive Design:** C³ doesn't require modifying existing CPU coherence controllers. This is crucial for practical adoption—Intel isn't going to redesign their CHA just to support ARM hosts on the same CXL fabric.

4. **Open Source Artifact:** They released the gem5 model and generator tool. This is increasingly important for reproducibility and lets others build on their work.

### Where It's Weak (The Skeleton in the Closet)

1. **No Real Hardware, No Real Heterogeneity:**
   - They simulated "heterogeneous MCMs" by toggling gem5's `needsTSO` flag. This is *not* the same as running actual x86 and ARM cores with their real microarchitectural differences.
   - CXL multi-host coherence hardware doesn't exist commercially. They're solving a problem for silicon that won't ship for 2-3 years.
   - The "CXL latency" is modeled via Garnet network parameters, not actual PCIe/CXL transport simulation.

2. **Evaluation Gaps:**
   - **No tail latency data.** They report average execution time, but CXL's conflict resolution handshakes (BIConflict/BIConflictAck) will murder p99 latency under contention. Where's the latency distribution?
   - **No memory bandwidth saturation tests.** What happens when the CXL link hits its 64 GB/s limit? Their workloads (PARSEC, SPLASH-4) are classic parallel benchmarks, not memory-bandwidth hogs.
   - **No multi-switch topology.** They test a star topology with one CXL directory. Real CXL 3.0 fabrics will have switches, and coherence traffic through switches adds latency and ordering complexity.
   - **No Type-3 pooled memory.** They assume HDM-DB (device-backed memory), but the interesting CXL use case is memory pooling across multiple hosts with dynamic allocation.

3. **The 5.5% Overhead is Misleading:**
   - The baseline is "MESI-MESI-MESI" (homogeneous, no CXL). But this baseline doesn't exist in the real world—if you're using CXL, you're already paying the CXL tax.
   - The *real* comparison should be against a properly tuned NUMA-aware system with local memory. How does C³ compare to just pinning threads to local DRAM and using explicit message passing for cross-node communication?

4. **Scalability Questions:**
   - They test 2 clusters. What happens with 8 clusters? 32? The compound state space grows, and the CXL directory becomes a serialization bottleneck.
   - The paper mentions "dynamic topologies" as a CXL feature, but they don't test hot-adding or removing hosts.

5. **RCC (GPU) Support is Hand-Wavy:**
   - Section IV-D2 claims C³ handles RCC, but the evaluation only tests MESI/MOESI/MESIF. No GPU workloads, no actual RCC protocol implementation shown.
   - The claim that "CXL cache is not kept strictly inclusive with host caches" for RCC is a significant deviation from their design rules—how does this interact with Rule I?

---

## Discussion Questions

1. **On the Baseline Choice:**
   "The paper compares C³ against a hypothetical all-MESI system without CXL. But in a real deployment, the alternative to heterogeneous CXL coherence isn't 'native MESI everywhere'—it's either (a) software-managed coherence with explicit flushes, or (b) partitioned memory with no sharing. How would C³'s overhead compare against a well-optimized software coherence scheme like what AIFM or Fastswap use?"

2. **On the Conflict Resolution Overhead:**
   "Figure 2 shows that CXL's BIConflict handshake adds 2 extra message round-trips compared to textbook MESI. In Section VI-C1, you identify this as the primary source of slowdown for write-heavy workloads. Have you characterized what fraction of coherence transactions actually trigger conflict resolution? Is there a workload pattern (e.g., false sharing, lock contention) that would cause this to dominate?"

3. **On the Memory Consistency Guarantees:**
   "The paper claims C³ preserves each host's native MCM by implementing a compound memory model. But the litmus tests only verify *forbidden* outcomes aren't observed—they don't verify that *allowed* outcomes (which might be performance-critical for weak MCMs) are still reachable. How do you ensure that C³ doesn't accidentally *strengthen* the memory model for ARM hosts, potentially hiding bugs that would appear on native ARM hardware?"

---

## Contextual Fit: Where This Sits in the Literature

This paper is building on several threads:

- **Compound Memory Models (PLDI '23):** The theoretical foundation. C³ is essentially "compound MCMs made real for CXL."
- **HeteroGen (HPCA '22) / HieraGen (ISCA '20):** Prior work on synthesizing heterogeneous coherence protocols. C³ argues these don't handle CXL's dynamic topology and conflict resolution.
- **Pond (ASPLOS '23) / TPP (ASPLOS '23):** CXL characterization and tiered memory management. Those papers assume homogeneous hosts; C³ addresses what happens when hosts differ.
- **Spandex (ISCA '18):** Another approach to heterogeneous coherence, but requires a unified interface that all hosts implement.

**What's missing from the related work discussion:** The paper doesn't engage with the RDMA-based disaggregation literature (FaRM, RAMCloud, Clover) that solved similar coherence problems in software. It also doesn't discuss how C³ relates to CCIX (the competing interconnect standard) or whether the same principles apply there.

---

## The Bottom Line

This is a **solid systems paper** that identifies a real (if not yet deployed) problem and provides a principled solution with formal backing. The contribution is the methodology—the two design rules and the compound state machine approach—not the specific implementation.

**For your research:** If you're working on CXL memory systems, this paper tells you that heterogeneous coherence is *solvable* without protocol modifications, but the devil is in the performance details. The 5.5% average overhead sounds acceptable, but I'd want to see tail latency under contention before betting a production system on it.

**What to watch for:** When real CXL 3.0 multi-host hardware ships (likely 2026-2027), someone will need to validate whether C³'s gem5 model predictions hold up. The conflict resolution overhead in particular could be much worse on real silicon with real PCIe latency variability.