# Paper Deconstruction: WSC-LLM

## Q1: Whiteboard Explanation

Alright, let me sketch this out for you.

**The Problem Setup:**
Imagine you have a wafer—a giant silicon pizza, 215mm × 215mm—covered with compute dies and DRAM chiplets, all connected with high-speed Die-to-Die (D2D) links. You want to run LLM inference on this beast. The challenge? LLM inference has two very different phases:

1. **Prefill**: Process all input tokens at once. This is *compute-bound*—you're doing massive matrix multiplies, and you want to crunch through them fast.
2. **Decode**: Generate one token at a time, iteratively. This is *memory-bound*—you're mostly reading KV cache from DRAM, and your bottleneck is memory bandwidth.

**The Core Tension (Figure 1):**
On a wafer, you have a fixed area budget. If you add more DRAM chiplets per die, you get:
- ✅ More memory capacity (store more KV cache, run more requests)
- ✅ Higher DRAM bandwidth (faster decoding)
- ❌ Fewer total dies on the wafer (less compute)
- ❌ Less D2D bandwidth (DRAM interfaces eat up the interconnect budget)

So there's a resource allocation problem the authors are trying to solve.

**What WSC-LLM Does (Figure 6):**

Think of it as a three-layer optimization stack:

1. **Central Scheduler (Section 4.2)**: Decides how to carve up the wafer into "prefill instances" and "decode instances." It searches for the optimal Tensor Parallelism (TP) size for each phase (Algorithm 1, page 7). It also decides *where* to place these instances on the 2D mesh—they use a "decode-centered" placement heuristic (Equation 1, Figure 7) to minimize KV cache transfer distances.

2. **Memory Scheduler (Section 4.4, Algorithm 2)**: Here's the clever trick. Because D2D bandwidth is so high (often exceeding DRAM bandwidth), you can treat DRAMs on *other* dies as if they were almost local. So when a prefill finishes and needs to hand off KV cache to a decode instance, you don't necessarily copy it—you can leave it where it is, or store it on dies *between* the prefill and decode instances, and just read it over D2D. This is how they claim to utilize the "wasted" memory in prefill instances (Figure 5b).

3. **Operator Execution Engine (Section 4.5)**: Standard two-level mapping: partition work across dies (TP Engine), then partition each die's work across its internal compute cores (Intra-Die Engine). They use a bidirectional ring for all-reduce (Figure 9a).

**The Sales Pitch:**
They compare against "Splitwise" (a GPU-cluster disaggregated serving system) and claim 3.12× improvement in E2E latency (Figure 11, Section 5.3).

---

## Q2: The Key Insight

The paper has **two distinct contributions**, one architectural and one algorithmic:

**Insight #1 (The Architecture/Memory Trick):**
On a wafer-scale chip with high D2D bandwidth, the traditional model of "each die manages its own DRAM" is suboptimal. Because D2D bandwidth can *exceed* local DRAM bandwidth (see Table 1: D2D is 1.5-2.5 TB/s, DRAM is 1-3 TB/s per die), you can treat the *entire wafer's DRAM* as a shared memory pool without paying a latency penalty that kills performance.

This enables their Memory Scheduler (Algorithm 2, page 8): after prefill, they don't always copy KV cache to the decode instance. Instead, they store it *along the shortest path* between the prefill and decode instances. The decode instance then reads it remotely. The key condition for this to work is stated in Section 4.4: *"in the absence of D2D link congestion, cross-die DRAM read and write operations are constrained only by DRAM bandwidth rather than D2D bandwidth."* This is the architectural bet they're making.

**Insight #2 (The Scheduling Observation):**
Prefill and decode phases have *different optimal TP configurations*. Figure 5(a) shows this clearly: for LLaMA3-70B, prefill benefits from higher TP (faster with more parallelism), but decode *degrades* with higher TP (communication overhead dominates). Prior disaggregated systems like Splitwise used fixed TP=8 for both. WSC-LLM searches for the phase-specific optimum (Algorithm 1, lines 7-13).

**The Real "Delta":**
The *mechanism* here is the cross-die memory scheduling (Algorithm 2). The *policy* innovation is the joint exploration of TP size, instance count, and placement (Algorithm 1 + Equation 1). Neither idea is earth-shattering alone, but the combination—applied to a wafer-scale topology with its unique bandwidth characteristics—is the contribution.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Reasonable Baseline Choice:** They compare against Splitwise on a 6-node A100 cluster (48 GPUs, 3840 GB total DRAM, 14,976 TFLOPS). This is a credible, recent (2023) disaggregated serving system, not a straw man. The wafer-scale chip has *less* total compute (14,100 vs 14,976 TFLOPS) and *less* memory (3,456 vs 3,840 GB), so the comparison isn't rigged in their favor on raw resources (Section 5.3, page 10-11).

2. **Real-World Traces:** They use Azure production traces (Section 5.1.4) with realistic input/output distributions, not synthetic workloads. The conversation dataset has ~1155 avg prompt tokens and ~212 decode tokens; code dataset has ~2048 prompt and ~28 decode tokens. This matters for evaluating scheduling.

