# Study B — Rich Directive
**Paper:** 1030008 PASCAL  A Phase Aware Scheduling Algorithm for Serving Reasoning based Large Language Models  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:27

---

Q1: Whiteboard Explanation

If I were explaining PASCAL to a colleague at a whiteboard, I'd start with the fundamental problem it addresses.

**The Core Problem:**
Reasoning-based LLMs like DeepSeek-R1 generate two types of tokens during decoding: reasoning tokens (internal chain-of-thought, wrapped in `<think>...</think>` tags) followed by answering tokens (the actual user-visible response). The critical insight is that TTFT (Time-To-First-Token) for these models isn't just prefill latency—it's prefill PLUS the entire reasoning phase latency before the first answering token appears. This can be tens of seconds to minutes, fundamentally changing what TTFT optimization means.

**Why Existing Schedulers Fail:**
I'd draw the standard FCFS vs RR tradeoff diagram. Under GPU memory pressure:
- FCFS causes head-of-line blocking: short reasoning requests wait behind long ones, inflating TTFT by up to 5× for short requests
- RR reduces blocking but preempts long reasoning requests, fragmenting their execution and adding ~1.75× latency overhead

The key observation from their characterization: reasoning phase latency is extremely sensitive to interruptions (preemption or blocking directly adds to TTFT), while answering phase is threshold-sensitive—it just needs to be "fast enough" (meet TPOT targets around 100ms) and can tolerate preemption if token pacing smooths delivery.

**PASCAL's Two-Level Architecture:**

*Instance-level scheduler:*
- For NEW requests (reasoning phase): Route to instance with smallest KV cache footprint, but exclude instances already violating answering-phase SLOs
- At phase transition (when `</think>` detected): Migrate request to instance with fewest reasoning requests, enabling answering to proceed without interference from high-priority reasoning work
- Adaptive migration: Skip migration if current instance has sufficient memory but target doesn't

*Intra-instance scheduler:*
- High-priority queue for reasoning requests (RR within queue)
- Low-priority queue for answering requests (RR + token pacing)
- Reasoning always preempts answering for GPU memory

The elegance is treating the same physical decoding stage differently based on semantic phase, enabling phase-appropriate scheduling policies.

---

Q2: The Key Insight

The central insight is that **reasoning-based LLMs create an asymmetric sensitivity to scheduling decisions within what was previously a homogeneous decoding stage**. Specifically:

1. **Reasoning phase latency directly compounds into TTFT**: Every millisecond of blocking or preemption during reasoning adds directly to the time before users see any output. This makes reasoning latency-critical and interruption-intolerant.

2. **Answering phase has threshold semantics, not latency semantics**: Users don't care if answering takes 10s vs 12s total—they care whether tokens stream at ~100ms/token (human reading speed). This means answering is SLO-tolerant and can absorb preemption overhead if token pacing masks the gaps.

This asymmetry is non-obvious because both phases are autoregressive decoding with identical computational characteristics per token. The difference is purely semantic (user visibility) but has profound scheduling implications.

