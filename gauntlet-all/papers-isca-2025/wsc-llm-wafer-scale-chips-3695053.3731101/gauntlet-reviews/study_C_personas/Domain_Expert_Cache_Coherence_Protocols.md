# Paper Deconstruction: WSC-LLM

## Q1: Whiteboard Explanation

Alright, let me sketch this out for you like we're at a coffee shop with a napkin.

**The Problem Setup:**
Imagine you have a massive silicon wafer—about 215mm × 215mm—and you want to build a custom chip for running LLM inference. You can pack this wafer with compute dies (think of them as little GPUs) connected by super-fast Die-to-Die (D2D) links in a 2D mesh topology. Each die also gets some DRAM chiplets stacked nearby.

Here's the fundamental tension: *the wafer area is fixed*. Every DRAM chiplet you add:
1. Eats wafer area → fewer compute dies
2. Steals D2D interface pins → less inter-die bandwidth

So you're playing a resource allocation game with three currencies: compute, memory, and communication bandwidth. The paper asks: **how do you balance these resources AND schedule LLM workloads to maximize throughput?**

**The LLM Workload Wrinkle:**
LLM inference has two phases with radically different characteristics:
- **Prefill**: Process all input tokens at once. Compute-bound. Loves parallelism.
- **Decode**: Generate tokens one-by-one. Memory-bandwidth-bound. The KV cache dominates.

Existing disaggregated systems (like Splitwise) separate these onto different machines, but they:
1. Pick TP (tensor parallelism) sizes by gut feeling
2. Don't think about 2D-mesh topology when transferring KV cache
3. Waste memory—prefill instances have tons of idle DRAM

**WSC-LLM's Approach:**
The authors build a co-exploration framework that:
1. **Searches for optimal resource partitioning**: How many dies per prefill instance? Per decode instance? What TP strategy for each?
2. **Places instances smartly on the 2D mesh**: Put decode instances in the center, prefill on the periphery, minimizing KV cache transfer hops
3. **Exploits cross-die memory**: Since D2D bandwidth > DRAM bandwidth, you can store KV cache on *any* die's DRAM without penalty—effectively pooling memory across the wafer

The key insight is that wafer-scale D2D bandwidth is so high that memory access latency is dominated by DRAM, not the interconnect. So you can treat the entire wafer's memory as one big pool for KV cache.

---

## Q2: The Key Insight

**The Real Delta:**
The paper's actual contribution isn't a new protocol or a novel algorithm—it's the *systematic co-exploration of architecture and scheduling* for wafer-scale LLM inference. But let me isolate the three mechanisms that matter:

**Insight #1: Memory Pooling via D2D Bandwidth Dominance (Section 4.4)**

This is the magic trick. The authors observe that D2D bandwidth (2 TB/s in their default config) exceeds DRAM bandwidth (also ~2 TB/s per die). This means:

> *"In the absence of D2D link congestion, cross-die DRAM read and write operations are constrained only by DRAM bandwidth rather than D2D bandwidth."* (Section 4.4)

The implication is profound: you can store a request's KV cache on *any* die's DRAM—not just the decode instance's local memory—without paying a latency penalty. Algorithm 2 exploits this by dynamically allocating KV cache to nearby dies along the prefill→decode transfer path, effectively turning idle prefill memory into decode memory. This is why Figure 5(b) shows such poor memory utilization in existing systems (prefill instances wasting 60%+ of DRAM) and why Figure 13(b) shows WSC-LLM achieving dramatically higher utilization.

**Insight #2: Topology-Aware Instance Placement (Section 4.2.2)**

The "decoding-centered placement" strategy is simple but effective: place decode instances in the center of the mesh, prefill instances on the periphery. This minimizes the total hop count for KV cache transfers (Equation 1) and avoids link congestion by preventing overlapping transfer paths. Figure 7 shows this reducing TransferCost from (8α + 16) hops to 16 hops.

**Insight #3: Phase-Specific TP Optimization (Section 4.2.1, Algorithm 1)**

The paper formalizes what others do by intuition: search over instance sizes and TP configurations independently for prefill and decode, then balance the ratio based on throughput matching. The key constraint is that instances must be rectangular (for mesh routing), which limits the search space.