3. **Ablation Studies Done Correctly:** Figure 12 (Section 5.4) isolates the contributions of the Central Scheduler (no-Central) and Memory Scheduler (no-Memory). The results are instructive: Memory Scheduler contributes *more* for larger models (LLaMA3-70B, GPT-175B), while Central Scheduler matters more for smaller models (LLaMA2-7B). This is a good sanity check.

4. **Scalability Analysis:** Section 6.2 and Figure 14 test a 2×2 wafer array against a 24-node GPU cluster, showing the framework scales to multi-wafer configurations.

**Weaknesses:**

1. **The Baseline Hardware is Hypothetical:** The wafer-scale chip itself is a *simulated* architecture based on a "Dojo-style" template (Section 5.1.1). The 16×16 core array, 1 GHz frequency, 6 TB/s D2D bandwidth—none of this has been validated on silicon. They cite ASTRA-sim for their evaluator (Section 4.6), but the accuracy of modeling a *wafer-scale* system with this tool is unvalidated. The paper acknowledges they use a DNN to *fit* the mapping lookup table (page 9), introducing another layer of modeling uncertainty.

2. **Splitwise-Wafer Comparison is Misleading:** They apply Splitwise's scheduling *unchanged* to the wafer-scale chip (SW-Wafer in Figure 11). But Splitwise was designed for GPU clusters with all-to-all NVLink topology, not a 2D mesh. Of *course* it performs poorly. A fairer comparison would be Splitwise *adapted* for 2D mesh, or a ring-based communication pattern. The 4.81× improvement over SW-Wafer is measuring how bad Splitwise is on the wrong topology, not how good WSC-LLM is.

3. **Missing Power and Area Analysis:** There is *zero* discussion of power consumption or area costs. Table 1 gives DRAM capacity/bandwidth per die, but doesn't quantify the area trade-off. For a paper about architecture exploration, this is a significant omission. They claim to explore "trade-offs" (Figure 4, Section 3.1) but never put numbers on the area cost of adding DRAM chiplets.

4. **Cherry-Picked Configuration Wins:** Figure 10 shows Case 3 (54 dies, 64GB/die DRAM, 2 TB/s D2D) wins across most workloads. But the design space has only 4 configurations (Table 1). This is a coarse sweep, not a rigorous DSE. The claim "moderate DRAM capacity delivers the best LLM service quality" (Section 5.2) is based on testing 4 points on a design curve.

5. **Simulation Intervals Not Specified:** The paper doesn't state how long the simulated serving window is. The Azure traces are described as "one-hour serving" (Section 5.1.4), but do they simulate the full hour, or a representative segment? This matters for steady-state behavior and queue dynamics.

---

## Q4: What the Authors Didn't Tell You

1. **The D2D Bandwidth Assumption is Optimistic:** The entire Memory Scheduler hinges on D2D bandwidth exceeding or matching DRAM bandwidth (Section 4.4). Table 1 shows D2D ranges from 1.5-2.5 TB/s. But this is *aggregate* bandwidth across all directions. In a real 2D mesh under tensor parallelism, multiple dies are communicating simultaneously (Figure 9a shows 4 concurrent data movements in all-reduce). The *effective* per-link bandwidth under contention is not analyzed. If D2D links congest, the entire "treat remote DRAM as local" assumption breaks down. The hyperparameter α in Section 4.2.2 (page 7) is a hand-wavy acknowledgment of this, but its value is never specified or validated.

2. **Yield and Defect Tolerance are Ignored:** Wafer-scale chips have notoriously low yield. Cerebras WSE-2 uses redundant compute tiles and interconnects to handle defects. This paper assumes a pristine 6×9 or 7×9 die array (Table 1) with no discussion of fault tolerance. For a paper claiming to guide "wafer-scale architecture design," this is a glaring omission.

3. **The "3.12× Improvement" Needs Qualification:** Section 5.3 reports 3.12× E2E latency improvement *averaged* across 4 models and 2 datasets. Looking at Figure 11, the improvement varies wildly: LLaMA2-7B on conversation shows ~2× (eyeballing the bar chart), while GPT-175B on code shows closer to 4×. The geometric mean vs. arithmetic mean choice matters, and they don't specify which they used. Also, "E2E latency" includes queueing time, which is scheduling-dependent. A system with higher throughput naturally has lower queue delays. The *per-request execution time* improvement would be more instructive.

4. **No Discussion of First-Token Latency (TTFT):** For interactive LLM serving, Time-to-First-Token is often the critical SLA metric. The paper reports only E2E latency and TPS. For a disaggregated system where prefill happens on separate instances, TTFT could be *worse* if prefill instances are undersized or if there's queueing. This metric is standard in the serving literature (Splitwise reports it prominently) but absent here.

5. **The Memory Scheduler Introduces Fragmentation:** Algorithm 2 allocates KV cache across potentially many DRAMs along the path between prefill and decode instances. When requests complete, this creates scattered free memory. The paper doesn't discuss memory compaction or the impact of fragmentation on effective memory utilization over long-running workloads.

6. **Comparison with Real Wafer-Scale Systems is Absent:** Cerebras WSE-2 and Tesla Dojo are mentioned in the introduction (Section 2.3, refs [50, 73]) but never compared against. A simulation of LLM inference on a WSE-2-like architecture would be informative. The authors instead define their own hypothetical template, which conveniently has parameters they can tune for their experiments.