# Paper Deconstruction: FACE Framework for Wafer-Scale LLM Inference

## Q1: Whiteboard Explanation

Imagine you're running a restaurant where every customer gets a two-course meal: an appetizer (prefill phase—processing their entire input prompt) and then many small dessert bites served one at a time (decode phase—generating tokens one by one). The appetizer is labor-intensive in the kitchen (compute-bound), while the dessert bites require constant trips to the walk-in freezer (memory-bound).

Traditional GPU-based systems handle this like having separate "appetizer stations" and "dessert stations." But this creates problems: appetizer chefs stand idle while dessert servers scramble, and transferring the order history (KV cache) between stations is slow.

**Wafer-scale chips** are like building an entire restaurant on one giant floor plate—tens of compute dies connected with ultra-fast "hallways" (D2D interconnects). The FACE framework's insight is: *on this giant floor plate, you can have chefs simultaneously preparing appetizers AND dessert bites at the same station*, because you have fine-grained control over every cook's movements and the hallways are wide enough to shuffle ingredients without traffic jams.

The core mechanism works in three layers:

1. **Configuration Space Exploration (CSE)**: Before opening the restaurant, FACE pre-computes a "recipe book" (look-up table) that tells you: "If you have X appetizer orders and Y dessert batches, here's exactly how to tile the work across cores so both finish at the same time." The key is tuning the tile sizes for attention computation so prefill and decode overlap completely (Section IV-B, Fig. 7).

2. **Dynamic Adaptive Scheduling (DAS)**: At runtime, new orders come in unpredictably. The prefill engine assigns them to the least-busy station using a sorted queue (Section IV-C). The decode engine is smarter—it doesn't blindly keep a customer's dessert at their original station; it calculates which station would see the *smallest increase in per-iteration latency* if it took this order, then routes accordingly.

3. **Optimized Memory Management (OMM)**: The "schedulable instance range" is determined by Equation 1: a decode request can be routed to any instance within `D2D_Bandwidth / DRAM_Bandwidth` hops, because within that range, cross-die memory access is bottlenecked by DRAM, not the interconnect (Section IV-D1, Fig. 9). This expands where you can place work without creating hallway congestion.

## Q2: The Key Insight

**The real contribution is not "using wafer-scale chips for LLMs"—it's the co-design insight that wafer-scale chips' fine-grained control enables *fully overlapped* prefill-decode attention execution within a single instance, which is impossible on GPUs.**

