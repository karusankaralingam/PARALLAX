Title: Magellan: C3: CXL Coherence Controllers for Heterogeneous Architectures(HPCA 2026)

Paper Review - https://pages.cs.wisc.edu/~karu/ArchAlphaZero/LLM_vs_Human/hpca-pdf/1029980_C3%20%20%20CXL%20Coherence%20Controllers%20for%20Heterogeneous%20Architectures.pdf

Nishant Aggarwal

1. Whiteboard explanation — You're explaining this paper's mechanism to a smart colleague who hasn't read it. Walk through what they built and how it works.

The industry is moving towards these heterogeneous systems with disaggregated / shared memory pools. The access to this shared memory pool is enabled by a high-speed fabric. CXL 3.0 is the physical standard for the same which enables high-speed, byte-addressable access to shared remote memory pools. This shift allows a diverse array of compute units, such as x86 CPUs, Arm processors, and GPUs, to interact with the same disaggregated memory as if it were a separate NUMA node.

However, the key gap is that even though CXL provides a coherent memory abstraction, it does not provide a systematic way to reconcile the semantic mismatch between different host coherence protocols and CXL’s own MESI-like protocol. A naive attempt to unify host and CXL coherence protocol would result into an explosion of transient states and complex race condition due to high fabric latency and message reordering. Similar gap exists for establishing a global memory consistency model as well. Existing theoritical frameworks like Compound memory model are too abstract and fail to provide the implementation aware rules needed to bridge the memory models. Furthermore, previous hardware synthesis approaches like HeteroGen are fundamentally static and break the plug and play nature of CXL systems. 

C3 solves it by introducing the CXL coherence controller that sits at the boundary of the host's local coherence domain and the global CXL coherence domain. Rather than fusing all the local protocols into a global coherence protocol, it retains each host’s existing coherence protocol and uses CXL for inter-host coherence. It is possible because the controller translates the messages semantically by re-expressing each cross-domain request as the core-level memory access (a load or a store) that would naturally trigger the equivalent coherence flow on the other side. 

To govern what gets translated and when, the authors derive two design rules from compound memory model theory. The first, Flow Delegation, requires any operation with globally visible effects to be forwarded to the CXL domain, and any CXL snoop affecting local state to be forwarded into the local domain, ensuring the CXL directory always maintains an accurate view of what each host is caching. The second, Atomicity, requires that no coherence effects be produced in the origin domain until the target domain signals completion, preventing causality violations that would otherwise arise from CXL's asynchronous, unordered interconnect. Together these rules determine which compound states are reachable and which are forbidden, dramatically reducing the state space the controller must handle.

C³'s hardware consists of two components. The CXL cache holds copies of remote CXL-mapped data and presents itself as an ordinary last-level cache to the host directory and as an ordinary cache controller to the CXL directory, allowing C3 to participate natively in both protocol worlds without modifying either. The C3 logic is a finite state machine whose states are the Cartesian product of both protocols' stable and transient states, tracking each cache line from both perspectives simultaneously to determine when cross-domain transactions are necessary.


2. What is the key insight that makes it work? (The "aha" — not what they did, but why it works)

The key insight is that you don't need to understand every protocol to bridge them correctly. Rather you just need to enforce two boundary conditions at the domain crossing, and correctness follows automatically from theory.
The first condition is that anything with globally visible effects must be forwarded globally, and anything global that affects local state must be forwarded locally. This keeps both sides honest about what data exists where. The second condition is that when you forward a request across the boundary, you freeze the origin side until the other side confirms completion. This makes the crossing appear atomic to both domains. These two conditions work they are the hardware realization of what compound memory model theory already proved is necessary and sufficient to compose heterogeneous memory models correctly.

3. What's the strongest aspect of the evaluation, and what's the weakest? (Methodology critique)

The strongest part of the evaluation is the correctness validation. Since this is fundamentally a coherence/consistency paper, the authors evaluate the right thing first which is whether C3 preserves memory-model behavior across heterogeneous coherence domains. They use formal FSM verification, herd7-generated litmus tests, Murφ-style checking, gem5 litmus tests, and negative controls where removing synchronization actually produces forbidden outcomes. That makes the correctness argument much stronger.

The weakest aspect of the evaluation is that it does not sufficiently stress many-host scalability. The paper motivates C3 using future heterogeneous multi-host CXL systems, but the main evaluation is limited to a two-cluster setup. Since CXL coherence overheads are likely to become more severe as the number of participating hosts grows, a broader scalability study would have made the performance and practicality claims stronger.

4. What did the authors not tell you? (Hidden assumptions, missing comparisons, unstated limitations)

The paper does not discuss failure or reconfiguration handling. C3 relies on forwarded transactions eventually completing before the origin domain can make progress. However, the paper does not explain what happens if a CXL device, host, or link fails, or if a device is removed while C3 is waiting in a transient state. Since CXL is motivated as a dynamic fabric where devices may be added or removed, the lack of timeout, rollback, recovery, or reconfiguration semantics is an unstated limitation.

5. What's the connection to ideas outside this paper's scope? (Cross-domain links, broader implications)

A broader implication is that C3 points toward a more systematic connection between software memory models and hardware coherence design. Today, programmers write synchronization using language-level rules, compilers map those rules to ISA fences and atomic instructions, and hardware coherence protocols must correctly enforce them. C3 operates at the hardware end of this chain, but its generated-controller approach suggests that future systems could more directly connect language-level memory-ordering requirements to the coherence mechanisms that implement them.