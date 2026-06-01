## Q1: Whiteboard Explanation

Imagine you're trying to run a massive LLM like GPT-175B, but a single chip can't hold it all. The traditional solution is to spread it across many GPUs connected by slow networks. **Wafer-scale chips** change this by cramming ~50 dies onto a single 215mm×215mm wafer with **very fast Die-to-Die (D2D) interconnects** — roughly 6× faster than NVLink.

**The Core Problem:** LLM inference has two phases with *opposite* resource needs:
- **Prefill:** Process all input tokens at once. This is **compute-bound** (like training).
- **Decode:** Generate one token at a time, repeatedly reading the KV cache. This is **memory-bandwidth-bound**.

Existing GPU systems either run both phases on the same hardware (inefficient) or split them across nodes but suffer from:
1. Slow inter-node KV cache transfers
2. Fixed parallelism strategies (e.g., always TP=8)
3. Wasted memory (prefill instances barely use DRAM after generating KV cache)

**WSC-LLM's Trick:** Co-design the architecture and scheduling specifically for wafer-scale chips:

1. **Central Scheduler (Algorithm 1):** For each phase, exhaustively search for the optimal:
   - Number of dies per instance (instance size)
   - Tensor Parallelism (TP) configuration
   - Then determine how many prefill vs. decode instances fit on the wafer

2. **Decoding-Centered Placement (Equation 1):** Place decode instances in the wafer's center, prefill around the edges. This minimizes KV cache transfer hops on the 2D mesh topology.

3. **Memory Scheduler (Algorithm 2):** Since D2D bandwidth often exceeds DRAM bandwidth, KV cache doesn't need to live only in decode instances. The scheduler distributes KV cache across *all* DRAMs on the transfer path — including idle prefill instance DRAMs — effectively pooling memory across the wafer.

**The Insight:** Wafer-scale chips turn inter-die memory into a shared, high-bandwidth pool. The paper exploits this to break the isolation between prefill and decode phases.

---

## Q2: The Key Insight

The central intellectual contribution isn't just "wafer-scale chips are good for LLMs." It's this:

> **On wafer-scale chips, D2D bandwidth can exceed DRAM bandwidth. Therefore, cross-die DRAM access is limited by DRAM bandwidth, not communication — enabling a "shared memory pool" abstraction that eliminates the traditional memory isolation between disaggregated prefill and decode instances.**

This is articulated most clearly in Section 4.4 (Memory Scheduler):

> *"Wafer-scale chips offer high D2D bandwidth, typically exceeding DRAM access bandwidth. Thus, in the absence of D2D link congestion, cross-die DRAM read and write operations are constrained only by DRAM bandwidth rather than D2D bandwidth."* (Page 7)

**Why this matters:** Prior disaggregated systems (like Splitwise) *must* transfer the entire KV cache from prefill to decode instances because memory is isolated per node. This creates a pipeline stall. WSC-LLM instead leaves KV cache in place (even on prefill dies!) and accesses it remotely during decode — because the D2D link is fast enough to overlap with computation.

