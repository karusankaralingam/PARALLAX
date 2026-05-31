# C³: CXL Coherence Controllers for Heterogeneous Architectures — A Deep Dive

## Q1: Whiteboard Explanation

Let me draw you the picture of what this paper is really about.

**The Problem Setup:**
Imagine you have two data center nodes trying to share memory over CXL (Compute Express Link). Node 1 runs Intel x86 CPUs with their MESI coherence protocol and TSO memory model. Node 2 runs ARM CPUs with their MOESI-variant coherence and a weak memory model. Both want to access the same CXL-attached memory pool using regular load/store instructions.

Here's the ugly reality: CXL 3.0 promises "multi-host coherent memory" but *doesn't tell you how to actually make heterogeneous hosts work together correctly*. Each host speaks a different coherence "language" (MESI vs MOESI vs whatever) and expects different memory ordering guarantees (TSO vs weak). Naively connecting them creates a mess—inconsistent states, potential data races, and broken programs.

**The Core Mechanism:**
C³ is essentially a **protocol translator** that sits at the boundary between each host's local coherence domain and the global CXL coherence domain. Think of it as an embassy between two countries with different legal systems.

The key idea is deceptively simple but powerful:

1. **Flow Delegation (Rule I):** When a host wants to do something with CXL memory that affects other hosts (like writing to shared data), C³ translates that request into the "language" of the CXL protocol, sends it globally, and translates the response back. It's like C³ "simulating" what the host *would have done* if it spoke CXL natively.

2. **Atomicity (Rule II):** While a request is being handled across the domain boundary, C³ stalls any other coherence activity on that cache line in the originating domain. This prevents the nasty race conditions where one host thinks it completed a write but another host hasn't seen the invalidation yet.

**The State Machine:**
C³ maintains a *compound state*—essentially tracking both the local protocol's view AND the CXL protocol's view simultaneously. So a cache line might be in state (M, S) meaning "Modified in local MOESI, Shared in CXL-MESI." This allows C³ to decide: do I need to cross the domain boundary, or can I handle this locally?

The clever trick is that C³ generates translation tables at synthesis time (not runtime). For every combination of incoming message and current compound state, there's a pre-computed action: what to send to the other domain, what transient state to enter, what completion to wait for.

**Why This Matters:**
CXL has this nasty `BIConflict` handshake mechanism (Figure 2) because messages can get reordered on the PCIe fabric—unlike on-chip networks where you control everything. When a host is waiting for permission to write (CmpM) and suddenly gets an invalidation (BISnpInv), it doesn't know which came first at the directory. The handshake resolves this ambiguity. C³ has to handle all these corner cases while also translating between protocols.

---

## Q2: The Key Insight

**The Real Innovation:**
The *delta* here is not building a CXL bridge—that's plumbing. The innovation is **a principled methodology for designing coherence bridges that preserve each host's native memory consistency model by construction**.

Previous work like HeteroGen [68] synthesizes merged directories, but that requires knowing all hosts at design time—incompatible with CXL's dynamic topology where devices come and go. HieraGen [67] does hierarchical composition but can't handle CXL's conflict resolution handshakes. Compound Memory Models [31] give you the theory but not the implementation recipe for a real distributed system with message reordering.

C³'s contribution is translating the abstract CMM axioms into two *concrete, implementable rules* that, when followed, guarantee the compound MCM properties hold:

1. **Flow Delegation** ensures global visibility—the CXL directory always knows who has what, so it can correctly orchestrate invalidations.

2. **Atomicity** ensures that forwarded operations appear instantaneous to the originating domain—no half-done coherence transactions that break causality or multi-copy atomicity.

The magic trick (Section IV-B) is **transaction nesting with coherence flow delegation**. When a local GetS comes in, C³ doesn't just forward a translated message—it conceptually "simulates" a load operation in the CXL domain. The translation tables (Table II) encode all these mappings statically.

**Why It Works:**
Because C³ operates at the coherence controller level (not inside the CPU or caches), it requires *zero modifications* to existing host hardware. The LLC controller just sees C³ as another directory; the CXL fabric just sees C³ as another cache controller. This is critical for practical adoption—you're not asking Intel to redesign their cache hierarchy.

The compound state tracking (Section IV-B, "State compounding") is elegant: by maintaining both local and global views, C³ can make intelligent decisions. State (M, M) means the host has dirty data AND CXL knows about it—forward the invalidation. State (I, M) means C³'s CXL cache has dirty data but no local cache does—respond immediately without touching the host.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Rigorous Correctness Validation (Section VI-A)**
The authors don't just claim correctness—they verify it through:
- **Formal verification** using Murφ model checker on the generated FSMs, testing litmus tests (IRIW, MP, 2+2W, CoRR1, CoRR2, LB, R, RWC, S, SB, WRC, WRW+2W, WWC) with all thread-to-cluster assignments
- **Empirical litmus testing** in gem5 (100,000 iterations each) across multiple protocol/MCM combinations
- **Control tests** with synchronization removed to confirm forbidden outcomes ARE detectable

Table IV is convincing: every combination of {MESI-CXL-MESI, MESI-CXL-MOESI} × {Arm-Arm, TSO-Arm, TSO-TSO} passes all litmus tests. This is the right way to validate a coherence protocol.

**2. Generality Demonstrated (Section VI-B, Figure 9)**
They actually test heterogeneous combinations—not just homogeneous setups dressed up as heterogeneous. Figure 9 shows that mixing ARM/TSO MCMs in different clusters only causes 2.2-14.4% slowdown compared to 22-43% for enforcing TSO everywhere. This validates the core claim: you preserve each host's native performance characteristics.

