# Study C — Multi-Persona Synthesis
**Paper:** 1030008 PASCAL  A Phase Aware Scheduling Algorithm for Serving Reasoning based Large Language Models  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 07:39

---

# Q1: Whiteboard Explanation

Reasoning LLMs like DeepSeek-R1 and OpenAI's o-series fundamentally change the inference pipeline. Unlike traditional LLMs where TTFT equals prefill latency, these models generate potentially thousands of hidden "thinking" tokens (wrapped in `<think>...</think>` tags) before producing any user-visible output. The user stares at a blank screen during this entire reasoning phase.

**The Core Problem:**
```
Traditional LLM:
[Prefill] → t1 → t2 → t3 → ... → done
           ↑
         TTFT

Reasoning LLM:
[Prefill] → r1 → r2 → r3 → </think> → t1 → t2 → t3 → ... → done
                              ↑
                            TTFT (now includes ALL reasoning tokens!)
```

GPU memory is finite, and when multiple requests compete for KV cache space, the system must either **block** new requests or **preempt** existing ones. The paper's key characterization (Figures 4 and 5) reveals an asymmetry:

- **Reasoning phase**: Latency-sensitive. Any blocking or preemption directly inflates TTFT. Figure 4 shows up to 5.14× latency inflation under FCFS for short reasoning requests (128 tokens), and 1.75× overhead under RR for long requests (2048 tokens).
- **Answering phase**: Threshold-sensitive. Users only need tokens "fast enough" (~100ms TPOT for human reading speed). Figure 5(b) shows RR achieves near-oracle SLO attainment despite causing 2× longer absolute latency—preemption hiccups can be absorbed by buffering.

**PASCAL's Two-Level Architecture (Figure 6):**

```
Instance-Level Scheduler
    ├── Algorithm 1: Route reasoning → instance with smallest KV footprint
    └── Algorithm 2: Migrate at </think> → instance with fewest reasoning requests
         ↓
Intra-Instance Scheduler (per GPU)
    ┌─────────────────────────┐
    │ HIGH-PRIORITY QUEUE     │ ← Reasoning (never preempted by answering)
    ├─────────────────────────┤
    │ LOW-PRIORITY QUEUE      │ ← Answering (RR + token pacer for smoothing)
    └─────────────────────────┘
```

The magic is **phase detection via token snooping**: when `</think>` appears, the request transitions from high to low priority, and the instance-level scheduler evaluates whether to migrate it to a less-loaded instance. An adaptive override (Figure 7) prevents migration when the current instance has free GPU memory but the target doesn't.

# Q2: The Key Insight

**The fundamental insight is that reasoning and answering phases have asymmetric sensitivity to scheduling interference, and existing schedulers that treat all decoding tokens uniformly leave significant optimization on the table.**

This is non-obvious because both phases execute identical underlying computation—autoregressive decoding through attention layers. The insight comes from recognizing that *user-visible semantics* differ: reasoning tokens are hidden (sprint through them), while answer tokens are streamed (just maintain comfortable pace).

**Why this matters:** Existing schedulers (FCFS, RR, even sophisticated ones like Andes) apply the same policy to both phases. PASCAL inverts conventional wisdom that preemption hurts user experience—it only hurts *answering* user experience, and only if you violate the TPOT threshold. This enables aggressive preemption of answering requests to protect reasoning requests.

**The structural delta from baselines:**
1. A hierarchical 2-queue structure per instance (high/low priority)
2. Phase detection logic (token pattern matching for `</think>`)
3. Inter-instance migration at phase boundaries with adaptive override

**What's genuinely novel vs. standard techniques:** The priority queue and round-robin are standard OS scheduling. The migration heuristics are simple (queue occupancy, memory footprint). The *application* of phase-awareness to reasoning LLMs is the contribution—recognizing that the CoT revolution fundamentally changes what "TTFT" means.

The phase boundary is *observable*—the `</think>` token explicitly marks the transition. This isn't predicting future behavior (which is hard); it's reacting to a clear signal in the token stream. This is orthogonal to existing optimizations like disaggregated prefill/decode or KV cache compression.

# Q3: Evaluation Critique

## Strengths

**1. Methodologically Sound Characterization (Section III):** The controlled experiments isolating reasoning vs. answering phase behavior (Figures 4 and 5) are well-designed. Comparison against an "oracle" (unlimited GPU memory) provides a meaningful upper bound. This is data-driven motivation, not hand-waving.

**2. Comprehensive Ablation Studies (Section V-D):**
- PASCAL(NoMigration) shows migration is critical—P99 blocking latency reaches 27.39 seconds without it (Figure 13c)
- PASCAL(NonAdaptive) demonstrates blind migration is harmful—SLO violations spike from 0.69% to 7.45% (Figure 15)
These decompose the contribution of each design choice.

**3. Simulator Validation:** Section V-A reports MAPE of 1.62% for end-to-end latency, 12.6% for mean TTFT, and 6.49% for TPOT against real H100 measurements. While simulation-based evaluation has limitations, they follow established methodology from prior work.

