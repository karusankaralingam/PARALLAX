## Q1: Whiteboard Explanation

Let me walk you through what this paper is actually doing at the hardware level.

**The Setup:** They're targeting wafer-scale chips—imagine a 215mm × 215mm wafer (basically a full 12-inch wafer) packed with compute dies connected via Die-to-Die (D2D) links in a 2D mesh topology. Each die has compute cores, DRAM chiplets (HBM stacks), NoC interconnects, and D2D interfaces around the periphery. The key physical constraint: the wafer area is fixed at ~46,225 mm², so every mm² you spend on DRAM is a mm² you *don't* spend on compute or D2D interfaces.

**The Core Mechanism:**

1. **Disaggregated Prefill/Decode Scheduling** (Figure 6): They partition the wafer into "prefill instances" and "decoding instances." Prefill is compute-bound (lots of parallel tokens), decode is memory-bandwidth-bound (sequential token generation reading KV cache). Algorithm 1 exhaustively searches over instance sizes and TP configurations to find optimal per-die goodput for each phase, then allocates dies proportionally.

2. **Decoding-Centered Placement** (Figure 7b): Since KV cache flows from prefill→decode instances, they place decode instances centrally and prefill instances around the perimeter. This minimizes the total hop distance for KV cache transfers in the 2D mesh. They formalize this with Equation 1: `TransferCost = Σ min(Distance(Pi, Dj))`.

3. **Memory Scheduler for KV Cache** (Algorithm 2): Here's the actual clever bit. Because D2D bandwidth exceeds DRAM bandwidth on wafer-scale chips, cross-die DRAM access is bottlenecked by DRAM, not the interconnect. So they exploit this by storing KV cache in *any* DRAM along the shortest path between prefill and decode instances—not just the local DRAM. The algorithm identifies the "relevant" DRAM set (those on shortest paths), sorts by location then capacity, and greedily allocates KV cache storage.

4. **TP Engine** (Section 4.5.1): For tensor parallelism, they use bidirectional Ring All-Reduce (Figure 9a) which maps naturally to the 2D mesh. They enumerate basic TP strategies (partition along B, S, H, K dimensions) and hybrid strategies (multi-dimensional partitions) to find what works best per phase.

**Data Flow:** Request arrives → Prefill Pool processes (chunked prefill for long prompts) → KV cache written to DRAMs along path → Decode Pool reads KV cache and generates tokens → continuous batching within decode instances.

---

## Q2: The Key Insight

**The "Magic Trick":** The paper's fundamental insight is that **D2D bandwidth on wafer-scale chips exceeds DRAM bandwidth**, which inverts the traditional memory hierarchy assumptions.

Specifically, from Section 4.4: *"Wafer-scale chips offer high D2D bandwidth, typically exceeding DRAM access bandwidth. Thus, in the absence of D2D link congestion, cross-die DRAM read and write operations are constrained only by DRAM bandwidth rather than D2D bandwidth."*

This means you can treat *all* DRAMs on the wafer as a single unified memory pool without paying a D2D penalty—the bottleneck is always DRAM bandwidth, not interconnect bandwidth. This enables their Memory Scheduler (Algorithm 2) to store KV cache in remote DRAMs "for free" (no additional latency penalty), which dramatically increases effective memory capacity for the decode phase.

**Why this matters:** In traditional GPU clusters, transferring KV cache between nodes is expensive (limited inter-node bandwidth), so disaggregated inference like Splitwise suffers significant communication overhead. On wafer-scale chips, the D2D bandwidth is high enough (~2-2.5 TB/s per die, per Table 1) that KV cache placement becomes a pure DRAM bandwidth scheduling problem, not a communication problem.

**The structural delta vs. baseline:** Splitwise on GPUs assumes KV cache must be transferred and stored locally at decode instances. WSC-LLM stores KV cache *in transit*—along the shortest path between prefill and decode—using DRAMs that would otherwise sit idle in prefill instances (see Figure 5b showing prefill instances have very low DRAM utilization in baseline).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Fair Compute/Memory Normalization (Section 5.3):** The baseline comparison uses "six 8-A100-80GB GPU nodes" with 14,976 TFLOPS and 3,840 GB DRAM versus 14,100 TFLOPS and 3,456 GB on the wafer. The wafer has *less* compute and memory but wins anyway—this is a credible iso-resource comparison, not apples-to-oranges.

2. **Ablation Studies Isolate Contributions (Section 5.4, Figure 12):** They separately disable the Central Scheduler (TP/placement optimization) and Memory Scheduler, showing the Memory Scheduler contributes more for larger models (LLaMA-30B, 70B, GPT-175B). This is methodologically sound and identifies which component matters.

