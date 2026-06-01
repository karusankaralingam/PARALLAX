# Paper Deconstruction: FACE

## Q1: Whiteboard Explanation

Alright, let me draw you the picture of what's actually happening here.

**The Problem:** You have a wafer-scale chip—imagine a dinner plate-sized piece of silicon with dozens of compute dies connected in a 2D mesh, each die packed with cores and HBM memory. You want to run LLM inference on this beast. The challenge is that LLM inference has two fundamentally different phases:

1. **Prefill**: You process the entire user prompt at once. This is compute-heavy—you're doing big matrix multiplications. Your expensive PE arrays are actually working hard.

2. **Decode**: You generate one token at a time, reading the KV cache. This is memory-bound—your compute units are mostly idle, twiddling their thumbs waiting for data from DRAM.

**The Prior Art (WSC-LLM):** The previous approach was to do "disaggregated scheduling"—carve up your wafer into separate prefill instances and decode instances. Prefill dies do prefill, decode dies do decode, and you ship KV caches between them. The problem? Your decode instances have compute utilization under 9% (Figure 4(b))—that's horrendous. Plus, you get messy topological constraints: some prefill instances aren't adjacent to decode instances, causing tail latency; you can't hit the ideal resource ratios; workloads get imbalanced.

**The FACE Trick:** Instead of segregating prefill and decode to different hardware, run them *simultaneously on the same instance*. The insight is that prefill is compute-bound and decode is memory-bound—so they're not actually competing for the same bottleneck resource. If you're clever about tiling the attention operator (which dominates execution time), you can interleave prefill attention tiles and decode attention tiles on the same cores.

The mechanism works in three stages:
1. **Configuration Space Exploration (CSE)**: Offline, you pre-compute a Look-Up Table (LUT) that says "for this many prefill tokens and this decode batch size, use these tile sizes to achieve overlap, and here's how long it takes." This is the playbook.
2. **Dynamic Adaptive Scheduling (DAS)**: At runtime, when requests arrive, you consult the LUT to decide which instance gets which request, aiming to maximize overlap and minimize per-iteration latency.
3. **Optimized Memory Management (OMM)**: Because D2D bandwidth exceeds DRAM bandwidth (a key architectural advantage), you can access KV cache stored on *neighboring* dies without D2D becoming the bottleneck. This expands your scheduling flexibility—a decode request doesn't have to execute on the same instance that did its prefill.

**The Architecture Co-Exploration:** On top of the scheduling, they also search the design space for optimal microarchitecture (how much SRAM per core? how many PEs? how much NoC?) and architecture (how big should each die be? how many HBM chiplets per die? how much D2D bandwidth?). They find that 0.75MB SRAM per core with high compute/NoC is best (Section VI-A1), and large dies with many HBM chiplets perform best (Section VI-A2).

---

## Q2: The Key Insight

The *real* innovation here is **recognizing that prefill and decode have complementary resource demands, and wafer-scale chips have the fine-grained control to exploit this.**

The authors explicitly state this in Section III-C and Figure 5(c): "we propose enabling parallel execution of the attention operations from both phases, thereby fundamentally eliminating prefill-decode interference and maximizing resource utilization."

This is subtle but important. Prior GPU-based systems like Sarathi [9] could overlap the *linear* operations (matrix multiplies for projections, FFN) between prefill and decode because those can be batched. But the *attention* operations remained serialized because they have different input shapes and are nonlinear—you can't just stack them into a bigger GEMM.

FACE exploits the fact that wafer-scale chips have **direct, fine-grained control over each core's controller and DMA engine via control I/O** (Section II-B2, "Advantages and Limitations"). This means the host can precisely orchestrate which cores work on which tiles of prefill attention versus decode attention, scheduling them to run concurrently. The tile sizes are chosen (via the LUT from CSE) so that prefill attention time ≈ decode attention time, achieving maximum overlap.

The secondary insight is that **D2D bandwidth >> DRAM bandwidth** on these wafer-scale chips enables a more flexible scheduling space. Equation (1) in Section IV-D1 formalizes this: a decode request can be scheduled on any instance within distance `D2D_BW / DRAM_BW` hops from its prefill instance without incurring congestion. This breaks the rigid coupling of disaggregated systems where prefill and decode for a request must happen on adjacent instances.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Solid Baseline Comparison:** They compare against WSC-LLM (the prior wafer-scale work) and vLLM (the dominant GPU serving system). The 3.68× E2E reduction vs. WSC-LLM and 7.23× vs. vLLM (Figures 11, 12) across multiple models and real-world Azure traces is substantial.

**2. Real-World Workloads:** They use Azure production traces for code and conversation datasets (Section V-A3) with realistic input/output length distributions (3-7437 input tokens, 3-1899 output tokens for code). This is far better than synthetic uniform traffic.

**3. Calibration with Real Hardware:** Section IV-F explicitly states they "refine the performance analysis models of WSC-LLM using real hardware measurement data" from "a representative NPU device [85]" and model DRAM based on "real HBM hardware [2]." This adds credibility to the simulation.

**4. Comprehensive Design Space Exploration:** Figure 13 shows microarchitecture results across 14 configurations, 3 die sizes, and 2 models. Figure 14 shows architecture results across 10 cases. This isn't cherry-picking one configuration.

**5. Honest Presentation of Decode Utilization Problem:** Figure 4(b) showing <9% compute utilization for decode instances is a damning critique of the prior art, and it motivates the work transparently.

### Weaknesses

