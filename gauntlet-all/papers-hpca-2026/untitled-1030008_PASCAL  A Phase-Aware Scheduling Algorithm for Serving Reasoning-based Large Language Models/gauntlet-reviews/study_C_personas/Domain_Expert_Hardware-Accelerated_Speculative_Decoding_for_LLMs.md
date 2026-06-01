# Paper Deconstruction: PASCAL

## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Forget the jargon for a moment.

**The Problem Setup:**
Reasoning LLMs like DeepSeek-R1 don't just spit out answers. They first generate a long internal monologue (reasoning tokens, often thousands of them) before producing the actual answer you see. The user is sitting there waiting, watching a blank screen, until that reasoning phase finishes and the `</think>` token appears. Only *then* do answering tokens start streaming.

**The Core Realization:**
In a traditional LLM, TTFT (Time-To-First-Token) equals "how long did prefill take?" Simple. But for reasoning LLMs, TTFT equals "prefill latency + time to generate ALL those hidden reasoning tokens + time to get the first answer token scheduled." That's potentially tens of seconds to over a minute of waiting.

**The Key Insight (Figure 4 vs Figure 5):**
The authors ran experiments asking: "What happens if we interrupt (preempt) a request during reasoning versus during answering?"

- **Reasoning phase:** If you preempt or block a request here, the user's wait time increases 1:1. Every second of delay adds a second to their TTFT. This is *latency-sensitive*.
- **Answering phase:** Users are reading tokens as they stream. If you preempt briefly, they don't notice as long as the *average* token rate stays above human reading speed (~10 tokens/sec). You can tolerate hiccups. This is *threshold-sensitive*.

**The Solution (PASCAL):**
Build a two-level scheduler that treats these phases differently:

1. **Intra-instance:** Maintain two queues. High-priority queue for reasoning requests—they get first dibs on GPU memory. Low-priority queue for answering requests—they get whatever's left and can be preempted.

2. **Inter-instance:** When a request's reasoning phase ends (detected by the `</think>` token), potentially *migrate* it to a different GPU instance that has fewer reasoning requests competing for resources.

3. **Adaptive migration:** Don't blindly migrate. If the current instance has free GPU memory, stay put rather than incur transfer overhead to a congested destination.

The whole thing boils down to: "Protect reasoning from interruption at all costs; let answering fight over scraps—it can handle it."

---

## Q2: The Key Insight

**The Real Delta:**
The genuine contribution is the *observation* that reasoning and answering phases have fundamentally different SLO sensitivities, and the *demonstration* that existing schedulers (FCFS, Round-Robin) are blind to this distinction. Prior work on LLM serving optimization (vLLM, ORCA, Andes, DistServe) focused on the prefill/decode split in conventional LLMs, not the reasoning/answering split *within* the decode phase itself.

**The Mechanism (The "Trick"):**
1. **Phase detection via token snooping:** The system monitors for the special `</think>` token (Section IV-B, Figure 6). This is computationally trivial—you're just checking each generated token against a known delimiter.

2. **Priority queue segregation:** Rather than one global scheduler, each instance runs a hierarchical queue (Section IV-C). Reasoning requests live in a high-priority queue with preferential GPU memory allocation. Answering requests are demoted to a low-priority queue where round-robin preemption is tolerable.

3. **Migration at phase boundaries:** When reasoning completes, Algorithm 2 evaluates whether the request should move to an instance with fewer competing reasoning requests. The adaptive migration policy (Section IV-B, Figure 7) adds a check: if the current instance has free GPU memory but the target doesn't, cancel the migration.

**What's Genuinely Novel vs. Standard Techniques:**
- The priority queue and round-robin within queues? Standard OS scheduling, nothing new.
- The migration decision algorithms? Simple heuristics based on queue occupancy and memory footprint.
- The *application* of phase-awareness to reasoning LLMs? That's the contribution. The authors correctly identify that the CoT revolution fundamentally changes what "TTFT" means, and existing systems haven't adapted.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Clean Characterization (Figures 4 and 5):**
The controlled experiments isolating reasoning vs. answering phase behavior are well-designed. Figure 4 shows reasoning latency increases up to 5.14× under FCFS due to head-of-line blocking for short requests. Figure 5(b) shows answering phase SLO attainment under RR remains high (often matching Oracle) even with preemption overhead. This directly supports their core claim about asymmetric sensitivity.

**2. Ablation Studies That Matter (Figures 13, 15):**
- PASCAL(NoMigration) shows migration is important—P99 "blocking latency" (time from reasoning end to first answering token scheduled) hits 27.39 seconds without migration vs. near-zero with it (Section V-D).
- PASCAL(NonAdaptive) shows blind migration is harmful—SLO violation jumps from 0.69% to 7.45% under high load (Figure 15(b)).

These ablations demonstrate the design choices aren't arbitrary.

**3. Realistic Workload Characteristics (Figure 8):**
They use actual token count distributions from AlpacaEval2.0 and Arena-Hard queried through OpenAI's o4-mini API. The distributions show realistic variance (mean ~550-950 reasoning tokens, with tails reaching thousands).