3. **Real Production Traces (Section 5.1.4):** Using Azure public traces with actual arrival times, prompt distributions, and output lengths is far more credible than synthetic uniform distributions.

4. **Architecture Design Space Exploration (Section 5.2, Figure 10):** They explore four configurations trading DRAM capacity vs. compute dies vs. D2D bandwidth (Table 1), finding Case 3 (moderate DRAM) optimal. This is useful for architecture designers, not just a single-point evaluation.

### Weaknesses

1. **Simulation-Based, No Silicon (Section 4.6):** The evaluator is built on ASTRA-sim with a DNN-fitted lookup table for intra-die timing. They admit "the error of the fitted results is within a controllable range" but never quantify this. No tape-out, no FPGA prototype, no RTL synthesis results.

2. **Idealized Assumptions on D2D Bandwidth:** The paper assumes no D2D congestion and that D2D bandwidth exceeds DRAM bandwidth. Table 1 shows D2D bandwidth ranging from 1.5-2.5 TB/s while DRAM is 1-3 TB/s—so Case 4 actually has D2D bandwidth *less than* DRAM bandwidth. The Memory Scheduler's "free" remote DRAM access assumption breaks down here, but they don't deeply analyze this regime.

3. **Limited Baseline Comparisons:** They only compare against Splitwise. No comparison to other wafer-scale work (Cerebras WSE2, Tesla Dojo), no comparison to DistServe [100], LoongServe [84], or DeepSpeed-FastGen [28] which they cite.

4. **Scalability Section Uses Hypothetical Multi-Wafer (Section 6.2, Figure 14):** The 2×2 wafer array evaluation assumes either 1.8 TB/s or 400 GB/s W2W bandwidth, but neither configuration exists—this is purely speculative.

5. **No Power or Cost Analysis:** Wafer-scale chips have significant cooling and yield challenges. The paper ignores power consumption, yield, and cost-per-token metrics entirely.

---

## Q4: What the Authors Didn't Tell You

1. **The 6 TB/s Interconnect Bandwidth is Suspicious (Section 5.1.1):** They claim "the interconnect interface of the compute die provides a total interconnect bandwidth of 6 TB/s across four directions." That's 1.5 TB/s per direction for a 21.92mm × 22.81mm die. For context, NVIDIA's NVLink on GB200 provides ~900 GB/s per GPU with exotic packaging. Getting 6 TB/s die-to-die in a 7nm process with peripheral D2D interfaces requires *very* aggressive assumptions about signaling density and power. They provide no area breakdown, no power budget, no PHY design details.

2. **The DRAM Chiplet Integration is Hand-Waved:** Figure 3 shows HBM stacks attached to compute dies, but HBM requires silicon interposer and TSV integration. They mention "CoWoS" packaging but don't discuss the massive interposer area required for 3-6 HBM stacks per die, the thermal implications, or how this affects the die count on the wafer. The trade-off curves in Table 1 (32GB→96GB DRAM capacity) imply adding more HBM stacks, which consumes substantial wafer area they don't account for.

3. **Algorithm 1's "test" Function is a Black Box:** Line 7-8 of Algorithm 1 calls `test(TP, W_prefill)` and `test(TP, W_decoding)` to evaluate goodput. This is where all the actual scheduling complexity lives, but it's described as "a simulator that executes workload on a single instance." The complexity claim of O(DS) ignores that each `test()` call is itself a full simulation.

4. **The Bidirectional Ring All-Reduce Doesn't Scale Well on 2D Mesh:** Figure 9a shows Ring All-Reduce with 4 dies in 2 timesteps for each phase. But on a 2D mesh, a ring topology requires careful routing to avoid congestion on shared links. With TP sizes of 16+ (needed for LLaMA3-70B with GQA, per Section 4.5.1), the ring becomes multiple hops and the latency scaling isn't linear. They don't show communication timing breakdowns.

5. **Memory Scheduler Ignores Bank Conflicts and Refresh:** Algorithm 2 treats DRAM capacity as a simple byte pool. Real HBM has banks, channels, and refresh cycles. Spreading KV cache across many dies' DRAMs creates scattered access patterns that could tank effective bandwidth due to row buffer misses and bank conflicts.

6. **The "3.12× improvement" Conflates Hardware and Software:** Section 5.3's comparison against Splitwise-GPU includes both the wafer-scale hardware advantage AND the WSC-LLM scheduling improvements. The fairer comparison is against Splitwise-Wafer (same hardware, Splitwise scheduling), which shows 4.81× E2E improvement—but this comparison just proves Splitwise wasn't designed for wafer-scale, not that WSC-LLM is uniquely good.