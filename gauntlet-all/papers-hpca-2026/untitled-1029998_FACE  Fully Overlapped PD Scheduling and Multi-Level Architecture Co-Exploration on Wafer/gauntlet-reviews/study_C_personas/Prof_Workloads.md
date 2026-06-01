Q1: Whiteboard Explanation

Let me walk you through FACE as if we're at a whiteboard.

**The Problem Setup:**
Imagine a wafer-scale chip—a massive 12-inch silicon wafer packed with compute dies, HBM memory, and high-bandwidth D2D interconnects. You want to run LLM inference on it. The challenge? LLM inference has two fundamentally different phases:

1. **Prefill**: Process the entire input prompt to generate the first token. This is compute-bound (lots of matrix multiplications).
2. **Decode**: Generate tokens one-by-one using the KV cache. This is memory-bound (tiny computations, lots of memory reads).

**The Prior Approach (WSC-LLM):**
The previous state-of-the-art used "disaggregated scheduling"—physically partition the wafer into prefill-only and decode-only instances. Sound familiar? It's what GPU clusters do. But on a wafer, this creates problems:
- The 2D-mesh topology means some prefill instances are far from decode instances (tail latency for KV cache transfer)
- You can't hit the ideal prefill:decode resource ratio due to discrete die counts
- Decode instances sit at <9% compute utilization (Figure 4b shows this across batch sizes)

**FACE's Key Move:**
Instead of separating prefill and decode, FACE runs them *simultaneously* within every instance. The insight: wafer-scale chips offer fine-grained control at the core level via control I/O to the host. You can precisely partition which cores handle prefill attention tiles versus decode attention tiles at each iteration.

**The Three-Part Solution:**
1. **Configuration Space Exploration (CSE)**: Offline, exhaustively explore all valid combinations of (prefill chunk size, decode batch size, decode token count, tile sizes for both phases). Store results in a Look-Up Table (LUT). The constraint? SRAM capacity per core must fit both tiles.

2. **Dynamic Adaptive Scheduling (DAS)**: At runtime, when a new decode request arrives, query the LUT to find which instance would see the minimum increase in per-iteration latency (∆T). Assign the request there.

3. **Optimized Memory Management (OMM)**: Exploit the fact that D2D bandwidth exceeds DRAM bandwidth. This means a decode request can read KV cache from a *remote* instance without D2D congestion, as long as the distance satisfies: Distance ≤ D2D_BW / DRAM_BW. This expands the schedulable range.

**The Architecture Co-Exploration:**
FACE also searches the hardware design space at two levels:
- **Microarchitecture**: What SRAM/compute/NoC balance per core maximizes LLM block throughput? (Answer: 0.75MB SRAM with high compute and large NoC)
- **Architecture**: Given a fixed wafer area, how many HBM chiplets per die? Larger dies with more HBM win, but only if D2D bandwidth doesn't become the bottleneck.

---

Q2: The Key Insight

The central insight is that **wafer-scale chips' fine-grained core-level control enables fully overlapped prefill-decode execution**, which eliminates the prefill-decode interference that plagues both unified scheduling (serialized attention) and disaggregated scheduling (topology constraints, resource underutilization).

The paper articulates this in Section III-C (page 4-5): "The fine-grained control of wafer-scale chips provides the foundation for this approach. Through control I/O, the host can directly manage the controller and DMA engine of each core to precisely regulate the sizes of prefill and decode tiles assigned according to scheduling requirements."

**Why this matters:** Previous GPU-optimized approaches either:
- Serialize prefill and decode attention (unified scheduling like vLLM), causing interference
- Separate them onto different hardware (disaggregated scheduling like DistServe), causing topology constraints and <9% decode compute utilization

FACE recognizes that wafer-scale chips aren't just "bigger GPUs"—they have a fundamentally different control model. By exploiting PE-level execution control and the dual-head pipeline strategy (Section IV-E), you can run prefill attention on some cores while decode attention runs on others, within the same iteration, on the same instance.

The second key insight is architectural: **the D2D bandwidth exceeding DRAM bandwidth creates a "free" scheduling expansion**. Equation 1 (page 8) formalizes this—decode requests can be scheduled to any instance within Distance ≤ D2D_BW/DRAM_BW hops without incurring additional latency, because the bottleneck remains DRAM access, not D2D transfer.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Real-world production traces**: They use Azure traces (Section V-A3) with realistic arrival patterns—code dataset at 2.57 req/s with 3-7437 input tokens, conversation dataset at 5.53 req/s with 2-14050 tokens. This isn't synthetic uniformly-distributed nonsense.

2. **Model diversity**: Testing LLaMA2-7B, LLaMA2-13B, and LLaMA3-70B covers both standard attention (7B/13B) and Grouped-Query Attention (70B), which have different compute-to-memory ratios.

3. **Calibration with real hardware**: Section IV-F states the evaluator is "calibrated with actual data collected from a representative NPU device [85]" and "DRAM access is modeled based on real HBM hardware [2]." This addresses the simulation fidelity concern.

4. **Ablation structure**: Figure 11 systematically isolates contributions—they test all 6 combinations of {W-Sch, U-Sch, F-Sch} × {W-Arch, F-Arch}, showing both scheduling and architecture improvements are necessary.

