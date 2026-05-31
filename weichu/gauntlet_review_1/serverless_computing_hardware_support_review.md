# Deconstruction: SLINFER (HPCA 2026)

## The "No-BS" Summary

This paper observes that serverless LLM hosting (think: cloud providers running thousands of fine-tuned 7B models for different customers) wastes GPUs catastrophically because existing systems give each model an entire GPU even when it's serving 3 requests per hour. Their solution: **treat CPUs with Intel AMX as first-class inference devices for small models, and dynamically share both CPU and GPU resources across multiple model instances at token-level granularity.** The core trick is a scheduler that predicts per-token compute time precisely enough to interleave multiple models on the same hardware without violating latency SLOs. They claim 47-154% improvement in serving capacity on a 4-GPU + 4-CPU testbed.

---

## The Core Mechanism: A Whiteboard Explanation

**The Problem Setup:**
Imagine you're Azure, hosting 128 different fine-tuned Llama-7B models. Each model gets maybe 5 requests/hour on average, but occasionally one model gets a burst of 50 concurrent requests. Traditional serverless LLM systems (like ServerlessLLM) give each active model its own GPU. With 4 GPUs and 128 models, you're constantly swapping models in and out, and 33% of requests miss their SLO just waiting in queue.

**SLINFER's Three-Part Trick:**

### 1. **CPUs Are Actually Useful Now (The AMX Revelation)**
Intel's 4th-gen Xeons have AMX (Advanced Matrix Extensions)—basically a baby tensor core baked into the CPU. The paper shows that for models ≤13B parameters with inputs ≤4K tokens, a 32-core Xeon can hit the same TTFT/TPOT SLOs that production systems require (0.5-8s for first token, 250ms per subsequent token). 

*The insight:* GPU nodes have dozens of idle CPU cores. Instead of letting them rot, use them to serve the long tail of low-traffic models, reserving GPUs for the hot models with bursty traffic.

### 2. **Token-Level Time-Slicing (The "Headroom" Scheduler)**
Here's where it gets clever. Instead of giving each model instance exclusive access to hardware, SLINFER interleaves them at the granularity of *individual token generations*. 

The scheduler maintains a "headroom" metric for each request:
```
headroom = (start_time + TTFT_SLO + TPOT_SLO × tokens_generated) - current_time
```

This tells you: "How much slack does this request have before it violates its SLO?" At each scheduling cycle, SLINFER picks the instance with the *shortest* headroom (most urgent) and lets it generate one token. Then it re-evaluates.

*Why this works:* LLM inference has wildly variable compute per token. Prefill (first token) for a 1K-input request takes 567ms on CPU; decode (subsequent tokens) takes 71ms. By scheduling at token granularity, you can pack multiple models onto one device without any single model hogging resources during its expensive prefill phase.

### 3. **Memory Orchestration (The "Don't OOM" Dance)**
Multiple models sharing a GPU means their KV-caches are constantly growing and shrinking. Naive dynamic resizing causes OOM crashes when two models try to expand simultaneously.

SLINFER uses:
- **Watermark-based scaling:** Don't resize on every request. Scale up to 125% of current need (buffer for bursts), scale down only when usage drops below 80% of allocation.
- **Optimistic/Pessimistic budgeting:** Track two memory numbers—"optimistic" (what we've promised) and "pessimistic" (what's actually freed). Scale-ups wait in a reservation station until scale-downs actually complete.

---

## The Critique

### Why It Got Into HPCA

1. **The AMX characterization is genuinely useful.** Table I showing 6.7× TTFT speedup from 3rd to 4th gen Xeon is the kind of data that practitioners need. The paper does the legwork of profiling when CPUs can/can't meet SLOs across model sizes and sequence lengths.

2. **The headroom scheduler is elegant.** Using SLO slack as the scheduling priority is a clean abstraction that naturally handles the heterogeneity of prefill vs. decode, different model sizes, and mixed CPU/GPU deployments.

3. **The problem is real and timely.** Figure 4 showing ServerlessLLM failing at 64+ models despite 23% average GPU memory utilization is a compelling motivation. The serverless LLM hosting market is exploding, and this is a genuine pain point.

4. **Solid experimental methodology.** They use real traces (Azure Functions + Azure LLM), test across multiple model sizes, and include ablations. The 4-CPU + 4-GPU testbed is modest but sufficient to demonstrate the mechanisms.

### Where It's Weak

1. **The CPU story has serious caveats they downplay:**
   - Only works with AMX-equipped CPUs (4th gen Xeon or newer—most datacenters still run older hardware)
   - Limited to ≤13B models with ≤4K inputs and ≥250ms TPOT SLO
   - Figure 6 shows 13B models *barely* meeting SLO at 2K tokens; at 4K they're already at 2.7s TTFT (SLO is ~8s, so little headroom)
   - **What they don't say:** If your SLO is 100ms TPOT (common for interactive apps), CPUs are useless even for 7B models

