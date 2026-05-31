# C³: CXL Coherence Controllers for Heterogeneous Architectures

## Q1: Whiteboard Explanation

Let me draw this out for you conceptually.

**The Problem in Plain Terms:**

Imagine you have two different CPU architectures — say an Intel x86 box and an ARM server — and you want them to share a pool of CXL-attached memory. CXL 3.0 promises this beautiful abstraction: multiple hosts accessing the same memory with hardware-managed cache coherence, just like accessing a NUMA node. Sounds great, right?

Here's where it breaks down: Intel's cache coherence protocol speaks one "language" (MESI-based with TSO memory ordering), ARM speaks another (MOESI-based with weak ordering), and CXL.mem speaks yet another dialect (MESI-like but with explicit conflict resolution handshakes due to the off-chip, reorderable nature of PCIe). When Host 1 writes data and Host 2 tries to read it, the coherence messages crossing between these domains don't map 1:1. Worse, the *memory consistency models* differ — x86 programmers expect stores to be seen in order without explicit fences; ARM programmers know they need barriers.

**What C³ Actually Does:**

C³ is a **translation shim** — a hardware component that sits at the boundary between a host's internal coherence domain and the CXL fabric. Think of it as a bilingual interpreter that:

1. **Translates coherence messages** between protocols (e.g., converts a host's `GetM` request into CXL's `MemRd,A` with appropriate handshaking)
2. **Enforces two key rules** to preserve correctness:
   - *Rule I (Flow Delegation)*: Any memory operation that affects remote visibility must be forwarded to the global CXL directory — you can't just handle it locally and pretend nothing happened.
   - *Rule II (Atomicity)*: When you forward a request across domains, you must stall the origin domain until you get confirmation — no producing coherence effects before the other side is done.

**The Mechanism:**

C³ maintains a **compound state machine** — a Cartesian product of the host's cache states and the CXL cache states. For example, state `(M, M)` means the host cache has Modified data AND the CXL-facing cache also holds it as Modified. When a BISnpInv (CXL invalidation snoop) arrives, C³ consults a pre-computed translation table: "In state (M,M), receiving BISnpInv means I must forward this as a store-like operation to the host caches, transitioning to a transient state until they acknowledge."

The clever part: C³ doesn't modify existing host cache controllers. It "simulates" the coherence effect by triggering native protocol flows. Want to invalidate host caches? Generate a Fwd-GetM internally. The host caches respond as they normally would — they don't know they're talking to CXL-backed memory.

## Q2: The Key Insight

**The Real Contribution (The Delta):**

The *actual* innovation is **deriving concrete, implementation-aware design rules from the abstract theory of compound memory models** and showing they work for CXL's specific quirks.

Prior work on compound memory models (Goens et al. [31]) established the *theoretical* foundation: if you forward operations correctly and ensure atomicity at domain boundaries, you can compose heterogeneous MCMs without breaking either one's guarantees. But that work operates in an idealized framework where memory operations propagate atomically between threads — it doesn't address the nasty realities of fabric-level coherence.

CXL introduces specific challenges that prior hierarchical coherence work (HieraGen [67], HeteroGen [68]) doesn't handle:
- **Conflict resolution handshakes** (`BIConflict`/`BIConflictAck`) — CXL explicitly serializes conflicting requests because it can't rely on implicit ordering from message arrival times (Figure 2 shows three scenarios of this)
- **Dynamic topologies** — devices can be added/removed at runtime, so you can't pre-fuse all directory state machines like HeteroGen proposes
- **MESI-like protocol that *isn't* MESI** — the stable states match, but transient states and transaction flows differ significantly (Table I, Section III-A)

C³'s insight is that you can bridge these by:
1. Making the CXL-facing cache *inclusive* of all remotely-cached data (so the CXL directory always knows who to snoop)
2. Treating cross-domain requests as "virtual loads/stores" that trigger native flows in the target domain
3. Blocking on transient states until handshakes complete, naturally integrating with CXL's conflict resolution