**What This Is NOT:**
This is not a new parallelism strategy (TP/PP are standard), not a new memory management technique (it's just exploiting the bandwidth hierarchy), and not a new scheduling algorithm (FCFS with continuous batching is textbook). The contribution is *applying* these ideas systematically to wafer-scale chips with their unique bandwidth characteristics.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

**1. Comprehensive Design Space Exploration (Table 1, Section 5.2)**
The authors actually vary the architectural parameters meaningfully—4 configurations with different DRAM/D2D bandwidth tradeoffs. Figure 10 shows that "Case 3" (moderate DRAM, balanced bandwidth) consistently wins. This provides actionable insight: *you want balance, not extremes.*

**2. Real Workload Traces (Section 5.1.4)**
They use Azure production traces with actual arrival patterns, not synthetic uniform distributions. The code vs. conversation dataset comparison (Figure 10-11) reveals that workload characteristics matter—conversation (longer decode phases) benefits more from memory optimization.

**3. Honest Ablation Study (Section 5.4, Figure 12)**
Figure 12 cleanly separates the contributions: Memory Scheduler dominates for larger models (30B+), Central Scheduler matters more for smaller models (7B). The authors explicitly state: *"The Memory Scheduler in WSC-LLM plays a more critical role than the Central Scheduler"* (Section 5.4). This is refreshingly honest—they're telling you which optimization actually matters.

**4. Scalability Analysis (Section 6.2, Figure 14)**
They scale to 4 wafers (2×2) and test LLaMA3-405B. Importantly, they test with *realistic* wafer-to-wafer bandwidth (1.8 TB/s and 400 GB/s), acknowledging that W2W is much slower than D2D.

### Weaknesses:

**1. Simulation-Only Evaluation**
The entire evaluation is simulated using a modified ASTRA-sim. Section 4.6 admits they use a DNN to *estimate* intra-die mapping results for configurations not explicitly simulated:

> *"Subsequently, these results are used to train a DNN to model the relationship between input metrics and outcomes."*

This introduces unknown error, and they cite prior work claiming the error is "controllable" without quantifying it for their specific setup.

**2. No Contention Modeling for D2D Links**
The core assumption—that D2D bandwidth > DRAM bandwidth enables "free" cross-die memory access—only holds *"in the absence of D2D link congestion"* (Section 4.4). But with multiple requests competing, multiple KV transfers happening simultaneously, and All-Reduce traffic during TP, link contention is inevitable. The paper doesn't model this rigorously.

**3. Strawman Comparison (Section 5.3)**
"Splitwise-Wafer" is a strawman: Splitwise's scheduling strategy applied naively to wafer topology. The fair comparison would be against a *topology-aware* version of Splitwise, or against other wafer-scale schedulers. The 3.12× improvement over SW-GPU is impressive, but the hardware is also dramatically different (wafer-scale vs. discrete GPUs).

**4. Fixed Compute Die Configuration**
The authors fix the compute die at 16×16 cores, Dojo-style, 7nm (Section 5.1.1). This is one point in a huge design space. They explore DRAM/D2D bandwidth tradeoffs but don't explore core architecture, SRAM sizing, or PE array configurations. The "co-exploration" is really just memory/bandwidth exploration.

**5. Limited Model Diversity**
All models are decoder-only transformers (LLaMA, GPT). MoE models (like Mixtral), encoder-decoder models, or models with different attention mechanisms (sliding window, etc.) are absent.

**6. No Power/Area/Cost Analysis**
For a hardware architecture paper, there's no power modeling, no thermal analysis (critical for wafer-scale!), and no cost estimation. The "optimal" architecture (Case 3) might be terrible from a power/cost perspective.

---

## Q4: What the Authors Didn't Tell You

**1. The D2D Bandwidth Assumption is Heroic**
The paper assumes 2-2.5 TB/s D2D bandwidth in a 2D mesh. Section 2.3 mentions they avoid "constraints on interconnect distances and the need to maintain signal integrity" as reasons for mesh topology. But they don't discuss what happens when you route traffic multiple hops across the mesh. A 6-hop KV cache transfer doesn't get 2 TB/s—it gets 2 TB/s *per link*, but the end-to-end transfer competes with other traffic on shared links. The "overlapped by DRAM access" claim (Section 4.4) is only true if there's no contention.

**2. The Lookup Table Approach Hides Latency Variance**
Section 4.6 describes pre-computing a "mapping lookup table" using a DNN to estimate latencies. But LLM inference has high *variance*—different sequence lengths, different batch compositions, different cache hit rates. A lookup table gives you expected latency; it doesn't capture tail latency or the impact of scheduling decisions on latency distributions. For SLO-sensitive deployments, this matters.

**3. Rectangular Instance Constraint is Significant**
Algorithm 1 line 4 notes that instances "must be a rectangular shape" for mesh routing. This is a major constraint! If you have 63 dies and want to use them all, you can't—you're stuck with 56 (7×8) or 64 (8×8). The paper doesn't quantify how much performance is lost to this fragmentation.

**4. Memory Scheduler Doesn't Handle Eviction**
Algorithm 2 allocates KV cache greedily to available DRAM. But what happens when memory fills up? Line 6 just says "break"—the scheduler tells the Central Scheduler to stop prefilling. There's no eviction policy, no priority system, no handling of long-running requests that hog memory. In a real system with bursty arrivals, this could cause head-of-line blocking.

**5. The "3.12× Improvement" Needs Context**
This is comparing a wafer-scale chip with 6 TB/s total D2D bandwidth to a GPU cluster with 400 GB/s inter-node bandwidth. The interconnect is 15× faster! Of course it's faster. The more interesting comparison would be: *given equal silicon area and power budget, how does wafer-scale compare to multi-chip packages like NVIDIA's NVL72?*

**6. No Discussion of Fault Tolerance**
Wafer-scale chips have yield issues—some dies will be defective. The paper mentions "high-yield" approaches (Section 2.3) but doesn't model partial failures, doesn't discuss redundancy, and doesn't show how scheduling adapts to dead dies.

**7. The "Moderate DRAM" Sweet Spot May Not Generalize**
Case 3 wins in their evaluation, but this depends heavily on their specific workload mix. If you're running a single massive model (like 405B) that barely fits, you want maximum memory (Case 4). If you're running many small models, you might want maximum compute (Case 1). The "balance is best" conclusion is workload-dependent.