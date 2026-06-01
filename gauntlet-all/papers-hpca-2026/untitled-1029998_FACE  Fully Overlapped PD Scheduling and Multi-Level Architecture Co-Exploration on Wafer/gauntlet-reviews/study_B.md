# Study B — Rich Directive
**Paper:** 1029998 FACE  Fully Overlapped PD Scheduling and Multi Level Architecture Co Exploration on Wafer  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:27

---

Q1: Whiteboard Explanation

Let me walk you through FACE as if explaining it on a whiteboard.

**The Problem Setup:**
Imagine a wafer-scale chip—a 12-inch silicon wafer packed with dozens of compute dies, each containing NPU cores and attached HBM memory, all connected via a 2D-mesh D2D interconnect. The challenge is: how do you efficiently run LLM inference on this massive, hierarchical hardware?

LLM inference has two phases: prefill (compute-intensive, processes the entire input prompt) and decode (memory-intensive, generates tokens one at a time autoregressively). Previous approaches either ran them separately on dedicated hardware regions (disaggregated), causing resource imbalance and topological constraints, or ran them together but serialized (unified), causing interference.

**The Key Innovation:**
FACE achieves *fully overlapped* prefill-decode execution within each instance. Here's how:

1. **Configuration Space Exploration (CSE):** Offline, FACE explores all valid configurations where prefill attention and decode attention can run simultaneously on the same cores. The trick is that wafer-scale NPUs offer fine-grained control—you can precisely allocate different tile sizes to prefill vs. decode attention, mapping them to overlapping core groups. CSE generates a Look-Up Table (LUT) storing optimal tile configurations for various workload combinations.

2. **Dynamic Adaptive Scheduling (DAS):** At runtime, new requests arrive continuously. The prefill engine assigns incoming requests to instances with the fewest pending chunks (load balancing). The decode engine is more sophisticated—when a prefill completes and generates a decode request, DAS queries the LUT to find which instance would experience minimal latency increase if this decode request were added, then routes it there.

3. **Optimized Memory Management (OMM):** Because D2D bandwidth exceeds DRAM bandwidth, a decode request doesn't have to run where its prefill ran. OMM defines a "schedulable instance range" based on the ratio D2D_BW/DRAM_BW—instances within this hop distance can access KV cache without D2D congestion. This expands scheduling flexibility dramatically.

**The Architecture Co-Exploration:**
FACE also searches the microarchitecture (core SRAM size, compute units, NoC bandwidth) and architecture (die size, HBM count per die, D2D bandwidth) design space. The finding: moderate SRAM per core with maximum compute/NoC density, large dies near reticle limit, and maximum HBM chiplets per die generally wins.

---

Q2: The Key Insight

The central insight is that **wafer-scale chips' fine-grained control capability enables true prefill-decode overlap at the attention operator level, which fundamentally eliminates the interference that plagues existing scheduling approaches.**

Previous work assumed prefill and decode must execute serially during attention (even if linear operators could overlap). FACE recognizes that wafer-scale NPUs—unlike GPUs—allow the host to directly control tile sizes and execution mapping at per-core granularity through control I/O. By pre-computing compatible tile configurations offline (where prefill attention tiles and decode attention tiles can execute concurrently on partitioned core groups within the same instance), FACE converts the scheduling problem from online search to table lookup.

This is fundamentally different from disaggregated scheduling (which spatially separates phases, amplifying topological constraints) and unified scheduling (which temporally serializes phases, wasting compute during decode). FACE achieves spatial co-location with temporal overlap.

The secondary insight is that **high D2D bandwidth relative to DRAM bandwidth enables flexible decode request migration**—a decode request can be routed to any instance within D2D_BW/DRAM_BW hops of where prefill executed, because remote KV cache access remains DRAM-bound, not D2D-bound. This converts a rigid topological constraint into a scheduling degree of freedom.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive design space coverage:** The evaluation systematically explores both microarchitecture (14 core configurations × 3 die sizes) and architecture (10 wafer configurations), providing genuine insights rather than single-point comparisons.

2. **Real workload traces:** Using Azure production traces with realistic arrival rates and length distributions is significantly more credible than synthetic uniform workloads.

