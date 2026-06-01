# PASCAL Paper Analysis: A Toolsmith's Perspective

## Q1: Whiteboard Explanation

Alright, let me break down what PASCAL is actually doing here.

**The Problem Setup:**
Reasoning-based LLMs like DeepSeek-R1 don't just spit out answers—they first "think" through a chain-of-thought, generating potentially thousands of reasoning tokens before producing the actual answer. The key insight is that users don't see these reasoning tokens; they're hidden behind a `<think>` tag. So TTFT (Time-To-First-Token) for these models isn't just the prefill latency—it's prefill PLUS all the reasoning tokens PLUS the first answer token.

**The Core Observation:**
The authors ran characterization studies (Figures 4 and 5) and discovered an asymmetry:
- **Reasoning phase**: Extremely latency-sensitive. Any blocking or preemption directly inflates TTFT. A short reasoning request blocked behind a long one can see 5.14× latency inflation (Figure 4).
- **Answering phase**: Threshold-sensitive, not latency-sensitive. As long as you meet the TPOT SLO (e.g., 100ms/token), users don't care if there's some preemption. Round-robin scheduling achieves near-oracle SLO attainment even with preemptions (Figure 5b).

**PASCAL's Solution:**
A two-level hierarchical scheduler:

1. **Instance-level scheduler**: Routes requests across multiple GPU instances. Algorithm 1 places reasoning requests on instances with minimum KV cache footprint. Algorithm 2 migrates requests at phase boundaries to instances with fewest reasoning requests.

2. **Intra-instance scheduler**: Maintains a high-priority queue (reasoning) and low-priority queue (answering). Reasoning always preempts answering. Both queues use round-robin internally, with a token pacer for answering to smooth perceived output rates.

The magic is in the **phase transition detection**—when the model emits `</think>`, PASCAL can migrate the request to a less-loaded instance, rebalancing the system.

---

## Q2: The Key Insight

**The fundamental insight is that reasoning and answering phases have fundamentally different performance sensitivities, and existing schedulers that treat all decoding tokens uniformly leave significant optimization on the table.**

Specifically: reasoning latency is *interruption-sensitive* (any delay directly hurts TTFT), while answering QoE is *threshold-sensitive* (you just need to be "fast enough" for human reading speed).

This is non-obvious because both phases execute the same underlying computation—autoregressive decoding through attention layers. The insight comes from recognizing that the *user-visible semantics* differ: reasoning tokens are hidden, so we should sprint through them; answer tokens are streamed, so we just need to maintain a comfortable pace.

**What makes this clever:** The authors exploit this asymmetry to enable aggressive preemption of answering requests (which prior work avoided) to protect reasoning requests. This inverts conventional wisdom that preemption hurts user experience—it only hurts *answering* user experience, and only if you violate the TPOT threshold.

**The architectural implication:** Phase-aware scheduling is orthogonal to existing optimizations like disaggregated prefill/decode (DistServe) or KV cache compression. You could layer PASCAL on top of these systems for additional gains.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Appropriate simulator validation** (Section V-A): They report MAPE of 1.62% for end-to-end latency, 12.6% for mean TTFT, and 6.49% for TPOT against real H100 measurements. This is honest—they don't hide behind a single aggregate number.

2. **Comprehensive ablation studies**: Figure 13 isolates the migration benefit (PASCAL vs. PASCAL(NoMigration)), Figure 15 isolates adaptive migration. This lets you understand which components contribute what.

3. **Varied workload characterization**: They use both chat-style datasets (AlpacaEval2.0, Arena-Hard with long answers, Figure 8) and reasoning-heavy datasets (MATH-500, GPQA, LiveCodeBench with short answers, Figure 14). Section V-D honestly admits diminished benefits on reasoning-heavy workloads.

4. **Realistic SLO definitions**: Following prior work [54], they set TTFAT to 0.25s and TPOT to 100ms (Section III-A footnote), grounded in human reading speed research.

### Weaknesses

1. **Simulation abstractions are concerning**: The paper uses a "profile-based" simulator (Section V-A) rather than cycle-accurate simulation. They model eight H100 instances with 100Gbps fabric—but don't validate network contention modeling. The KV cache transfer overhead analysis (Section V-C) reports P99 latencies of 0.14-0.25s, but this assumes no PCIe contention, no NUMA effects, and uniform bandwidth availability.

2. **No real system implementation**: Everything is simulated. The claim that "PASCAL can be layered on top of existing systems" (Section VI) is unsubstantiated. Key questions remain: What's the phase detection latency? How does the token pacer interact with actual network RTT? Can the instance monitor actually keep up with per-token phase checks at 30ms/token?

3. **Memory model simplifications**: They cap GPU memory at "50% of oracle capacity" (Section III-A) without justifying this as representative. Real systems have fragmentation, memory allocator overhead, and competing processes. The KV cache footprint metric (Algorithm 1, line 7) sums GPU+CPU memory but doesn't account for transfer overhead differences.

4. **Workload generation methodology**: Traces are built by querying OpenAI's o4-mini API (Section V-A), which means reasoning/answering token counts reflect o4-mini's behavior, not DeepSeek-R1-Distill-Qwen-32B's actual distribution. This is a significant validity threat—different models have different thinking styles.

5. **Missing sensitivity analysis**: Token quantum is fixed at 500 (Section V-A), demotion threshold at 5000 tokens. No exploration of how these hyperparameters affect results across different workloads.

---

## Q4: What the Authors Didn't Tell You

1. **The o4-mini trace generation is a red flag.** They use OpenAI's model to generate token counts, then simulate DeepSeek's behavior with those counts. But reasoning length distributions are model-specific—DeepSeek-R1 was explicitly trained with RL to develop its own reasoning patterns (cited [16]). Using o4-mini traces to evaluate a DeepSeek-optimized scheduler is methodologically questionable.

2. **Phase detection is assumed instantaneous and perfect.** The paper assumes they can detect `</think>` tokens with zero latency and 100% accuracy. In reality, token generation is asynchronous, there may be network delays between the inference engine and the scheduler, and malformed outputs (no `</think>`, multiple `</think>`) aren't discussed.

3. **The adaptive migration decision (Section IV-B) has race conditions.** Figure 7 shows the adaptive override logic, but what happens when multiple requests transition simultaneously? The paper says "if the current instance has sufficient GPU memory while the selected one does not" (page 8), but memory state is non-monotonic—a request could allocate memory between the check and the migration decision.

4. **CPU memory is treated as infinite.** Preempted KV caches are "offloaded to CPU memory" (Section II-B), but CPU memory is finite. At high load with many preempted requests, you could hit CPU memory limits, but this scenario isn't evaluated.

5. **The QoE metric modification is buried.** In Section V-A, they casually mention: "we instead compute QoE solely from TPOT and evaluate TTFT separately." This decouples the metric from its original definition [31], making comparisons to prior work on QoE-aware scheduling problematic.

6. **No discussion of model accuracy impact.** The demotion policy (reasoning requests >5000 tokens get demoted to low-priority queue, Section IV-C) could cause reasoning requests to be preempted before completing their reasoning, potentially affecting model accuracy. This is never measured.

7. **The 8-instance cluster is small.** Real inference clusters have hundreds of GPUs. The instance-level scheduling algorithms (Algorithms 1 and 2) are O(N) scans, which is fine for 8 instances but may not scale. No complexity analysis or scalability evaluation is provided.