**What Makes This Non-Obvious:**

The paper's Figure 3 beautifully illustrates the problem: a MOESI cluster receiving a `BISnpData` transitions to O-state (Owner, dirty sharer), but CXL-MESI expects a writeback and assumes S-state (clean sharer). You now have *inconsistent global state* — the MOESI host thinks it owns dirty data requiring future writeback, while CXL thinks everyone has clean copies. C³ prevents this by its state compounding and delegation rules.

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Rigorous Correctness Verification (Section VI-A)**

The authors don't just claim correctness — they demonstrate it through:
- **Formal verification via Murφ model checker**: Extended from HeteroGen's methodology, they verify FSMs against litmus tests (IRIW, MP, 2+2W, etc.) for all thread-to-cluster assignments
- **Exhaustive litmus testing in gem5**: 100,000 runs per test, across 6 protocol/MCM combinations (Table IV)
- **Control validation**: They intentionally removed synchronization primitives to confirm tests *would* detect forbidden outcomes when they should occur

This is more thorough than most cache coherence papers I've reviewed. The fact that removing store-store fences on TSO cores still passes (because TSO provides that ordering natively) while removing other fence types produces failures validates that C³ correctly propagates — but doesn't strengthen — local MCM guarantees.

**2. Demonstration of Generality (Section VI-B, Figure 9)**

They test multiple protocol combinations (MESI-CXL-MESI, MESI-CXL-MOESI, MESI-CXL-MESIF) across multiple MCM combinations (ARM-ARM, TSO-TSO, ARM-TSO). The results show C³ works consistently — the performance degradation patterns are explained by MCM strength differences (22-43% for TSO vs weak ARM), not by C³ breaking down.

**3. Honest Performance Analysis (Section VI-C1, Figure 11)**

Figure 11 is refreshingly transparent. They decompose cache miss latency into three buckets (low/medium/high) and show that CXL-sensitive workloads see a 2.9× increase specifically in high-latency (cross-cluster) accesses. They correctly attribute this to CXL's protocol design — 6 remote message delays vs. MESI's 3 for dirty owner invalidation, plus non-pipelined blocking transient states at the directory. This is inherent to CXL, not C³.

### Weaknesses

**1. Simulation Environment Limitations**

- **No real CXL hardware validation**: They use gem5's Garnet network model configured to approximate CXL latency (70ns link, 400ns round-trip per [57]). While they justify this as focusing on "protocol bridging" rather than PCIe transport, it means they don't capture real CXL switch behavior, credit-based flow control, or fabric contention under load.
- **Syscall emulation mode**: No OS involvement means they can't evaluate how C³ interacts with page faults, TLB shootdowns, or kernel memory management — all crucial for real CXL deployment.
- **Scaled-down workloads**: "Small input sizes and scale the cache sizes and number of cores for each workload to achieve similar MPKI as observed in real hardware" (Section V). This calibration approach is reasonable but means absolute performance numbers are not directly applicable to production systems.

**2. Missing Multi-Host Scaling Analysis**