3. **Meaningful baselines:** Comparing against WSC-LLM (state-of-the-art wafer-scale) and vLLM (state-of-the-art GPU) covers the relevant design space. The ablation separating scheduling strategy (W-Sch/U-Sch/F-Sch) from architecture (W-Arch/F-Arch) isolates contributions.

4. **Model diversity:** Testing LLaMA2-7B, 13B, and LLaMA3-70B (with GQA) covers different scales and attention variants.

**Weaknesses:**

1. **Simulation-based evaluation:** The evaluator is analytical/simulation-based, calibrated against "a representative NPU device." There is no silicon validation. The claim of 3.68× improvement relies entirely on model accuracy, which is concerning for novel scheduling strategies where system-level effects (control overhead, memory fragmentation, queuing dynamics) may not be captured.

2. **Scheduling overhead not measured:** The paper claims CSE is "offline" and DAS has "O(n)" complexity, but never quantifies actual scheduling latency. For a system claiming "extremely real-time responsiveness," this omission is problematic.

3. **LUT size and lookup latency unspecified:** How large is the LUT? The paper claims it's stored on-host with "no on-wafer storage consumption," but doesn't address host memory footprint or lookup time for the "match-and-evaluate" strategy with Euclidean distance computation.

4. **Comparison fairness concerns:** The GPU comparison uses 6 A100 nodes while the wafer-scale chip is a single device. Total transistor count, power consumption, and cost comparisons would strengthen the argument.

5. **Limited sensitivity analysis:** The paper shows architecture exploration but doesn't analyze sensitivity to key assumptions (e.g., D2D bandwidth variations, different HBM generations, varying request arrival rates).

6. **Chunked prefill assumption:** Setting chunk size to "average input token length" is convenient but may be suboptimal for heavy-tailed workload distributions. The paper doesn't evaluate sensitivity to this choice.

---

Q4: What the Authors Didn't Tell You

**Implementation Complexity:**
The paper glosses over significant implementation challenges. Achieving fine-grained core-level control for simultaneous prefill/decode attention requires: (1) the NPU architecture to support dynamic tensor partitioning mid-execution, (2) the compiler/runtime to generate appropriate instructions, and (3) the host-device control interface to have sufficiently low latency. None of this infrastructure is described or validated.

**Memory Fragmentation:**
The KV cache allocation strategy (Section IV-D2) suggests offloading to nearby instances when local DRAM is exhausted. But LLM serving involves continuous request arrival/completion, inevitably causing memory fragmentation. How KV cache is compacted or migrated during execution is unaddressed. PagedAttention-style memory management would help but isn't discussed.

**Failure Handling and Load Imbalance:**
The DAS algorithm routes decode requests based on LUT-estimated latency increases, but workload estimation errors accumulate. What happens when estimates diverge from reality? There's no feedback mechanism to correct scheduling decisions. Additionally, the prefill engine's simple "assign to least-loaded queue" heuristic may cause imbalance when request lengths vary dramatically.

**Scalability to Multi-Wafer:**
Section VI-B1 briefly mentions FACE "can naturally extend to multi-wafer systems" but provides no evidence. The OMM constraint (D2D bandwidth > DRAM bandwidth) almost certainly fails across wafers, meaning inter-wafer scheduling would require completely different mechanisms.

**Cost and Yield Considerations:**
The architecture exploration recommends "compute dies as large as possible" (near reticle limit) with maximum HBM chiplets. This ignores yield implications—larger dies have exponentially worse yield. The paper's 800mm² dies at 7nm would face significant manufacturing challenges and cost penalties that the performance-only analysis completely ignores.

**Thermal and Power Constraints:**
Achieving full prefill-decode overlap means potentially running all cores at high utilization simultaneously. The paper never discusses power delivery or thermal implications for wafer-scale integration, which are known challenges for this form factor.

**The "Optimal" Architecture Finding:**
The conclusion that "large dies + maximum HBM" wins is somewhat trivial—more memory bandwidth helps memory-bound workloads. The more interesting question is cost-performance tradeoffs, which the paper avoids entirely.