The paper explicitly contrasts this with prior work in Figure 5. Existing unified scheduling (Fig. 5a) serializes prefill and decode, causing interference. Optimized unified scheduling (Fig. 5b, e.g., Sarathi's piggybacking) can overlap *linear* operators but still serializes *attention*—and attention dominates latency for long sequences (Section III-C, citing [37]). WSC-LLM's disaggregated approach (Section II-C) avoids interference by physically separating phases, but this creates four concrete problems illustrated in Figure 4(a): placement imbalance causing tail latency, suboptimal resource ratios, forced non-optimal instance sizes, and imbalanced decode workloads.

**The "magic trick" is in the LUT construction (Algorithm in Fig. 7, middle).** For each workload configuration, FACE explores pairs of `(p_tile, d_tile)`—the tile sizes for prefill and decode attention—and records the configuration where `max(p_time, d_time)` is minimized. This means both phases finish simultaneously, achieving full overlap. The SRAM constraint check (line 6) ensures the tiles actually fit in core memory.

**Why this only works on wafer-scale chips:** The paper emphasizes that the "fine-grained control" advantage (Section II-B2) allows the host to "directly manage the controller and DMA engine of each core to precisely regulate the sizes of prefill and decode tiles" (Section III-C). This PE-level execution control isn't available on GPU SMs, which are designed for SIMT execution of homogeneous warps, not simultaneous execution of differently-tiled attention kernels.

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Design Space Exploration (Section VI-A):**
The microarchitecture exploration in Figure 13 is genuinely useful. Testing 14 configurations across three die sizes and two models produces a non-obvious insight: 0.75MB SRAM with high compute/NoC is optimal (Section VI-A1). The paper explains *why*—moderate SRAM maintains core count while allowing denser compute integration. This is actionable guidance, not hand-waving.

**2. Architectural exploration reveals real trade-offs (Figure 14):**
The comparison between cases 5 and 6 (Section VI-A2) demonstrates intellectual honesty. More HBM doesn't always win—case 6's higher DRAM bandwidth is offset by lower D2D bandwidth degrading communication. This validates their claim that "coordinated provisioning of different hardware resources" matters.

**3. Fair baseline selection:**
Using WSC-LLM's exact hardware configuration (54 dies, 261.12 TFLOPS/die, 2 TB/s D2D/DRAM) as W-Arch enables direct comparison. The 6-node A100 cluster baseline uses the same 7nm process node (Section V-A3), addressing die-area fairness.

**4. Real-world workloads:**
The Azure production traces (Section V-A3) with code (2.57 req/s, 3-7437 input tokens) and conversation (5.53 req/s, 2-14050 input tokens) datasets capture realistic heterogeneity, not synthetic uniform distributions.

### Weaknesses

**1. The "evaluator" is simulation, not silicon (Section IV-F):**
The paper states the evaluator is "modified based on WSC-LLM" and "calibrated with actual data collected from a representative NPU device [85]." But [85] is their own concurrent work, and the wafer-scale chip itself doesn't exist. The D2D model reuses ASTRA-sim. This is fundamentally a simulation study claiming 3.68× improvement over another simulation (WSC-LLM). No silicon validation means the absolute numbers are projections.

**2. Thermal modeling is completely absent:**
A 12-inch wafer with 30 compute dies running simultaneous prefill and decode will have heterogeneous power density. Prefill is compute-bound (high power on some cores), decode is memory-bound (high DRAM I/O power). The paper never mentions thermal constraints, hotspots, or whether the "optimal" configurations are thermally feasible. This is a significant omission for wafer-scale systems where thermal management is notoriously difficult.

**3. The GPU comparison is against unified scheduling only:**
Figure 12 compares FACE against vLLM, which uses unified scheduling (U-Sch). But the paper's own Figure 11 shows that U-Sch significantly underperforms disaggregated scheduling (W-Sch) on wafer-scale chips. The fair comparison would be against disaggregated GPU systems like DistServe [91] or Splitwise [53], which the paper cites but doesn't benchmark against.

**4. LUT storage and lookup overhead is hand-waved:**
Section IV-B2 claims the LUT is stored "on the host, thus without any on-wafer storage consumption." But the LUT must cover all combinations of `(p_chunk, d_batch, d_token, p_tile, d_tile)`. With chunked prefill at dataset-average length, d_batch up to 64, d_token up to 2048+ (from the datasets), and multiple tile size pairs—this could be millions of entries. The paper claims lookup is "one-dimensional Euclidean-distance computation" (Section IV-C2), but doesn't quantify lookup latency versus the ~millisecond iteration times.

**5. Yield and cost are ignored:**
Section II-B mentions chiplet-based integration offers "higher configurability and improved manufacturing yield" versus monolithic, but never quantifies this. A 12-inch wafer with 30 dies at 800mm² each (case 10 in Table I) will have brutal compound yield. The "optimal" architecture with maximum die size may be economically infeasible.

## Q4: What the Authors Didn't Tell You

**1. The "fully overlapped" claim has fine print:**
Section IV-B2 states that prefill uses "chunked prefill" with chunk size set to "the average input token length from the test dataset." This means a request with 7000 input tokens (present in the Azure code dataset) requires many iterations of chunked processing, during which its TTFT (time-to-first-token) balloons. The paper reports only aggregate E2E latency and throughput—not TTFT or P99 latency, which are critical SLO metrics for real deployments.

**2. The scheduling complexity is O(n) per request (Section IV-C):**
While the paper argues this is "insignificant," consider: the decode engine must iterate through all instances in the schedulable range (Section IV-C2), perform LUT lookup for each, compute ΔT, and select the minimum. With ~7 instances per wafer (30 dies / 4 dies per instance), this is cheap. But scale to multi-wafer systems (mentioned in Section VI-B1), and this becomes a centralized scheduling bottleneck.

**3. The "fine-grained control" assumption may not be free:**
The paper assumes the host can "directly manage the controller and DMA engine of each core" (Section III-C). This control path has latency. If the host must issue new tile configurations every iteration (~ms scale), the control overhead could be non-trivial. Real NPUs like those from [85] may batch instructions, making per-iteration reconfiguration expensive.

**4. The KV cache offloading strategy (Section IV-D2) adds tail latency:**
For "burst requests with exceptionally long input token lengths," KV cache is distributed across multiple instances with "multiple remote accesses via D2D links." The paper acknowledges this increases effective hop count (bottom of Fig. 9) but doesn't quantify the latency penalty. Long-context requests—increasingly common with 128K+ context windows—would systematically hit this penalty.

**5. The architectural exploration is constrained by their template:**
Table I shows the exploration space: 10 architecture cases with fixed (die_size, HBM_count) combinations. The paper doesn't explore: (a) heterogeneous die sizes within a wafer, (b) asymmetric HBM placement, (c) non-square die arrays, or (d) different memory technologies (e.g., CXL-attached DRAM). The "optimal" architecture is optimal within a narrow search space, not globally.

**6. No comparison with WaferLLM [26]:**
The paper cites WaferLLM as concurrent work on wafer-scale LLM inference but provides no head-to-head comparison. Given WaferLLM uses monolithic integration (Cerebras-style) versus FACE's chiplet approach, this comparison would illuminate fundamental architectural trade-offs.