5. **Microarchitecture exploration depth**: Figure 13 shows 14 configurations across three die sizes and two models, providing evidence for the 0.75MB SRAM sweet spot claim.

**Weaknesses:**

1. **The "Cherry-Pick" Check — Missing workload diversity**: 
   - They only test decoder-only LLMs (LLaMA family). What about encoder-decoder models (T5, BART) or vision-language models with heterogeneous prefill patterns?
   - The datasets are both from Azure's ChatGPT-like workloads. What about batch inference scenarios with predictable, bursty arrivals (e.g., search reranking)?
   - No mention of adversarial workloads: What happens when input lengths are highly bimodal (many very short + few very long)?

2. **The Baseline Validity Check — Potentially Stale GPU Comparison**:
   - They compare against "extended vLLM" on 6× A100 nodes (Section V-C), but A100s are 2020 hardware. Why not H100 clusters with NVL72's 900 GB/s interconnect? Section VI-B2 acknowledges this but dismisses it without evaluation.
   - The vLLM version isn't specified. Modern vLLM (2024+) has chunked prefill, speculative decoding, and tensor parallelism optimizations that may narrow the gap.

3. **The "Zero-Event" Reality Check — Decode Utilization Baseline Questionable**:
   - Figure 4(b) shows decode utilization <9% to motivate the problem. But this uses "default setup in WSC-LLM [79]"—is that a fair representation? If WSC-LLM's default is suboptimal, comparing against it inflates FACE's improvement.
   - They don't report FACE's actual decode compute utilization numbers. Did it actually improve?

4. **Simulation-Only Results**:
   - Despite calibration claims, there's no silicon validation. The paper even states FACE "performs end-to-end scheduling" (Section VI-B1), but all results come from the evaluator built on ASTRA-sim [76]. How do control overheads, memory fragmentation, and thermal throttling affect real performance?

5. **Missing Latency Distribution Analysis**:
   - They report average E2E latency, but for SLO-driven LLM serving, P99/P999 latency matters more. The OMM strategy with remote KV cache access could create tail latencies that aren't captured in averages.

6. **Throughput-Latency Tradeoff Not Shown**:
   - Figure 11 and 12 show E2E latency and throughput separately. Where's the throughput-latency Pareto curve? At what point does FACE's scheduling saturate?

7. **LUT Storage and Lookup Overhead Unquantified**:
   - Section IV-C claims LUT lookup is O(n) where n is "only a few to a dozen instances." But how large is the LUT? If the exploration space includes multiple decode batch sizes, token counts, and tile sizes, the LUT could be substantial. Storage on host is "free," but lookup latency during real-time scheduling isn't characterized.

---

Q4: What the Authors Didn't Tell You

1. **The cost model is incomplete**: They never discuss wafer-scale chip cost, yield, or power. A 12-inch wafer with advanced packaging isn't cheap. Section V-A1 mentions "high yield and cost efficiency" for chiplet-based integration but provides no numbers. Is FACE's 3.68× speedup worth 10× the cost of a GPU cluster?

2. **The "fine-grained control" assumption is optimistic**: The paper assumes the host can "directly manage the controller and DMA engine of each core" (Section III-C) with negligible overhead. In practice, issuing per-core control commands at microsecond granularity requires a sophisticated runtime. They don't discuss the control plane bandwidth or latency.

3. **Memory fragmentation is hand-waved**: The KV cache allocation strategy (Section IV-D2) assumes you can always find contiguous space on remote instances. What happens when memory becomes fragmented after many request arrivals and departures? PagedAttention [40] solved this for GPUs—where's the equivalent for wafer-scale?

4. **The dual-head pipeline (Section IV-E) has hidden assumptions**: They claim concurrent PE array and vector unit utilization, but this requires the softmax computation time to exactly match the matrix multiplication time. If they don't balance, one unit stalls. They don't show the actual pipeline efficiency.

5. **OMM's schedulable range expansion is limited in practice**: Equation 1 says Distance ≤ D2D_BW/DRAM_BW. With their optimal architecture (case 10, Table I), D2D bandwidth is 5.69 TB/s and DRAM bandwidth is 8×410 GB/s = 3.28 TB/s per die. The ratio is ~1.7, meaning you can only schedule 1-2 hops away. For a 6×5 die array, this still constrains scheduling significantly.

6. **The "optimal" architecture might not generalize**: Case 10 (large dies, 8 HBM chiplets) wins for LLaMA models. But LLaMA is representative of dense, decoder-only transformers. Mixture-of-Experts models (DeepSeek, Mixtral) with sparse activations have entirely different memory access patterns. The paper doesn't test MoE workloads despite citing DeepSeek [46] in the references.

7. **CSE's offline exploration assumes static workload distribution**: Section IV-B3 claims "macroscopic workload distribution...remains relatively stable" and samples the dataset for configuration selection. But what if workload characteristics shift (e.g., shorter prompts during peak hours)? The optimal configuration from Section IV-B3 might become suboptimal, and there's no online adaptation mechanism.

8. **They don't compare against other wafer-scale scheduling strategies**: The only wafer-scale baseline is WSC-LLM [79]. What about WaferLLM [26], cited in their related work? Figure 11 shows F-Sch + W-Arch also improves over W-Sch + W-Arch, suggesting the scheduling innovation alone is valuable—but they don't isolate how much of the 3.68× comes from scheduling versus architecture.