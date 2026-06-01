# Paper Deconstruction: PASCAL

## Q1: Whiteboard Explanation

Imagine you're running a ChatGPT-like service, but with a "reasoning" model like DeepSeek-R1 or OpenAI's o1. These models don't just spit out answers—they first "think out loud" internally, generating hundreds or thousands of reasoning tokens (wrapped in `<think>...</think>` tags) before producing the actual answer the user sees.

**The Problem:** From the user's perspective, they submit a query and then... nothing happens for a very long time. The model is furiously generating reasoning tokens internally, but the user just sees a blank screen. Traditional LLM serving systems don't know the difference between "the model is still thinking internally" versus "the model is generating the user's answer." They treat all tokens equally.

**The Key Tension:** GPU memory is finite. When you're serving multiple requests, their KV caches (the memory of what each request has processed) compete for space. When memory fills up, you have two options:
1. **Block** new requests (make them wait in line)
2. **Preempt** existing requests (pause them, offload their KV cache to CPU, resume later)

Both are painful, but *when* that pain is inflicted matters enormously:
- During the **reasoning phase**: Any delay directly increases TTFT (Time-To-First-Token). The user is staring at a blank screen. Every second counts.
- During the **answering phase**: As long as tokens stream at human reading speed (~10 tokens/sec), users are happy. Brief pauses can be masked by buffering.

**PASCAL's Solution:** A two-level scheduler that knows which phase each request is in:

1. **Instance-Level Scheduler** (across multiple GPU servers):
   - Routes new reasoning requests to servers with lowest memory pressure
   - When a request transitions to answering (detects `</think>` token), potentially migrates it to a different server with fewer competing reasoning requests

2. **Intra-Instance Scheduler** (within each GPU):
   - **High-priority queue**: Reasoning requests get first dibs on GPU memory
   - **Low-priority queue**: Answering requests use whatever's left, with round-robin time-sharing
   - A **token pacer** smooths out answering token delivery to hide preemption stutters

The core insight: reasoning is latency-critical (minimize absolute time), answering is threshold-critical (just be "good enough").

---

## Q2: The Key Insight

**The Real Contribution:** This paper identifies and exploits a fundamental *asymmetry in QoE sensitivity* between the two phases of reasoning-based LLM inference.

The "delta" isn't a new hardware primitive or a novel attention algorithm. It's a **scheduling insight**: the decoding stage of reasoning LLMs should be treated as *two semantically distinct workloads* with different optimization objectives.

**The Mechanism (Figures 4 and 5 are the smoking guns):**

- **Figure 4** shows that during reasoning, *any* interruption—blocking or preemption—directly inflates latency. For 128 reasoning tokens under FCFS, latency balloons to **5.14× oracle** due to blocking. Under RR with 2048 tokens, preemption adds **1.75× overhead**. This is devastating because it all contributes to TTFT.

- **Figure 5** reveals the asymmetry: during answering, RR scheduling causes *higher absolute latency* than FCFS for long sequences (Figure 5(a)), but achieves **identical SLO attainment** to the oracle (Figure 5(b)). Why? Because SLOs are threshold-based—as long as tokens arrive fast enough, users don't care about total latency.

The paper essentially recognizes that existing schedulers (FCFS, RR, even sophisticated ones like Andes) apply the same policy to both phases, leaving performance on the table. PASCAL's hierarchical priority queue with phase-aware migration is the logical consequence of this insight.

**What makes this clever:** The phase boundary is *observable*—the `</think>` token explicitly marks the transition. This isn't predicting future behavior (which is hard); it's reacting to a clear signal in the token stream.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Honest Characterization Study (Section III):** Before proposing PASCAL, the authors systematically measure the impact of blocking and preemption on each phase independently (Figures 4 and 5). This is exemplary methodology—they demonstrate the problem before claiming to solve it.