This insight drives the Memory Scheduler's design (Algorithm 2), which allocates KV cache storage along the shortest path between prefill and decode instances, treating those DRAMs as a unified pool (Section 4.4, Line 1: `D' ← Relevant(Pi, Dj)`).

**Prior work gap:** Splitwise [62] and DistServe [100] assume memory is per-instance. NeuroVM-style memory disaggregation exists for GPUs but not for the 2D-mesh topology unique to wafer-scale chips.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Comprehensive Design Space Exploration (Table 1, Figure 10):** Testing 4 architectural configurations across 4 models (7B–175B) and 2 datasets (code, conversation) shows the framework's generality. The finding that Case 3 (moderate DRAM capacity) wins across nearly all scenarios is non-obvious and actionable.

2. **Real Production Traces (Section 5.1.4):** Using Azure's public dataset [1] with realistic arrival times, prompt lengths, and output lengths adds credibility. The code vs. conversation distinction captures meaningful workload variance (median decode tokens: 13 vs. 129).

3. **Ablation Studies Reveal Mechanism (Figure 12):** Disabling Central Scheduler (no-Central) vs. Memory Scheduler (no-Memory) cleanly isolates contributions. The observation that Memory Scheduler dominates for larger models (Section 5.4, Phenomenon 2) is insightful.

4. **Scalability Evaluation (Figure 14):** Testing a 2×2 wafer array against Splitwise on LLaMA3-405B demonstrates the framework scales beyond a single wafer. The comparison at matching W2W bandwidth (400 GB/s) is fair.

### Weaknesses

1. **Simulation Validation Gap:** The evaluator is built on ASTRA-sim [83] with a DNN-based lookup table for intra-die mapping (Section 4.6). However:
   - No validation against RTL or measured silicon latencies.
   - The claim that "error of fitted results is within a controllable range" (page 9) cites prior work [37, 89] but provides **no quantitative error bounds** for WSC-LLM's specific model.
   - Memory access patterns (e.g., HBM bank conflicts, refresh overhead) are likely abstracted away.

2. **Hardware Template is Hypothetical:** The compute die (Section 5.1.1) is described as "Dojo-style" at 7nm, 1GHz, with 6TB/s total D2D bandwidth. But:
   - Tesla Dojo [73] is 7nm at ~2GHz with different core counts.
   - HBM configurations (Table 1: 32–96GB, 1–3TB/s) are plausible but unvalidated against packaging constraints.
   - **No power or area estimates** for the configurations. Is Case 3 actually buildable?

3. **Baseline Comparison Asymmetry (Section 5.3):** Splitwise (SW-GPU) uses 6 nodes of 8×A100-80GB (14,976 TFLOPS, 3,840GB). The wafer-scale chip has 14,100 TFLOPS and 3,456GB. The paper claims "fewer resources" but:
   - A100 TFLOPs are for sparse tensor operations; dense FP16 is 312 TFLOPS per GPU → 14,976 TFLOPS total, matching the comparison.
   - Inter-node bandwidth is 400GB/s, but intra-node NVLink is 900GB/s per GPU. The paper compares D2D bandwidth (1.5–2.5TB/s per die) but doesn't account for NVLink's lower-latency topology within a node.

4. **Communication Model Simplicity:** All-reduce is modeled as bidirectional ring (Section 4.5.1, Figure 9a). This ignores:
   - Ring topology inefficiency on 2D mesh (requires virtual ring embedding).
   - No comparison to tree-based or 2D-mesh-aware collectives (e.g., TACOS [82] is cited for scalability but not adopted).

5. **Limited Sensitivity Analysis:** DRAM refresh, thermal throttling, and yield loss are not modeled. For wafer-scale integration, defect rates and redundancy requirements are critical and absent.

---

## Q4: What the Authors Didn't Tell You

### Unstated Assumptions

1. **Infinite NoC Bandwidth Within a Die:** The paper mentions each die has a 16×16 core array with NoC (Section 5.1.1), but the intra-die engine (Section 4.5.2) assumes sub-tasks complete without NoC contention. No NoC bandwidth specification is given.

2. **Perfect D2D Link Availability:** The placement algorithm (Section 4.2.2) minimizes hops but assumes no link failures. Wafer-scale chips historically have redundancy for defects — the cost of this is not modeled.

3. **DRAM Refresh is Invisible:** HBM at high capacity (64–96GB per die) requires periodic refresh. During refresh windows, bandwidth drops. This is not modeled but would impact memory-bound decode phases.

### Limitations Buried in Fine Print

- **Section 4.2.1:** "The search space remains manageable, with a solving time of only a few minutes." This implies Algorithm 1 runs **offline**. If workload distributions shift online (e.g., sudden traffic spike), recomputing is non-trivial.

- **Section 4.6:** The DNN-based lookup table for intra-die mapping is "pre-built and stored offline." This bakes in assumptions about model architectures. New attention variants (e.g., sliding window, MoE routing) would require retraining the DNN.

### Open Research Questions

1. **Fault Tolerance:** What happens when a die fails mid-request? KV cache spread across multiple dies (Algorithm 2) implies checkpointing complexity.

2. **Multi-Tenant Isolation:** Can multiple LLMs share a wafer? The current design assumes a single model family (weights replicated per instance).

3. **Speculative Decoding Compatibility:** The paper focuses on vanilla autoregressive decode. Speculative decoding [72] adds verification complexity — does the Memory Scheduler's KV placement still help?

4. **Thermal Gradients:** Wafer-scale chips have non-uniform thermal behavior. Center dies run hotter. Does placing decode instances in the center (Section 4.2.2) exacerbate thermal throttling during the memory-bound phase?