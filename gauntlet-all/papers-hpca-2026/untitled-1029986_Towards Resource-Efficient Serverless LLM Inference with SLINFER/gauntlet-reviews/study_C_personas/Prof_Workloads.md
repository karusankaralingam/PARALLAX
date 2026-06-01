## Q1: Whiteboard Explanation

**"What problem does this paper solve, and what's the core mechanism?"**

Imagine you're running a serverless LLM platform hosting 64 different private chatbots (3B-13B models). Each chatbot gets maybe 5 requests/hour on average. The problem? ServerlessLLM gives each model a whole A100 GPU, but Figure 5 shows average memory utilization is only **23%**. Meanwhile, 33% of requests miss their SLOs due to queuing (Section III-C).

**The Core Insight:** SLINFER realizes two things:
1. **CPUs with Intel AMX can now serve small LLMs independently** — Figure 6-8 show a 32-core 4th-Gen Xeon can meet TTFT/TPOT SLOs for 7B/13B models under moderate loads
2. **Multiple LLMs can time-share a single GPU/CPU** — since serverless workloads are bursty and sparse (Figure 12 shows concurrency swings from 1 to 128 for the same model)

**The Mechanism (Three Parts):**

1. **Headroom-Driven Compute Scheduling (§VI):** Instead of round-robin, SLINFER computes a "headroom" for each request — how much time slack remains before SLO violation. At each token generation cycle, it picks the instance with the *shortest* headroom (most urgent). This is token-level scheduling, not request-level.

2. **Hazard-Aware Memory Subsystem (§VII):** KV-cache scaling isn't free — Figure 17 shows scaling 32GB cache to 64GB takes 1.9 seconds. SLINFER uses watermarks (lazy scale-down, eager scale-up) and a "reservation station" to parallelize memory operations without OOM (Figure 18-19).

3. **Consolidation (§VIII):** When resources are tight, instead of fragmenting instances across nodes, SLINFER proactively preempts smaller-batch instances to let larger ones grow. This is critical because batch size 1+1 ≠ batch size 2 in efficiency (Table II: 2×half-GPU gives only ~50% of full-GPU concurrency).

---

## Q2: The Key Insight

**The Conceptual Leap:**

The paper's key insight is that **the "resource unit" for serverless LLM serving shouldn't be a whole GPU** — it should be **token-level compute timeslices + dynamically-sized memory blocks**.

Prior work (ServerlessLLM, Medusa) assumed each model needs exclusive GPU access because LLM inference is "compute-hungry." But SLINFER recognizes that:

1. **Compute demand is episodic, not continuous** — Figure 1's right panel shows compute usage spikes during prefill (building KV-cache) then drops during decode. Between requests, instances are idle.

2. **Memory demand is highly variable** — Figure 9 shows even under the top-1% workload (heaviest 1% of models), 50% of the time a 7B model uses <17GB. The P50 workload uses even less.

3. **CPUs are no longer just "auxiliary"** — Table I shows 4th-Gen Xeon (with AMX) gets 6.7× speedup over 3rd-Gen for TTFT. This isn't offloading attention to CPUs (like NEO); this is **CPUs independently serving entire models**.

**Why it matters:** This reframes the problem from "GPU scarcity" to "resource fragmentation." The solution isn't more GPUs — it's finer-grained multiplexing of existing ones.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Workload Representativeness is Strong:**
   - They use real Azure Serverless Traces [61] for invocation patterns (Figure 21 shows the characteristic hot/cold model distribution)
   - Token lengths from Azure LLM Conversation/Code datasets [54] (Figure 34 shows realistic input/output distributions)
   - This combination captures both the multi-model serverless pattern *and* realistic LLM workloads

2. **Comprehensive Sensitivity Analysis (§IX-I):**
   - Tested 5 different datasets (Figure 35): Azure Conv, Code, HumanEval, ShareGPT, LongBench
   - Varied CPU cores (Figure 29), keep-alive thresholds (Figure 30), watermark settings (Figure 31)
   - Showed BurstGPT trace results (Figure 27) — an actual LLM trace, not just serverless traces

3. **Fair Baseline Treatment:**
   - They don't just compare against vanilla ServerlessLLM — they create `sllm+c` (CPU-enabled) and `sllm+c+s` (time-sharing enabled) variants
   - They **manually tuned concurrency limits** for baselines (§IX-A): "we tried our best to conservatively tailor a set of higher concurrency limits" — this is unusually honest

4. **Ablation Study is Legitimate (Figure 23):**
   - Disabling sharing drops SLO rate to 89% — proving it's not just a minor optimization
   - GPU timeline shows consolidation prevents resource sprawl after load spikes

### Weaknesses

1. **The "Cherry-Pick" Check — Model Size Selection:**
   - All experiments use 3B/7B/13B models. The 34B model only appears in mixed deployment (§IX-E), where SLINFER admits it "falls back to exclusive GPU allocation" (0:0:0:1 case in Figure 26)
   - **Missing:** What about 20B models? 30B quantized models? The paper claims "small- to mid-sized" but never defines the boundary precisely
   - **Concern:** The CPU opportunity disappears for models >13B with long contexts (Figure 6 shows 34B violates SLO even on GPU for 4K inputs)