**4. Honest Disclosure of Limitations:** Section V-D acknowledges that reasoning-heavy, short-answer workloads (MATH-500, GPQA, LiveCodeBench) reduce PASCAL's benefits. They test these scenarios rather than hiding them.

**5. Tail Latency Focus:** Up to 72% reduction in tail TTFT on Arena-Hard vs. FCFS (Figure 10), with absolute improvements of 64 seconds.

## Weaknesses

**1. Simulation-Only at Scale:** Despite having H100 access (Section III), the 8-instance cluster results are entirely simulated. Real systems have PCIe contention, CUDA stream scheduling, memory fragmentation, and network congestion that simulators miss. The KV cache transfer latencies (0.14-0.25s P99, Section V-C) are simulation outputs, not measured values.

**2. Workload Construction Issues:** Traces are built by querying OpenAI's o4-mini API (Section V-A), but reasoning length distributions are model-specific—DeepSeek-R1 was trained with RL to develop its own reasoning patterns. Using o4-mini traces to evaluate a DeepSeek-optimized scheduler is methodologically questionable.

**3. Limited Baseline Comparison:** Only FCFS and vanilla RR are compared. Missing: Andes [31] (QoE-aware scheduling), Llumnix [44] (priority-aware migration), FastServe [48] (deadline-aware), DistServe [54]. These are cited in Related Work but not benchmarked.

**4. Single Model, Single Scale:** All experiments use DeepSeek-R1-Distill-Qwen-32B on 8×H100 instances. No evaluation of larger models (70B+), smaller instances (A100-40GB), or tensor-parallel deployments where KV cache migration crosses NVLINK domains.

**5. Fixed Hyperparameters Without Sensitivity Analysis:** Token quantum is fixed at 500, demotion threshold at 5000 tokens (Section V-A). No exploration of how these interact with different workloads.

**6. Cherry-Picked Primary Workloads:** AlpacaEval2.0 and Arena-Hard have reasoning-to-answering ratios near 1:1 (Figure 8). Real reasoning deployments for math/coding tasks have ratios of 4:1 to 8:1 (Figure 14), where PASCAL's advantages shrink substantially.

# Q4: What the Authors Didn't Tell You

**1. KV Cache Migration Cost is Hand-Waved:** Section IV-B claims migration overhead is "negligible" with 40ms transfer for 2048 tokens. But under high load with simultaneous migrations, they observe 0.25s P99 transfer latency (Section V-C)—6× higher. More critically, migration requires synchronization: the source instance must stop generating tokens, serialize the KV cache, and wait for ACK. This creates bubbles affecting *all* requests on that instance, not just the migrating one. Bandwidth contention when multiple instances migrate simultaneously is mentioned but not quantified.

**2. Phase Detection Assumed Perfect and Free:** The paper assumes `</think>` detection is instantaneous with zero overhead and 100% accuracy. In reality: token detokenization is asynchronous, the scheduler must inspect every generated token, and failure modes (no `</think>`, multiple `</think>`, false positives in code generation) aren't discussed.

**3. The Demotion Policy is a Hack:** Section IV-C mentions reasoning requests exceeding 5000 tokens are demoted to low-priority. This hardcoded threshold contradicts their claim that reasoning should never be preempted, has no theoretical justification, and admits their high-priority queue can't handle adversarial workloads. Under sustained high arrival rates, answering requests could be indefinitely starved—the demotion policy is a band-aid, not a principled solution.

**4. The QoE Metric Modification is Convenient:** Section V-A states they "compute QoE solely from TPOT and evaluate TTFT separately" because reasoning LLMs have "highly variable reasoning lengths." The original QoE definition from Andes [31] includes TTFT. By separating them, they avoid showing combined degradation—a request with 200s TTFT but perfect TPOT looks great under their metric, terrible under the original.

**5. Throughput is Flat—But Why?** Figure 12 shows PASCAL achieves throughput "within 3%" of baselines. Given that PASCAL adds instance-level scheduling overhead, KV cache migration, and priority queue management, where does the overhead go? Either scheduling decisions are negligible (good), or the simulation doesn't capture real scheduling latency (suspicious). No profiling of scheduler overhead is provided.

**6. Multi-Turn Conversations Ignored:** Real deployments involve multi-turn sessions where KV cache from previous turns must be preserved, reasoning in turn N may depend on context from turn N-1, and instance affinity becomes important (migration breaks cache locality). The evaluation uses single-turn requests only.

**7. Why Not Disaggregate?** The authors dismiss DistServe-style disaggregation in Section VII because "both phases belong to the same decoding stage." But given that PASCAL already migrates requests at phase boundaries, dedicated reasoning/answering instances would eliminate inter-phase interference entirely. This natural extension isn't benchmarked.

**8. The Adaptive Migration Heuristic is Fragile:** Algorithm 2 selects instances based on queue occupancy, but the paper admits the scheduler "cannot foresee future memory contention due to unpredictable output lengths." The adaptive migration (Figure 7) is reactive rather than predictive. Race conditions when multiple requests transition simultaneously aren't addressed—memory state is non-monotonic between check and migration decision.