The authors correctly identify that prior work either ignores phases entirely (vLLM's FCFS) or applies uniform time-sharing (RR variants), neither of which exploits this asymmetry. PASCAL's contribution is recognizing that phase-aware priority inversion—treating reasoning as high-priority for GPU memory allocation while allowing answering to be preempted—can reduce tail TTFT by 72% without sacrificing answering-phase SLO attainment.

The hierarchical instance-level placement (Algorithm 1 for reasoning, Algorithm 2 for answering) extends this insight across multiple GPUs: reasoning requests should consolidate on memory-available instances, while answering requests should migrate away from reasoning-heavy instances at phase boundaries.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Appropriate characterization methodology**: The separation of reasoning-phase and answering-phase experiments (Figures 4-5) cleanly isolates the sensitivity differences. Using synthetic workloads with controlled token lengths before moving to real datasets is methodologically sound.

2. **Realistic workload construction**: Using OpenAI o4-mini API to generate reasoning/answering token distributions for AlpacaEval2.0 and Arena-Hard is clever—it captures realistic CoT behavior without requiring expensive inference runs. The 50% memory constraint simulates meaningful pressure.

3. **Comprehensive ablation studies**: The PASCAL(NoMigration) and PASCAL(NonAdaptive) variants (Figures 13, 15) demonstrate that both migration and adaptive migration contribute meaningfully—NoMigration shows 27.39s P99 blocking latency vs near-zero for full PASCAL.

4. **Tail latency focus with appropriate binning**: The variable percentile selection based on sample size (max for <10 samples, P90/P95/P99 scaling) is statistically appropriate for skewed distributions.

5. **KV transfer overhead analysis**: The authors directly address the potential overhead concern, showing P99 transfer latencies of 0.14-0.25s are negligible relative to multi-second reasoning latencies.

**Weaknesses:**

1. **Simulation-only evaluation**: The entire cluster-level evaluation uses a profile-based simulator. While they report 1.62% MAPE for end-to-end latency on a single-node validation, this doesn't validate the multi-instance scheduling dynamics. The 12.6% MAPE for mean TTFT is concerning for a paper focused on TTFT optimization.

2. **Limited model diversity**: Only DeepSeek-R1-Distill-Qwen-32B is evaluated. The claim that reasoning token counts are model-dependent and the scheduling benefits depend on reasoning/answering ratio asymmetry—yet there's no evaluation on other reasoning models (QwQ-32B, o1-mini, etc.).

3. **Token quantum sensitivity unexplored**: Fixed at 500 tokens with no sensitivity analysis. Given the wide variance in reasoning lengths (128-15000 tokens in Figure 8), quantum selection likely significantly impacts performance.

4. **SLO threshold selection**: The 0.95 QoE threshold for SLO violation and 100ms TPOT target are asserted based on prior work but not validated for reasoning model use cases specifically. Users might have different expectations when waiting through visible reasoning.

5. **Weak baseline comparison**: Comparing against vanilla FCFS and RR from vLLM rather than recent SLO-aware schedulers (Andes, Llumnix) that the related work acknowledges. The claim of novelty for phase-awareness is stronger, but practical improvement over state-of-art schedulers is undemonstrated.

6. **Limited scalability analysis**: Eight instances is modest. Load balancing heuristics (minimum KV footprint, minimum reasoning requests) may not scale to larger deployments with higher variance.

---

Q4: What the Authors Didn't Tell You

**Implementation Complexity Hidden:**
- The paper doesn't discuss how phase detection integrates with continuous batching. Detecting `</think>` requires decoding and checking every token, potentially adding overhead to the critical path.
- KV cache migration at phase boundaries requires coordination between instances. The paper assumes 100Gbps fabric but doesn't analyze contention when multiple requests transition simultaneously.

**Reasoning Model Evolution May Obsolete This:**
- The paper targets models with explicit `<think>` delimiters. Newer reasoning models (Claude 3.5, GPT-4o) may hide reasoning or use different mechanisms. Latent reasoning without explicit markers would require fundamentally different detection.
- Models like DeepSeek-R1 generate reasoning tokens that users can optionally view. If users start wanting to see reasoning (for trust/verification), the entire TTFT redefinition collapses.

**Memory Pressure Assumptions:**
- The 50% memory constraint is somewhat arbitrary. Real deployments vary widely in KV cache pressure depending on sequence lengths, batch sizes, and model sizes. The benefits scale with memory pressure, but actual deployment configurations aren't characterized.

**Throughput vs. Latency Tradeoff Not Fully Explored:**
- Figure 12 shows comparable throughput, but this is under the evaluated arrival rates. At higher utilization, prioritizing reasoning may starve answering requests indefinitely. The demotion threshold (5000 tokens) is configurable but the tradeoff space isn't characterized.

**Missing Multi-Tenancy Considerations:**
- Real deployments serve multiple users with potentially different SLO requirements. PASCAL's global reasoning prioritization may cause fairness issues—a user with a long reasoning request could delay other users' answering phases.

**Speculative Decoding Interaction:**
- Modern LLM serving increasingly uses speculative decoding. How PASCAL interacts with draft-verify cycles, especially when speculation occurs during reasoning vs. answering phases, is unexplored.

**The "Phase-Aware" Framing May Be Overselling:**
- PASCAL's core mechanism—priority queues with preferential GPU memory allocation for one class of work—is well-established in systems. The novelty is recognizing that CoT creates two semantic classes within decoding, not the scheduling mechanisms themselves.