2. **Realistic Baselines:** They compare against FCFS (vLLM's default) and Round-Robin with explicit token quanta. These are genuine, deployed scheduling policies, not strawmen.

3. **Simulator Validation (Section V-A):** They report MAPE of 1.62% for end-to-end latency against real H100 measurements. This is credible for a profile-based simulator, and they cite established methodology from prior work [1], [6], [30], [39].

4. **Ablation Studies (Section V-D):**
   - **Figure 13:** PASCAL(NoMigration) shows that migration at phase boundaries is critical—P99 blocking latency reaches 27.39s without it.
   - **Figure 15:** PASCAL(NonAdaptive) demonstrates that *always* migrating is also bad—SLO violations spike to 7.45% vs 0.69% with adaptive migration.

5. **Alternative Dataset Analysis (Figure 16):** They acknowledge that reasoning-heavy, short-answer workloads (MATH-500, GPQA, LiveCodeBench) reduce PASCAL's benefits and test accordingly. This intellectual honesty is rare.

### Weaknesses

1. **Simulation-Only Evaluation:** Despite having access to an H100 (Section III), the main results (Figures 9-12) are entirely simulated. The cluster simulation models 8 instances over 100Gbps fabric, but real deployment has contention, OS scheduling jitter, and software stack overhead that simulators notoriously miss.

2. **Single Model, Single Model Size:** All experiments use DeepSeek-R1-Distill-Qwen-32B. The paper claims the 32B model was chosen because "GPU memory constraints limit the allocation of KV caches" (Section V-A), but this is also the regime where memory pressure is *most* important. Would PASCAL help with 7B models where memory is abundant? What about 70B+ models requiring tensor parallelism across GPUs?

3. **Fixed Token Quantum (500) and Demotion Threshold (5000):** These are stated as constants (Section V-A) without sensitivity analysis. Are these values optimal? How do they interact with different workloads?

4. **Limited Load Testing:** The "high" request arrival rate stresses the system, but Figure 12 shows throughput differences of ≤3%. If PASCAL doesn't improve throughput, what happens at *higher* loads where the system saturates? Does PASCAL degrade more gracefully?

5. **KV Cache Migration Overhead Dismissed Too Quickly (Section IV-B, V-C):** The paper claims 40ms transfer latency is "negligible" relative to multi-second reasoning times. But Figure 8 shows AlpacaEval2.0 has a mean of only 558 reasoning tokens—at 30ms/token, that's ~17 seconds. The claim holds, but barely, and ignores the tail where short reasoning + large KV caches could be problematic.

6. **No Comparison to State-of-the-Art Schedulers:** Where is Andes [31]? Llumnix [44]? DistServe [54]? The paper cites these in Related Work but doesn't benchmark against them. The baselines (FCFS, RR) are necessary but not sufficient.

---

## Q4: What the Authors Didn't Tell You

### The Elephant in the Room: Why Not Disaggregate?

The authors explicitly state in Section VII that "both the reasoning and answering phases belong to the same decoding stage" and thus DistServe-style disaggregation "offers little benefit." But this deserves scrutiny.

DistServe and Splitwise disaggregate *prefill* from *decode* because prefill is compute-bound and decode is memory-bound. Here, reasoning and answering are both decode (memory-bound), so the hardware characteristics are similar. **But the scheduling characteristics are different.** 

A natural extension would be: run reasoning on dedicated "reasoning instances" and answering on "answering instances." This would eliminate inter-phase interference entirely. The paper dismisses this in Section VII as "questionable," but doesn't actually benchmark it. Given that PASCAL already migrates requests at phase boundaries anyway, the overhead might be acceptable.

### The QoE Metric is Conveniently Modified

In Section V-A, they note: "Because reasoning-based LLMs have highly variable reasoning lengths, the original QoE metric (which includes a fixed TTFT target) is impractical; we instead compute QoE solely from TPOT and evaluate TTFT separately."

Translation: they couldn't meet a reasonable TTFT target so they decoupled the metrics. This is pragmatic, but it obscures the fact that users of reasoning LLMs experience *horrible* TTFT by conventional standards (tens of seconds vs. sub-second for traditional LLMs).

### What About Speculative Decoding and Other Optimizations?

The paper operates in a world where each token takes ~30ms. Modern inference systems use speculative decoding, continuous batching optimizations, and potentially different attention implementations during different phases. Would PASCAL's benefits hold if reasoning tokens could be generated faster? The paper is silent on this.

### The "Adaptive Migration" Heuristic is Fragile

Algorithm 2 selects instances based on $r_i$ (reasoning requests) and $a_i$ (fresh answering requests). But the paper admits in Section IV-B that the scheduler "cannot foresee future memory contention due to unpredictable output lengths." The adaptive migration (Figure 7) is a patch on top of a fundamentally reactive system. A more principled approach might predict memory requirements using sequence length distributions.

### Multi-Turn Conversations are Ignored

Real reasoning LLM deployments involve multi-turn conversations where KV caches persist across turns. How does PASCAL handle a request that completes answering, then receives a follow-up prompt? Is the KV cache kept resident? Migrated? This deployment scenario is not addressed.

### Hardware Heterogeneity is Future Work

Section VII mentions NVIDIA Rubin CPX (prefill-optimized) and CPU-GPU hybrid inference but punts entirely: "A deeper exploration of PASCAL in such heterogeneous environments is beyond the scope of this work." Given that these architectures are actively being deployed, this limitation may date the paper quickly.