2. **The evaluation scale is concerning:**
   - 4 GPUs, 4 CPUs, 128 models max
   - Real cloud deployments have thousands of GPUs and tens of thousands of models
   - Their consolidation mechanism (§VIII) relies on global coordination—how does this scale?
   - The "shadow validation" requires simulating future token generations across all instances on a node. At 1000 concurrent instances, this becomes expensive.

3. **The baseline comparison is generous:**
   - They compare against ServerlessLLM with *their own tuned* concurrency limits (Table in §IX-A)
   - The `sllm+c+s` baseline uses static 50% partitioning, which is obviously suboptimal. A fairer comparison would be against MuxServe or other dynamic GPU sharing systems.
   - No comparison against speculative decoding, continuous batching optimizations, or other orthogonal techniques

4. **Memory fragmentation is hand-waved:**
   - Figure 17 shows KV-cache resizing takes 0.3-1.9 seconds. During this time, what happens to incoming requests?
   - The watermark mechanism (§VII-B) trades memory efficiency for stability, but they don't quantify how much memory is wasted by the 25% buffer
   - No discussion of GPU memory fragmentation from repeated allocations/deallocations

5. **The "consolidation" mechanism (§VIII) is underspecified:**
   - Proactive preemption requires migrating requests to other nodes. What's the latency cost?
   - They claim "shadow validation ensures preempted requests can still meet SLOs after rescheduling"—but if the cluster is congested enough to need preemption, where do these requests go?
   - The bin-packing reactive strategy assumes you can predict which instances will finish soon. With variable output lengths, this is noisy.

6. **Missing real-world concerns:**
   - No discussion of multi-tenancy isolation. If Model A and Model B share a GPU, can A's malicious input cause B to miss SLOs?
   - No power/energy analysis. CPUs running AMX workloads consume significant power—is this actually cost-effective vs. just buying more GPUs?
   - No discussion of model loading/unloading latency in the critical path. They use ServerlessLLM's loader but don't account for its overhead in their SLO calculations.

---

## Discussion Questions

1. **On scalability:** "Your shadow validation simulates future token generations for all instances on a node. With 8 instances per GPU (your max in Figure 28), this is tractable. But MuxServe reports 16+ models per GPU in production. At what point does the scheduling overhead dominate, and have you measured this?"

2. **On the CPU value proposition:** "Table I shows 4th-gen Xeon is 6.7× faster than 3rd-gen for TTFT. But 3rd-gen Xeons are still the majority of deployed CPU capacity. Your system falls back to GPU-only mode for older CPUs—doesn't this mean SLINFER provides zero benefit for most existing infrastructure?"

3. **On memory safety:** "Your pessimistic budgeting (§VII-C) prevents OOM by blocking scale-ups until scale-downs complete. But Figure 17 shows scale-downs take 0.3s. If a burst of requests arrives during this window, they queue. Have you measured the tail latency impact of this blocking, especially under the bursty workloads in Figure 12?"

4. **On the threat model:** "You co-locate multiple customer models on shared hardware. A malicious user could craft inputs that maximize prefill time (long sequences) or KV-cache growth (long outputs), deliberately starving other models of resources. How does your headroom scheduler handle adversarial workloads?"

5. **On alternative designs:** "DistServe (OSDI'24) argues that prefill-decode disaggregation improves efficiency. Your Table III shows it hurts in serverless settings because prefill instances sit idle. But what if you disaggregated at the *cluster* level—dedicated prefill nodes shared across all models, with decode distributed? Have you explored this middle ground?"

---

## Contextual Fit

This paper sits at the intersection of two trends:

1. **Serverless LLM serving:** Following ServerlessLLM (OSDI'24), Medusa (ASPLOS'25), and DeepServe (ATC'25), which focus on cold-start optimization but assume exclusive GPU allocation.

2. **GPU multiplexing for ML:** Following MuxServe (ICML'24), which does spatial-temporal multiplexing but assumes predictable workloads, and Llumnix (OSDI'24), which does dynamic request migration but within a single model.

SLINFER's contribution is combining these with heterogeneous CPU/GPU scheduling. The AMX angle is novel—most prior work treats CPUs as auxiliary (NEO, FastDecode) rather than independent inference devices.

**What's missing from the related work:** No comparison to NVIDIA's MPS (Multi-Process Service) or MIG (Multi-Instance GPU), which are the industry-standard approaches to GPU sharing. The paper's `sllm+c+s` baseline uses "time-sharing" but doesn't clarify if this is MPS-based or something simpler.

---

## Bottom Line

This is a solid systems paper that identifies a real inefficiency (GPU underutilization in serverless LLM hosting) and proposes a reasonable solution (heterogeneous CPU/GPU sharing with token-level scheduling). The AMX characterization is valuable, and the headroom scheduler is a clean design.

However, the evaluation is small-scale, the CPU applicability is narrower than the paper suggests, and several practical concerns (adversarial workloads, memory fragmentation, scheduling overhead at scale) are unaddressed. A student reading this should understand the *mechanism* but remain skeptical of the *generality* of the results.