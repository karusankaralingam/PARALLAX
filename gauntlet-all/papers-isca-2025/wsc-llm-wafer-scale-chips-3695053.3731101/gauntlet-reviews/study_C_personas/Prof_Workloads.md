## Q1: Whiteboard Explanation

Let me explain this paper as if I'm standing at a whiteboard.

**The Problem Setup:**

Imagine you have a wafer-scale chip — essentially a massive 215mm × 215mm silicon wafer with dozens of compute dies integrated together, connected via high-bandwidth Die-to-Die (D2D) links. You want to run LLM inference on this thing.

*[Drawing a grid of boxes representing dies]*

LLM inference has two distinct phases:
1. **Prefill** — Process all input tokens in parallel. This is compute-bound.
2. **Decode** — Generate tokens one-by-one, reading KV cache. This is memory-bandwidth-bound.

The challenge is: wafer area is fixed. If you add more DRAM per die, you get more memory bandwidth (good for decode) and more capacity (can serve more requests). BUT you lose compute dies and D2D bandwidth (bad for prefill, bad for cross-die communication).

*[Drawing the trade-off triangle: Compute ↔ Memory ↔ Communication]*

**What WSC-LLM Does:**

It's a co-exploration framework with three key pieces:

1. **Central Scheduler** (Section 4.2): Decides how to partition the wafer. How many dies for prefill instances? How many for decode? What TP (tensor parallelism) size for each? Where to physically place them on the 2D mesh?

2. **Memory Scheduler** (Section 4.4): Since D2D bandwidth exceeds DRAM bandwidth, you can store KV cache *anywhere* on the wafer and access it without D2D being the bottleneck. So exploit idle DRAM in prefill instances to store decode's KV cache.

3. **TP Engine** (Section 4.5): Maps operators across dies using Ring-based All-Reduce/All-Gather for the 2D-mesh topology.

**The Key Trick:**

The paper's central insight is that wafer-scale chips have D2D bandwidth > DRAM bandwidth. This means KV cache transfers across dies are "free" (overlapped with DRAM access). Traditional disaggregated inference wastes prefill instance memory because KV cache must be transferred to decode instances. WSC-LLM says: "Just leave the KV cache wherever it's convenient, and read it remotely."

---

## Q2: The Key Insight

The paper's crucial insight is buried in **Section 4.4** and **Figure 5(b)**:

> *"Wafer-scale chips offer high D2D bandwidth, typically exceeding DRAM access bandwidth. Thus, in the absence of D2D link congestion, cross-die DRAM read and write operations are constrained only by DRAM bandwidth rather than D2D bandwidth."*

This is the architectural enabler for everything else. In traditional GPU clusters, moving KV cache from prefill nodes to decode nodes is expensive (limited NVLink/network bandwidth). On wafer-scale chips, D2D bandwidth is ~2-2.5 TB/s per die edge, while DRAM bandwidth is 1-3 TB/s per die. This inverts the usual constraint.

**Why this matters:**

1. **Memory Scheduler becomes powerful:** Algorithm 2 (KV Cache Placement) exploits this by storing KV cache in *any* DRAM along the transfer path between prefill and decode instances — not just in decode instances. This increases effective memory utilization from ~50% to ~70% (Figure 13(b)).

2. **Prefill instances are no longer "memory-wasted":** In Splitwise and other disaggregated systems, prefill instances hold almost no KV cache because it gets transferred out. WSC-LLM keeps some KV cache in prefill instance DRAMs.

3. **The "decoding-centered placement" strategy (Section 4.2.2):** By placing decode instances centrally and prefill around the perimeter, the shortest paths naturally traverse through useful DRAM, enabling the Memory Scheduler to work without D2D congestion.

The ablation study in **Figure 12** confirms this: the Memory Scheduler contributes more to performance than the Central Scheduler for larger models (LLaMA-30B, 70B, GPT-175B).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real production traces (Section 5.1.4):** They use Azure public traces with actual arrival times, prompt sizes, and output lengths. This is significantly better than synthetic distributions. The code and conversation datasets represent meaningful workload diversity.

2. **Fair hardware comparison attempt (Section 5.3):** When comparing against Splitwise-GPU, they ensure comparable total compute (14,976 TFLOPS vs 14,100 TFLOPS) and memory (3,840 GB vs 3,456 GB). Splitwise actually has *more* resources, making WSC-LLM's 3.12× improvement more credible.

3. **Ablation studies are well-designed (Section 5.4, Figure 12):** no-Central and no-Memory isolate the contributions of each scheduler component. The crossover where Memory Scheduler matters more for larger models is a believable finding.

4. **DSE across four configurations (Table 1, Figure 10):** Exploring the DRAM-capacity/D2D-bandwidth trade-off across Cases 1-4 provides actual architectural guidance, not just "our system is better."