The entire evaluation uses **only two compute nodes** (Figure 1's topology). CXL 3.0's multi-host coherence is interesting precisely because it enables *many* hosts sharing memory. What happens with 4, 8, or 16 hosts? Does C³'s compound state machine explode in complexity? Does the CXL directory's blocking behavior during conflict resolution become a bottleneck? The paper provides no data.

**3. Workload Selection Bias**

PARSEC, SPLASH-4, and Phoenix are classic shared-memory benchmarks but don't represent CXL's target applications:
- No large-memory workloads (databases, in-memory analytics) that would actually benefit from memory pooling
- No latency-sensitive workloads (HPC stencils, key-value stores) where the 5.5% average overhead might mask larger tail latency penalties
- No producer-consumer patterns across heterogeneous hosts — the very use case CXL 3.0 enables

**4. Selective Protocol Coverage**

They test MESI, MOESI, and MESIF (SWMR protocols) but only briefly mention RCC (Release Consistency Coherence) for GPUs in Section IV-D2. The RCC discussion is theoretical — no performance data, no litmus tests for GPU-like consistency. Given that heterogeneous systems often include accelerators, this is a significant gap.

**5. No Power or Area Estimates**

Section IV-D4 claims "minimal area and power overhead" and states C³-logic is "purely combinational and sequential logic." But no synthesis results, gate counts, or power estimates are provided. The CXL cache must be inclusive — how large must it be for realistic workloads? The paper dismisses this as "can be integrated with the LLC" without quantification.

## Q4: What the Authors Didn't Tell You

**1. The Elephant in the Room: CXL 3.0 Multi-Host Hardware Doesn't Exist**

The authors acknowledge "to date, to the best of our knowledge, no hardware platform supports multi-host coherence CXL—not even for homogeneous systems" (Section I). This is a crucial caveat buried in the introduction. The entire paper solves a problem for hardware that *might* ship in 2025-2026. The CXL spec exists, but whether vendors will implement multi-host coherence with C³-compatible interfaces is speculative.

**2. The BIConflict Handshake Overhead is Underexplored**

Figure 2 shows the conflict resolution mechanism requires additional round-trips (`BIConflict` → `BIConflictAck`). Under high contention (multiple hosts fighting for the same cache lines), this handshaking could serialize access severely. The paper's workloads apparently don't stress this case — the "hot-spot" discussion in Section VI-C1 mentions detecting them but doesn't quantify how much contention-induced serialization contributed to the 19-25% slowdowns in affected workloads.

**3. Inclusivity Requirement Has Real Costs**

Rule I requires the CXL cache to remain "inclusive of all remote data cached by a host" (Section IV-B). For systems with large host LLC (Intel's now ship with 100MB+), this inclusion requirement could either:
- Require an equally large CXL cache (area/power cost)
- Force frequent victim evictions (performance cost via Figure 7's eviction flow)

The paper assumes the CXL cache "can be integrated with the LLC" but doesn't discuss sizing tradeoffs or back-invalidation policies.

**4. RCC Support is Handwavy**

Section IV-D2 discusses RCC (used by GPUs) but reveals that C³ breaks the inclusion property for RCC: "C³ can directly respond to invalidations from the CXL directory without host cache involvement." This works because RCC relies on self-invalidation at release/acquire points, but it means:
- The CXL cache and host caches can be *inconsistent* between synchronization points
- A GPU thread reading between release/acquire could see stale data from CXL's perspective

This is correct per RCC's semantics but creates subtle programming hazards in heterogeneous systems where CPU threads (expecting MESI guarantees) and GPU threads share data. The paper doesn't discuss this programmability challenge.

**5. The Generator Tool is the Real Contribution**

Section V mentions a "generator tool [47] that takes machine-readable SSP specifications for both host and CXL CC protocols as input, merges them, and outputs SLICC code for C³." This automation is arguably more valuable than any single C³ instance — it enables systematic exploration of protocol combinations. But the tool itself isn't released with this paper (reference [47] is a separate submission), and the synthesis methodology details are sparse.

**6. What About CXL.cache?**

The paper explicitly states "We base our work on CXL.mem" (Section II-A) and focuses on Type 2/3 devices with host-managed device memory. But CXL.cache enables devices (like GPUs or accelerators) to cache host memory, inverting the coherence relationship. How would C³ apply to CXL.cache scenarios? The paper doesn't discuss this, though it's equally important for heterogeneous systems.

**7. Compiler Mappings Are Assumed Unchanged, But Are They?**

The paper claims existing software remains correct "without modification or re-compilation" because compound MCMs preserve local MCM axioms. But compiler mappings for concurrent primitives (footnote 1, page 2) are architecture-specific. If an x86 binary compiled assuming TSO runs on a heterogeneous system where some data traverses CXL to ARM cores, are the compiler's elided fences still safe? The formal verification assumes correct synchronization is present — it doesn't analyze what happens with legacy binaries that exploited TSO's implicit guarantees.