**4. Tail TTFT Improvements Are Substantial (Figures 9, 10):**
Up to 72% reduction in tail TTFT on Arena-Hard vs. FCFS, with absolute improvements of 64 seconds. These are meaningful for user experience when reasoning phases are long.

### Weaknesses

**1. Simulation-Based Evaluation:**
The entire evaluation (Section V-A) uses a "profile-based simulator" rather than running on real hardware under real load. While they validate MAPE of 1.62% for end-to-end latency against real measurements, the multi-instance cluster behavior—especially migration overhead under contention—is modeled, not measured. The 100 Gbps fabric assumption and the claim that KV cache transfer latency is "negligible" (Section IV-B quotes ~40ms for 2K tokens from prior work) deserves skepticism in production settings where network congestion is real.

**2. Single Model, Single Scale:**
All experiments use DeepSeek-R1-Distill-Qwen-32B on 8×H100 instances. No evaluation of:
- Larger models (70B+) where memory pressure is more severe
- Smaller instances (A100-40GB, consumer GPUs)
- Tensor-parallel deployments where KV cache migration crosses NVLINK domains

**3. Cherry-Picked Workload Mix for "Alternative Reasoning Datasets" (Section V-D, Figure 16):**
The authors acknowledge that for pure problem-solving benchmarks (MATH-500, GPQA, LiveCodeBench) with short answers, "PASCAL's benefits may diminish." Their workaround is to artificially mix 50% Arena-Hard with these datasets. This is fair disclosure, but it means PASCAL's value proposition is workload-dependent—chat applications benefit, pure reasoning tasks may not.

**4. Missing Cost of Wrong Phase Detection:**
What happens if the model generates a token that *looks like* `</think>` but isn't (e.g., in code generation)? The paper assumes perfect phase detection but doesn't discuss failure modes or robustness.

**5. No Comparison to State-of-the-Art Schedulers:**
The baselines are FCFS (vLLM default) and vanilla Round-Robin. What about Andes [31] which they cite for QoE-aware scheduling? Or Llumnix [44] for priority-aware migration? The related work section acknowledges these exist but the evaluation doesn't compare against them.

---

## Q4: What the Authors Didn't Tell You

**1. The "72% Reduction" Is in Tail TTFT Under Specific Conditions:**
Figure 10 shows tail TTFT improvements, but look carefully at Figure 9 (raw TTFT scatter plots). Under "Low" arrival rates, all three schedulers perform nearly identically—the lines overlap. PASCAL's benefits emerge only under "Medium" and "High" load when memory pressure forces preemption/blocking decisions. If your cluster isn't heavily loaded, PASCAL buys you little.

**2. The SLO Violation Rates Are Already Low:**
Figure 11 shows SLO violation rates. Even under "High" load, FCFS and RR are at ~2-5% violations. PASCAL reduces this, but we're talking about going from 2.5% to near 0%—not saving a sinking ship. The system isn't fundamentally broken without PASCAL; it's an optimization, not a rescue.

**3. Throughput Is Unchanged (Figure 12):**
PASCAL achieves "comparable throughput" (within 3%) to baselines. This is presented positively, but it also means PASCAL provides *no throughput improvement*. The gains are purely in latency distribution, not total work done. For cost-conscious deployments where throughput matters more than tail latency, PASCAL is neutral.

**4. The "Reasoning-Heavy" Results Are Underwhelming:**
Section V-D's alternative dataset evaluation (Figure 16) admits: "Compared to RR, improvements are smaller due to dataset characteristics." The paper pivots to arguing PASCAL still reduces tail TTFT "by up to 13.9%" in some bins, with "worst-case degradation under 7.7%." This is hedging. For workloads dominated by reasoning with short answers, PASCAL's value proposition weakens significantly.

**5. Migration Overhead Is Handwaved:**
Section IV-B claims KV cache transfer latency is "negligible" because reasoning phases take tens of seconds anyway. But Section V-C reports P99 KV cache transfer latencies of 0.14-0.25 seconds under high load. That's assuming a clean 100 Gbps network. In real deployments with competing traffic, these could be higher. More importantly, the paper doesn't measure *bandwidth contention* when multiple instances migrate simultaneously—they mention it as a possibility but provide no data on how often it occurs or its impact.

**6. The "Adaptive Migration" Is Actually Pretty Simple:**
Figure 7 and the surrounding text make adaptive migration sound sophisticated, but it's essentially: "If destination GPU memory is full and source has space, don't migrate." This is a sensible heuristic, but it's reactive rather than predictive. There's no modeling of future memory pressure or intelligent scheduling of migration timing.

**7. No Discussion of Phase Prediction:**
The system waits for the `</think>` token to appear, then reacts. But by then, the reasoning phase is over. Ideally, you'd *predict* when reasoning is about to end and pre-stage migration. The Dynasor work [13] they cite in related work does exactly this—predicting which requests need extended reasoning. PASCAL ignores prediction entirely, leaving potential performance on the table.