2. **The Baseline Validity — Is `sllm+c+s` a Fair Strawman?**
   - `sllm+c+s` uses **fixed 50% resource partitioning** — each instance gets half a GPU
   - But why 50%? Why not 33% or 25%? The paper says this is because "compute and memory shortages can easily occur" but doesn't explore optimal static partitioning
   - MuxServe [24] does static GPU sharing with profiling — why wasn't it compared?

3. **The "Zero-Event" Reality — How Often Do CPUs Actually Help?**
   - Figure 22 shows CPU usage. For 128 7B-sized models, SLINFER uses 4.0 CPUs + 4.0 GPUs
   - But `sllm+c` also uses 4.0 CPUs + 4.0 GPUs for the same workload
   - **Question:** In saturated conditions, what's the marginal benefit of SLINFER's CPU intelligence vs. just "use CPUs too"?

4. **Missing Network Overhead:**
   - When consolidation preempts requests and reschedules them across nodes (Figure 20b), what's the migration latency?
   - §IX-G mentions "100 Gbps cross-node bandwidth" but doesn't isolate network overhead in main experiments
   - For KV-cache migration of a 13B model with 32GB cache, 100 Gbps = 2.5 seconds minimum

5. **Limited Hardware Diversity:**
   - All experiments use A100-80GB GPUs and 4th-Gen Xeon CPUs
   - No H100, no A10g, no AMD MI300 — yet they claim "hardware-agnostic" (§V)
   - No evaluation on older CPUs (though Table I shows 3rd-Gen is 6.7× slower, suggesting SLINFER wouldn't work there)

6. **SLO Definition Favors the Approach:**
   - TTFT SLO = `min(max(0.5, L/512), 8)` seconds — this scales with input length
   - TPOT SLO = 250ms — quite generous (human reading speed is cited)
   - Under tighter SLOs (100ms TPOT), the paper admits "batch sizes limited to 9 for 1K-length" (§IV-A2)
   - **No experiments at 50ms or 100ms TPOT SLOs** — yet many production systems target these

---

## Q4: What the Authors Didn't Tell You

1. **The Cold-Start Grace Window Hides Real Problems:**
   - §IX-A: "we relax the TTFT requirement for such requests by allowing a grace window equal to the cold-start duration"
   - This means Figure 22's TTFT CDFs don't include cold-start latency as violations
   - How many requests hit cold-start? The paper never says. With 128 models and 1s keep-alive, this could be significant.

2. **The Profiling Cost is Non-Trivial:**
   - §VI-B: "SLINFER only needs to collect O(log L_max · log B_max) cases" — "a few hundred samples"
   - For each model × hardware type combination. With 128 models and 2 hardware types = 256 profiling sessions
   - Each session takes "minutes" — that's potentially hours of profiling per model deployment

3. **What Happens When Estimates Are Wrong?**
   - §VI-B claims 5.9% average TTFT error and 3.9% TPOT error
   - But §VI-C says they "overestimate each iteration by 10%" as a safety margin
   - §VII-D admits underestimation still happens: "SLINFER will evict and re-schedule the request with the longest headroom"
   - **How often does this eviction happen?** Never quantified except one mention: "0-0.3%" migration rate at 25% watermark (§IX-I5)

4. **The Consolidation Preemption Has Hidden Costs:**
   - §VIII-A: "requests of the preempted instances are then rescheduled to other nodes"
   - This requires serializing KV-cache, sending it over network, deserializing
   - Shadow validation must pass for preemption to occur — but if the cluster is congested enough to need preemption, won't shadow validation fail too?

5. **CPU Inference Engine Differences:**
   - GPU uses vLLM 0.5.2; CPU uses OpenVINO 2024.6.0
   - These have different batching behaviors, memory management, and overhead profiles
   - The "decode speed" comparison (Figure 22) might not be apples-to-apples

6. **The 4th-Gen Xeon Dependency is Underplayed:**
   - Table I shows 3rd-Gen Xeon is **6.7× slower** for TTFT
   - Most datacenters still run older CPUs — 4th-Gen launched in 2023
   - The paper's CPU sharing opportunity is contingent on hardware that isn't yet widely deployed

7. **Fragmentation Metric is Missing:**
   - §VIII describes consolidation to reduce fragmentation, but they never measure fragmentation directly
   - How many instances of the same model exist simultaneously? What's the weight duplication overhead?
   - Figure 23 shows "w/o Consolidation" uses more GPUs, but doesn't show *why* (fragmented instances? lower batch sizes?)

8. **The LongBench Results Reveal CPU Limits:**
   - §IX-I1: "For LongBench, however, CPUs cannot satisfy the long-sequence TTFT SLO"
   - Figure 35 shows SLINFER uses 3.4 GPUs vs 3.1 CPUs for LongBench — it's mostly falling back to GPUs
   - **Implication:** For long-context applications (RAG, document QA), the CPU opportunity largely disappears