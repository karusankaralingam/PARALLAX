# Study B — Rich Directive
**Paper:** 1029980 C3   CXL Coherence Controllers for Heterogeneous Architectures  
**Model:** claude-opus-4-5  
**Generated:** 2026-04-21 11:33

---

# Comprehension Report: C³: CXL Coherence Controllers for Heterogeneous Architectures

## Q1: Whiteboard Explanation

Imagine you have two different computers—one Intel x86 machine and one ARM machine—that need to share the same memory pool through CXL (Compute Express Link). The problem is that each machine speaks a different "language" for keeping their caches consistent with memory. Intel uses one protocol, ARM uses another variant, and CXL has its own protocol. When these machines try to access the same memory location simultaneously, chaos can ensue because they have different rules about what operations are allowed and in what order.

C³ is essentially a translator that sits between each host computer and the shared CXL memory. Here's how it works:

**The Architecture:** Each host has its local cache hierarchy managed by its native coherence protocol (say MESI for Intel). C³ sits at the boundary, appearing as a directory controller to the local caches and as a cache controller to the CXL network. It maintains a "CXL cache" that holds copies of all data this host has cached from remote CXL memory.

**The Two Key Rules:**

1. **Flow Delegation**: Any operation that affects other hosts must be forwarded through CXL. If Host A wants to write to shared data, it can't just do so locally—C³ must tell the CXL directory, which then invalidates copies on Host B. Conversely, when CXL sends an invalidation snoop, C³ must propagate it to local caches.

2. **Atomicity**: When C³ forwards a request across domains, it must block all other operations to that cache line until it gets confirmation. This prevents race conditions where Host A thinks it wrote successfully but Host B never saw the invalidation.

**State Compounding:** C³ tracks cache line states in both protocols simultaneously as a pair (LocalState, CXLState). For example, (M, M) means the local cache has the line in Modified state AND the CXL cache has it in Modified state. This compound state determines what translation actions are needed.

**Why This Works:** By always delegating cross-domain effects and enforcing atomicity at boundaries, C³ realizes what's called a "compound memory model"—each host sees exactly the memory ordering guarantees its native architecture promises, even though other heterogeneous hosts are accessing the same memory.

## Q2: The Key Insight

The central insight is that **heterogeneous cache coherence protocols can be correctly composed by treating cross-domain operations as atomic nested transactions, without modifying the participating protocols themselves**.

This is non-obvious for several reasons. The naive approach would be to either (1) create a universal coherence protocol that all devices speak, requiring massive redesign, or (2) merge the state machines of different protocols into a single complex automaton, which prior work like HeteroGen attempted but which doesn't scale to CXL's dynamic topologies where hosts can be added/removed at runtime.

The key realization is that you don't need protocol unification—you need protocol isolation with controlled interaction points. C³ exploits the fact that loads and stores are universal primitives across all coherence protocols. When a local GetM request arrives, C³ doesn't try to directly translate it to CXL's MemRd,A; instead, it conceptually "simulates" what a store operation would do in the CXL domain. This simulation is pre-computed at synthesis time into translation tables.

The atomicity rule is the critical correctness enabler. Without it, you get race conditions where the CXL directory grants write permission to Host 2 before Host 1 has actually invalidated its local caches (Figure 4 in the paper shows exactly this failure mode). By blocking the origin domain until the target domain confirms completion, C³ ensures that all observers see a consistent global ordering.

The compound memory model foundation is important: it guarantees that weaker memory models (ARM) don't strengthen to TSO just because they're sharing memory with x86, and vice versa. Each architecture keeps its native semantics.

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Rigorous Correctness Validation**: The authors employ both formal verification (Murφ model checking via HeteroGen's methodology) and extensive litmus testing (100,000 runs per test, covering IRIW, MP, 2+2W, etc.). The litmus tests cover all combinations of MCMs and protocols, and critically, they include negative controls where removing synchronization produces expected forbidden outcomes. This dual approach is appropriate for cache coherence verification.

2. **Generality Demonstration**: Testing across three benchmark suites (PARSEC, Splash-4, Phoenix) with 33 applications, multiple protocol combinations (MESI-CXL-MESI, MESI-CXL-MOESI, MESI-CXL-MESIF), and multiple MCM combinations (ARM-ARM, TSO-TSO, ARM-TSO) demonstrates the approach isn't narrowly tuned.

3. **Honest Performance Analysis**: The 4-29% overhead (5.5% average) is reasonable, and the authors correctly identify that CXL's protocol design (6 message delays vs. MESI's 3, blocking transient states preventing pipelining) causes the slowdown—not C³ itself. The miss latency breakdown (Figure 11) provides convincing evidence.

