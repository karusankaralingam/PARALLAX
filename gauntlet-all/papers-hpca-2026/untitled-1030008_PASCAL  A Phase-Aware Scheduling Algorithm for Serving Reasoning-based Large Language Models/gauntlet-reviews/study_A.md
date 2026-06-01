# Study A — Simple Directive
**Paper:** 1030008 PASCAL  A Phase Aware Scheduling Algorithm for Serving Reasoning based Large Language Models  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:27

---

Q1: Whiteboard Explanation

If I were explaining PASCAL at a whiteboard, I'd start by drawing the key difference between conventional and reasoning-based LLMs.

**The Problem Setup:**
In conventional LLMs, Time-To-First-Token (TTFT) equals just the prefill latency—process the prompt, emit the first token. Simple. But reasoning models like DeepSeek-R1 first generate hundreds or thousands of "thinking" tokens (marked by `<think>...</think>`) before producing the actual answer. So TTFT now includes: prefill + ALL reasoning tokens + first answer token. This dramatically inflates TTFT.

**The Key Observation:**
Here's what the authors discovered through characterization: the reasoning phase and answering phase have fundamentally different sensitivities to scheduling decisions.

*Reasoning phase*: Any blocking or preemption directly adds to TTFT. If you pause a reasoning request, the user waits longer for their first visible token. This is latency-sensitive—minimize absolute delay.

*Answering phase*: Only needs to be "fast enough." Users read at ~10 tokens/second. As long as you meet that threshold, you can tolerate some preemption without degrading user experience. This is threshold-sensitive, not absolute-latency-sensitive.

**PASCAL's Design:**
The system has two levels:

1. *Instance-level scheduler*: Routes requests across multiple GPU instances. Algorithm 1 places reasoning requests on instances with the smallest KV cache footprint. Algorithm 2 migrates requests at phase boundaries to instances with fewer reasoning requests (to avoid interference).

2. *Intra-instance scheduler*: Uses hierarchical priority queues. Reasoning requests go to a high-priority queue (scheduled first, get GPU memory first). Answering requests go to a low-priority queue with round-robin + token pacing to smooth output.

The result: TTFT drops up to 72% while maintaining answering-phase SLO compliance.

Q2: The Key Insight

The central insight is the **asymmetric sensitivity** of reasoning versus answering phases to scheduling interference in reasoning-based LLMs.

While existing LLM schedulers treat all decoding tokens uniformly, PASCAL recognizes that reasoning tokens are "latency-sensitive" (any delay directly inflates TTFT, which now spans the entire reasoning chain), whereas answering tokens are "threshold-sensitive" (they only need to meet a minimum rate for acceptable user experience). This asymmetry means that aggressive preemption is catastrophic for reasoning but tolerable for answering.

This insight enables a simple but effective design: strictly prioritize reasoning-phase requests to run uninterrupted, while using time-sharing with token pacing for answering-phase requests. The phase boundary (detected by the `</think>` token) becomes a natural migration point for load balancing across instances.

The insight is compelling because it's grounded in how users actually experience these systems—they don't see reasoning tokens, so they experience reasoning delay as pure wait time, but they actively consume answering tokens where meeting reading speed is sufficient.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**
1. *Comprehensive characterization*: Figures 4-5 clearly demonstrate the asymmetric behavior of reasoning vs. answering phases under different scheduling policies, building strong motivation.
2. *Multiple workload types*: Evaluation spans chat-focused (AlpacaEval2.0, Arena-Hard) and reasoning-heavy (MATH-500, GPQA, LiveCodeBench) datasets.
3. *Ablation studies*: NoMigration and NonAdaptive variants isolate contributions of migration and adaptive policies.
4. *Practical metrics*: Using QoE (threshold-based SLO) rather than just average TPOT better reflects real user experience.

**Weaknesses:**
1. *Simulation-based evaluation*: While validated against real hardware, the cluster-level results are simulated. Real deployment could reveal unforeseen issues (e.g., migration timing, network contention).
2. *Limited model diversity*: Only DeepSeek-R1-Distill-Qwen-32B evaluated. Different reasoning models may have different phase characteristics.
3. *Synthetic trace construction*: Using o4-mini API to generate reasoning/answering token counts for traces may not capture real production patterns.
4. *Fixed parameters*: Token quantum (500) and demotion threshold (5000) seem hand-tuned; sensitivity analysis is missing.
5. *No comparison with recent SLO-aware schedulers*: Only FCFS and RR baselines; missing comparison with Andes or other sophisticated schedulers.

Q4: What the Authors Didn't Tell You

**Implementation complexity:** The paper assumes phase detection is reliable via token matching (`</think>`), but real reasoning models may have variations, malformed outputs, or streaming parsing challenges. Production systems would need robust tokenization and error handling.

**Prediction problem:** PASCAL is reactive—it detects phase transitions after they happen. It cannot predict reasoning length upfront, which limits optimal instance placement. The adaptive migration policy is a workaround, not a solution.

**Multi-tenancy challenges:** The evaluation uses homogeneous workloads. In real deployments with mixed model types or priority levels, the hierarchical queue design may cause starvation for certain workloads.

**KV cache transfer costs at scale:** While the paper claims transfer overhead is "negligible" relative to reasoning latency, this assumes sufficient network bandwidth. With many simultaneous migrations, bandwidth contention could become significant.

**Memory fragmentation:** Prioritizing reasoning requests for GPU memory, combined with variable reasoning lengths and migrations, could cause significant KV cache fragmentation over time.

**Generalization to future models:** As reasoning models improve, reasoning phases may become shorter or more predictable, potentially diminishing PASCAL's benefits. The approach is somewhat coupled to current model behavior.