**3. Detailed Performance Analysis (Section VI-C)**
Figure 11's breakdown is excellent forensics. They identify that CXL slowdowns come from:
- 2× message complexity (6 delays vs 3 for dirty-owner writes)
- Blocking transient states at the CXL directory preventing request pipelining
- Convoy effects from hot cache lines

Crucially, they correctly attribute these slowdowns to **CXL protocol design**, not C³ overhead. The 4-26.6% overhead (avg 5.5%) is reasonable for crossing a domain boundary.

### Weaknesses

**1. Limited Scale Evaluation**
Table III shows 8-30 cores, 2 clusters, 1 CXL memory node. This is far from realistic data center scale:
- What happens with 8 hosts sharing CXL memory? The directory tracking overhead grows.
- What about multiple CXL switch hops? Their 70ns link latency (Table III) assumes single-hop.
- The CXL directory is a single point of serialization—no evaluation of contention at scale.

**2. Synthetic Workload Distribution**
They place *all* data in CXL memory (Section V, paragraph 3) to "stress-test C³." But this maximizes coherence traffic artificially. Real systems would use CXL for specific shared regions while keeping private data local. The "practical" hybrid configuration they mention is never evaluated.

**3. No Real Hardware Validation**
Everything is gem5 simulation with Garnet network modeling. While they calibrate MPKI to match Intel Sapphire Rapids measurements, the critical CXL-specific behaviors (especially the conflict resolution handshakes, timing, and contention) are approximated. The footnote admits: "no hardware platform supports multi-host coherence CXL—not even for homogeneous systems."

**4. Limited Protocol Diversity**
They test MESI, MOESI, MESIF—all SWMR protocols from the same family. The RCC (Release Consistency Coherence) discussion in Section IV-D2 is theoretical with only Figure 8 showing one example flow. No RCC performance numbers. GPUs using actual RCC would stress the system very differently than CPUs.

**5. Missing Scalability Analysis**
The compound state space is a Cartesian product of local × global states. For MESI×MESI, that's manageable. What happens with more complex protocols? They don't quantify:
- State machine complexity growth
- Transient state explosion
- Synthesis time for the generator tool

---

## Q4: What the Authors Didn't Tell You

**1. The Directory Bottleneck Problem**
Section VI-C1 admits CXL uses "blocking transient states at the directory" preventing pipelining. This is buried in the performance analysis but is actually a fundamental scalability issue. With many hosts contending for the same cache lines, the CXL directory becomes a serialization bottleneck. The paper doesn't explore:
- Directory associativity and eviction policies
- What happens when the directory runs out of tracking entries
- Back-pressure effects when many hosts simultaneously request different lines

**2. The Inclusion Requirement's Cost**
Section IV-B states: "the CXL cache must remain inclusive of all remote data cached in the host caches." This is a significant design constraint with unstated costs:
- The CXL cache must be sized ≥ sum of all host caches touching CXL memory
- Every host LLC eviction might trigger a CXL cache operation
- For RCC protocols (Section IV-D2, footnote 5), they handwave that "RCC restores inclusion at each release/acquire"—but what's the overhead of these synchronization points?

**3. The Conflict Resolution Overhead**
Figure 2's three scenarios show the `BIConflict`/`BIConflictAck` handshake. This adds extra round trips whenever there's ambiguity. The paper never quantifies:
- How often do conflicts actually occur in practice?
- What's the latency penalty when they do?
- Does conflict frequency increase with more hosts?

**4. The Memory Barrier Story is Incomplete**
Section IV-D3 claims barriers are handled "indirectly through the coherence messages." For TSO, fine—stores are ordered by the protocol. But for weak MCMs, they say cores "translate them into existing cache maintenance events (flush, invalidate) or specific coherence messages." This glosses over significant complexity:
- Who decides which barriers need global propagation vs local enforcement?
- What about fence instructions that don't map cleanly to coherence messages?
- The interaction between CPU pipeline ordering and coherence ordering isn't addressed

**5. The Synthesis Tool is a Black Box**
Section V mentions a "generator tool" based on Protogen [66] that takes SSP specifications and outputs SLICC code. But:
- What are the limitations of the SSP input language?
- How does it handle protocols not designed for SSP representation?
- The paper admits it "does not support separate instruction and data caches" (footnote 7)—what other limitations exist?

**6. The Relaxed Consistency Performance Story**
Figure 9 shows ARM MCM is 22-39% faster than TSO MCM. But this conflates two effects:
- Memory ordering relaxations allowing more parallelism
- Fewer fence instructions in the compiled code

They use gem5's `needsTSO` flag to isolate MCM effects, but this doesn't capture the full picture of how real ARM vs x86 code would behave, since the flag only affects the core model, not the actual instruction mix.

**7. What About Writes to the Same Line from Different Hosts?**
Figure 6b shows a write invalidation flow, but the common case of ping-ponging writes (false sharing or producer-consumer) between hosts isn't deeply analyzed. The 2.9× increase in high-latency accesses (Section VI-C1) for stores/RMWs hints at this problem but doesn't break down the root causes.

**8. The "Non-Intrusive" Claim Has Asterisks**
Section IV-D4 claims C³ "can be integrated into existing CXL implementations with minimal effort" and mentions Intel SPR/EMR platforms. But then admits: "only the controller logic in the CXL CHA needs to be extended with C³'s stateful coherence logic." That's not "minimal"—that's asking Intel to modify their proprietary cache home agent silicon. The practical deployment path is unclear.