**1. The Evaluator is Simulation, Not Silicon:** Despite the calibration claims, this is ultimately a modified version of WSC-LLM's analytical model and ASTRA-sim [76] for communication. There is **no real wafer-scale chip** being measured. Section IV-F admits the D2D model is "reused from WSC-LLM." The system overheads, thermal effects, and yield issues of a real wafer are absent.

**2. Throughput Gains are Modest Compared to Latency Gains:** While E2E latency improves 3.68× vs. WSC-LLM, throughput only improves 1.70× (Section V-B). For vLLM, it's 7.23× latency vs. 2.29× throughput. This asymmetry suggests the system is good at reducing *per-request* latency but doesn't scale request-level parallelism as dramatically. The paper doesn't deeply analyze why.

**3. The LUT Lookup Heuristic is Hand-Wavy:** Section IV-C2 describes the "match-and-evaluate strategy" for LUT queries as finding the entry with "minimum Euclidean distance." But workload parameters are discrete (decode batch size, token count). It's unclear how robust this interpolation is when the actual workload falls between explored configurations. No sensitivity analysis is provided.

**4. Fault Tolerance is Completely Absent:** This is a 12-inch wafer. Section I mentions Cerebras and Dojo as precedents, but neither the architecture template (Section II-B, Figure 2) nor the evaluation mentions **defect handling, redundancy, or routing around dead dies/cores**. For a chiplet-based wafer (which they claim has "improved manufacturing yield" over monolithic), this is a glaring omission. Real wafer-scale systems dedicate significant area and design complexity to this.

**5. Power and Thermal Analysis is Missing:** Wafer-scale chips are notoriously power-hungry and hard to cool. The paper reports no power numbers, no energy-per-token, and no discussion of thermal throttling. Section V-A1 mentions 7nm process and Table I lists compute GFLOPS, but actual power consumption is never evaluated.

**6. The "Fair" GPU Comparison is Questionable:** Section V-C compares against "six NVIDIA A100 nodes" connected via NVLink and InfiniBand. But the wafer-scale chip has all its dies on a single substrate with multi-TB/s D2D bandwidth. The A100 cluster has 8 GPUs per node × 6 nodes = 48 GPUs, connected by a heterogeneous hierarchy of NVLink (intra-node) and InfiniBand (inter-node). This isn't apples-to-apples—the communication topology is fundamentally different. The paper argues this is "fair" because of same 7nm process, but power, cost, and achievable bandwidth are not equivalent.

---

## Q4: What the Authors Didn't Tell You

**1. The Software Compilation Problem is Assumed Away.** The entire framework assumes a "magic compiler" can efficiently map the attention operator to the core array, partition into tiles, and orchestrate the dual-head pipeline (Section IV-E). The Operator Mapping Engine description is high-level ("sub-computation tasks," "nano-computation tasks," "core groups"), but there's no discussion of the complexity of finding optimal tile sizes or the overhead of the partitioning itself. Real NPU compilers struggle with this for *single* workloads; doing it for *interleaved* prefill and decode is harder.

**2. The CSE Phase has Non-Trivial Offline Cost.** Section IV-B describes iterating over "all feasible workload parameters," "all tile sizes," checking SRAM constraints, and measuring latency for each configuration. For a real system with many models, datasets, and instance sizes, this LUT could be enormous. They mention the LUT is "stored on the host, thus without any on-wafer storage consumption," but don't quantify its size or the time to generate it.

**3. The Instance Configuration Search is a Knapsack Problem.** Section IV-B1 says they "formulate the generation of ICL as a variant of the knapsack problem." Knapsack is NP-hard. They don't say how they solve it—exact? greedy? heuristic? For small wafer configurations this is fine, but scalability to larger wafers or more complex constraints is unclear.

**4. KV Cache Offloading (OMM) Has Unstated Overheads.** Section IV-D2 describes offloading KV cache to neighboring instances for "burst requests with exceptionally long input token length." This requires tracking which fragments of KV are stored where, updating "link weights" for multi-hop access, and coordinating reads during decode attention. The bookkeeping overhead and potential for fragmentation are not quantified.

**5. The "High D2D Bandwidth" Claim Needs Scrutiny.** The paper states D2D bandwidth ranges from 1.65 TB/s to 8.13 TB/s (Table I). But this is aggregate per-die bandwidth across all ports. The bidirectional ring algorithm for collectives (Section IV-E1) doesn't get full bisection bandwidth—it gets the per-hop bandwidth times the topology constraints. The effective bandwidth for a specific collective like all-reduce on a 2D mesh is significantly lower than the sum of all D2D links.

**6. They Don't Model Queuing Dynamics.** The Azure traces have bursty arrival patterns (2.57 req/s for code, 5.53 req/s for conversation). But the DAS algorithm (Section IV-C) processes requests one-by-one with a simple "smallest incremental latency" heuristic. There's no explicit queuing model, no analysis of how tail latency behaves under bursts, and no comparison to more sophisticated scheduling policies (e.g., fair queuing, SJF with preemption).

**7. The Microarchitecture Exploration Doesn't Cover Heterogeneous Cores.** They explore cores with varying SRAM/compute/NoC ratios, but all cores on a die are identical. Given that prefill and decode have different resource demands, a heterogeneous design (some cores optimized for compute-heavy prefill, others for memory-bound decode) might be superior. This is not explored.

**8. Scalability Beyond Single Wafer is Vague.** Section VI-B1 claims FACE "can naturally extend to multi-wafer systems," but only mentions that "when wafer-to-wafer bandwidth is limited... FACE automatically confines inter-instance workload balancing within each individual wafer." This isn't a design—it's a fallback. Real multi-wafer systems (like Cerebras clusters) need explicit cross-wafer scheduling, which FACE doesn't address.