### Weaknesses

1. **The Baseline Validity Problem:** 
   - **Splitwise-Wafer** is a strawman. Directly applying Splitwise's GPU-optimized scheduling to wafer-scale chips without any adaptation is obviously suboptimal. The 4.81× improvement over SW-Wafer is inflated. The more meaningful comparison is against SW-GPU (3.12×).
   - They don't compare against other wafer-scale scheduling approaches (e.g., Cerebras's actual software stack, Tesla Dojo's scheduling). The related work (Section 7) acknowledges Cerebras WSE2 and Dojo exist but doesn't benchmark against them.

2. **Simulation-Only Evaluation:**
   - The entire evaluation is based on ASTRA-sim extensions (Section 4.6). There's no real silicon validation. The paper acknowledges using a "DNN to model the relationship between input metrics and outcomes" for the mapping lookup table. What's the error bound of this model? They cite "existing simulator works [37, 89] validate the feasibility" but don't report their own validation accuracy.

3. **The "Cherry-Pick" Check — Model Selection:**
   - All models are decoder-only transformers (LLaMA variants, GPT-175B). No encoder-decoder models (T5, BART), no mixture-of-experts (Mixtral). MoE would stress communication differently.
   - LLaMA3-70B uses GQA (8 KV heads), which artificially reduces KV cache size and memory pressure. The other models use MHA. This asymmetry isn't deeply analyzed.

4. **Scalability Claims (Section 6.2) are Weakly Supported:**
   - Figure 14 shows 2×2 wafer arrays, but only against Splitwise with the same total GPU count. They don't show what happens with 4×4 or 8×8 wafer arrays. Does the decoding-centered placement still work? Do D2D paths become congested?
   - The W2W bandwidth configurations (400 GB/s vs 1.8 TB/s) are stated but the paper doesn't validate that these are achievable in practice.

5. **Missing Latency Tail Analysis:**
   - E2E latency and TPS are averages (or medians — not specified). For LLM serving, P99 latency is critical. Figure 5(b) shows peak memory usage but not latency distributions. Do some requests get starved?

6. **No Power/Energy Analysis:**
   - Wafer-scale chips have significant power constraints (Cerebras WSE-2 draws 15kW). The paper never mentions power. A 3× throughput improvement is less impressive if it comes with 5× power consumption.

---

## Q4: What the Authors Didn't Tell You

1. **The Real Comparison Should Be Against Optimized GPU Clusters:**
   - Splitwise is from late 2023. More recent systems like DistServe [100], LoongServe [84], and SGLang optimize disaggregated inference further. The 3.12× number against Splitwise may shrink against these.
   - More importantly: the paper doesn't consider **quantization**. Running LLaMA3-70B in INT4 on GPUs would change the memory bandwidth equation entirely. Their FP16 assumption (Section 5.1.3) is increasingly unrealistic for production.

2. **The Hardware Template is Hypothetical:**
   - Figure 3's architecture is a "highly configurable template," not a real chip. The die dimensions (24.99mm × 33.25mm), core count (16×16), and interconnect bandwidth (6 TB/s total) are design assumptions. Real wafer-scale chips like Cerebras WSE-2 have different characteristics (850,000 cores, 40 GB on-chip SRAM, 220 Pb/s interconnect).
   - The DRAM-per-die configurations (32GB-96GB HBM) assume 3D stacking that may not match actual packaging constraints.

3. **The Memory Scheduler's Assumptions:**
   - Algorithm 2 assumes KV cache can be arbitrarily split across DRAMs. But attention computation requires the *entire* KV cache for a sequence. If KV cache is scattered across 5 dies, the decode phase must gather it. The paper claims D2D bandwidth makes this "free," but this only holds if there's no congestion.
   - What happens when multiple requests' KV caches share the same D2D links? The paper's placement strategy (Equation 1) minimizes total distance but doesn't explicitly model contention.

4. **The Chunked Prefill Trade-off:**
   - Section 4.3.1 mentions using chunked prefill, but this trades TTFT (time-to-first-token) for throughput. For latency-sensitive applications, this may be unacceptable. The paper reports E2E latency but not TTFT.

5. **What "Moderate DRAM Capacity" Actually Means:**
   - Section 5.2 concludes that Case 3 (64GB DRAM, 2 TB/s DRAM bandwidth, 2 TB/s D2D bandwidth) is optimal. But this is optimal *for their workloads and models*. A different workload (e.g., very long contexts requiring 100GB+ KV cache per request) would shift this.

6. **The Cost and Yield Question:**
   - Wafer-scale integration has well-known yield challenges. The paper mentions "chiplet-based integration" (Section 2.3) for "high yield" but doesn't quantify this. If 10% of dies are defective, does the scheduling adapt? The algorithms assume a fully functional rectangular grid.