**Weaknesses:**

1. **Worst-Case-Only Evaluation**: All data resides in CXL memory, which the authors acknowledge is "worst-case" but which obscures how C³ performs in realistic hybrid configurations. The 5.5% average overhead might be much lower with mixed local/remote data, or it might be similar due to Amdahl's law effects. This is a significant gap.

2. **Simulation Limitations**: Using gem5's Garnet network model instead of PCIe-based CXL simulation means transport-layer effects are abstracted away. While the authors argue this isolates protocol effects, real CXL systems have complex PCIe credit flow, TLP ordering, and flow control that could interact with coherence traffic patterns. The 70ns link latency chosen empirically to match 400ns round-trip seems coarse.

3. **Missing RCC Evaluation**: Despite dedicating Section IV-D2 to explaining how C³ handles Release Consistency Coherence (GPUs), there's no experimental evaluation with RCC. The paper claims generality to GPUs but only demonstrates CPU protocols.

4. **Scalability Questions Unanswered**: The evaluation uses 2 clusters with 8-30 cores total. CXL 3.0's multi-host coherence targets larger scales. How does the CXL directory's blocking transient states affect systems with 8 or 16 hosts? The "convoy effect" they identify for hot cache lines would likely worsen with more hosts.

5. **No Hardware Synthesis Data**: The paper claims C³-logic is "purely combinational and sequential logic, incurring minimal area and power overhead" but provides no synthesis numbers. Comparing FSM complexity (number of states, transitions) to baseline protocols would strengthen this claim.

6. **Inclusion Requirement Overhead**: The CXL cache must be inclusive of all CXL data cached locally. For systems with large L3 caches (tens of MB), this could require substantial dedicated storage. The paper punts on this by suggesting integration with LLC, but doesn't quantify the cost.

## Q4: What the Authors Didn't Tell You

**Implementation Reality vs. Paper Claims:**

The paper positions C³ as requiring "no modification to existing caches or directory controllers." This is technically true for the protocol logic, but there's significant unstated integration complexity. The CXL cache needs address space mapping to distinguish local vs. remote memory regions—this requires OS involvement, BIOS configuration, or memory controller modifications not discussed. Intel's actual CHA (Caching and Home Agent) mentioned in Section IV-D4 is deeply integrated with system agent functionality; C³ isn't a drop-in replacement.

**The Protocol Generator is the Real Contribution:**

The paper buries the lede. The generator tool (reference [47]) that takes SSP specifications and produces correct SLICC code is arguably more valuable than C³ itself. This tool embeds the design rules and could generate bridges for protocols beyond CXL. The paper treats it as methodology (Section V) rather than a primary contribution.

**CXL Protocol Limitations Are Fundamental:**

The 6-message-delay vs. 3-message-delay comparison reveals that CXL's protocol is inherently slower than traditional MESI for cross-host coherence. This isn't a C³ artifact—it's baked into CXL 3.0's design choices for handling network reordering and dynamic topologies. Anyone building multi-host CXL systems will face this penalty regardless of how they handle heterogeneity.

**The Compound Memory Model Guarantee is Weaker Than It Sounds:**

Compound memory models preserve local MCM semantics, but inter-architecture ordering is only guaranteed at synchronization points. If an ARM thread releases a lock and an x86 thread acquires it, the ordering is correct. But if programmers assume stronger cross-architecture guarantees (e.g., that x86's TSO ordering propagates to ARM observers), they'll have subtle bugs. The paper doesn't discuss the programming model implications for applications spanning heterogeneous hosts.

**Missing Discussion of CXL.cache:**

The paper focuses on CXL.mem, but CXL.cache exists for device-to-host coherence (accelerators caching host memory). C³'s principles should apply, but the asymmetry of CXL.cache (devices snoop host memory, not vice versa) creates different challenges. This is a notable scope limitation.

**Practical Deployment Barriers:**

No commercially available hardware supports CXL 3.0 multi-host coherence. The paper is entirely simulation-based. When real hardware emerges, implementation details (how different vendors handle BIConflict timing, DCOH implementations, etc.) could invalidate gem5-derived performance numbers. The authors acknowledge this implicitly by never claiming real-system validation.

**The 22-39% TSO-on-ARM Overhead is Suspicious:**

The claim that enforcing TSO on ARM cores causes 22-39% slowdown aligns with binary translation work, but C³ isn't doing binary translation—it's simulating TSO via gem5's `needsTSO` flag. This flag affects core-internal ordering, not coherence. The coherence protocol should be identical regardless of local MCM. The slowdown is real but misattributed; it comes from core pipeline serialization, not C³.