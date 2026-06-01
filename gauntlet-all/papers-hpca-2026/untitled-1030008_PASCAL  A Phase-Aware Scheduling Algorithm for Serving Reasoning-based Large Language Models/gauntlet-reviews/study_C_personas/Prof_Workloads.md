## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Imagine you're using ChatGPT-o1 or DeepSeek-R1 — these "reasoning" LLMs that think before they answer.

**The Problem Setup:**

```
Traditional LLM:
[Prefill] → t1 → t2 → t3 → ... → done
           ↑
         TTFT (Time-To-First-Token)

Reasoning LLM:
[Prefill] → r1 → r2 → r3 → </think> → t1 → t2 → t3 → ... → done
                              ↑
                            TTFT (now includes ALL reasoning tokens!)
```

So here's what changed: In reasoning LLMs, the user doesn't see the intermediate "thinking" tokens (r1, r2, r3...). They're waiting — staring at a blank screen — while the model generates potentially *thousands* of reasoning tokens. Only after `</think>` does the user get their first visible answer token (t1).

**The Core Insight (Figure 4 vs Figure 5):**

The paper runs two clever experiments:
- **Reasoning phase sensitivity**: When you block or preempt requests during reasoning, TTFT explodes. FCFS causes 5.14× latency increase for short requests (128 tokens). RR causes 1.75× for long requests (2048 tokens).
- **Answering phase tolerance**: During answering, preemption barely hurts SLO attainment! RR achieves near-oracle SLO rates even with preemption overhead (Figure 5b).

**PASCAL's Solution:**

```
Instance-Level Scheduler
         ↓
    Algorithm 1: Place reasoning → instance with smallest KV footprint
    Algorithm 2: Migrate answering → instance with fewest reasoning requests
         ↓
Intra-Instance Scheduler (per GPU)
    ┌─────────────────────────┐
    │ HIGH-PRIORITY QUEUE     │ ← Reasoning requests (minimize interruption)
    ├─────────────────────────┤
    │ LOW-PRIORITY QUEUE      │ ← Answering requests (RR + token pacer)
    └─────────────────────────┘
```

Key mechanism: When a request emits `</think>`, it migrates to a different instance (Algorithm 2) to avoid blocking new reasoning requests on its original instance.

---

## Q2: The Key Insight

**The Fundamental Insight:** Reasoning and answering phases have *asymmetric sensitivity* to scheduling interference.

- **Reasoning phase**: Latency-sensitive. Every second of blocking or preemption adds directly to TTFT. Users are staring at nothing during this entire phase.
- **Answering phase**: Threshold-sensitive. Users only need tokens "fast enough" (~100ms TPOT per human reading speed). Preemption is tolerable as long as the token pacer can smooth delivery.

**Why This Matters:**

Existing schedulers (FCFS, Round-Robin) treat all decoding tokens identically. They don't know whether a token is a "hidden reasoning" token or a "user-visible answering" token. This ignorance causes:
1. FCFS: Head-of-line blocking inflates TTFT for short reasoning requests
2. RR: Frequent preemption fragments long reasoning requests

**The Architectural Response:**

PASCAL exploits this asymmetry with a hierarchical design:
1. **Never preempt reasoning for answering** — reasoning gets GPU memory priority
2. **Freely preempt answering among themselves** — they can handle it
3. **Migrate at phase boundary** — relocate answering requests to reduce interference

This is a phase-aware, priority-differentiated scheduler specifically designed for the two-phase structure of CoT inference. The insight is simple but powerful: "invisible work" and "visible work" have fundamentally different QoE implications.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Methodologically Sound Characterization (Section III)**
The motivation experiments in Figures 4 and 5 are well-designed. They isolate reasoning vs. answering phases using synthetic workloads with controlled token lengths (128–2048). The comparison against an "oracle" (unlimited GPU memory) provides a meaningful upper bound. This isn't hand-waving — it's data-driven motivation.

**2. Appropriate Baseline Selection**
Comparing against FCFS (vLLM default) and Round-Robin covers the spectrum of phase-agnostic policies. They don't compare against a strawman; FCFS is genuinely the production default, and RR represents the state-of-the-art in SLO-aware time-sharing schedulers (Section II-C cites Andes [31], AQUA [26], etc.).

**3. Ablation Studies (Section V-D)**
Figure 13 ablates migration (PASCAL vs. PASCAL(NoMigration)), showing P99 blocking latency jumps to 27.39 seconds without migration. Figure 15 ablates adaptive migration, demonstrating SLO violation rate drops from 7.45% to 0.69% with the adaptive policy. These decompose the contribution of each design choice.

**4. Simulation Validation**
Section V-A reports MAPE of 1.62% for end-to-end latency, 12.6% for mean TTFT, and 6.49% for TPOT. While simulation-based evaluation has limitations, they follow established methodology from [1], [6], [30], [39] and validate against real H100 hardware.

### Weaknesses

**1. The "Reasoning-Heavy Dataset" Problem (Figure 16)**
When they test on MATH-500, GPQA, and LiveCodeBench — where reasoning tokens dominate and answers are short — the gains shrink dramatically. Against RR, improvements are marginal ("limited additional benefit" per Section V-D). This is concerning because these problem-solving benchmarks represent a significant use case for reasoning LLMs. The paper acknowledges this but frames it as "competitive performance" rather than addressing the limitation head-on.

**2. Cherry-Picked Workload Distribution**
The primary evaluation uses AlpacaEval2.0 and Arena-Hard with o4-mini API queries (Figure 8). These are *chat* benchmarks with mean answering tokens of 566 and 824 respectively. The reasoning-to-answering ratio is nearly 1:1. Real reasoning model deployments serving mathematical or coding tasks would have ratios of 4:1 to 8:1 (Figure 14). The evaluation is biased toward workloads where PASCAL shines.

**3. Simulation-Only at Scale**
The 8-instance cluster results come entirely from simulation. While single-instance simulation is validated (Section V-A), the cluster-level behavior — especially KV cache transfer contention, network bandwidth saturation, and migration timing — relies on modeled assumptions. The P99 KV cache transfer latencies (0.14s, 0.25s) reported in Section V-C are simulation outputs, not measured values.

**4. Fixed Token Quantum Sensitivity**
The token quantum is set to 500 tokens (Section V-A) without sensitivity analysis. Prior work [31] shows quantum selection significantly impacts fairness-latency tradeoffs. The 5000-token demotion threshold for reasoning requests (Section IV-C) is also presented without justification.

**5. Single Model Evaluation**
All experiments use DeepSeek-R1-Distill-Qwen-32B. This is a distilled 32B model — the reasoning behavior and token distributions may differ from the full 671B DeepSeek-R1 or OpenAI's o-series. No evaluation on alternative reasoning architectures limits generalizability claims.

---

## Q4: What the Authors Didn't Tell You

**1. The Phase Detection Assumption is Fragile**
The paper assumes the model emits a clean `</think>` token to signal phase transitions (Section IV-A, IV-B). But what if:
- The model is fine-tuned without explicit think tokens?
- The reasoning/answering boundary is fuzzy (some models interleave)?
- The model emits multiple thinking segments?

Section VI mentions PASCAL is "orthogonal to reasoning-token reduction methods" [13], [19], but these methods may alter or eliminate the clean phase boundary that PASCAL depends on.

**2. Migration Overhead During Contention**
Section IV-B claims KV cache transfer overhead is "negligible" because 40ms transfer time is dwarfed by tens-of-seconds reasoning latency. But under high load with multiple simultaneous phase transitions:
- What's the bandwidth contention on the 100 Gbps fabric?
- What happens when multiple requests migrate to the same "best" instance?
- The adaptive migration policy (Figure 7) handles one pathological case, but systematic analysis of migration storms is absent.

**3. The QoE Metric Modification is Convenient**
In Section V-A, they modify QoE to *exclude* TTFT: "we instead compute QoE solely from TPOT and evaluate TTFT separately." The original QoE metric [31] combines both TTFT and TPOT. This separation lets them report good QoE numbers (Figures 11) while TTFT for long requests can still be hundreds of seconds (Figure 9). A user waiting 200+ seconds isn't experiencing "good QoE" regardless of how smooth the subsequent token stream is.

**4. Throughput is Flat — But Why?**
Figure 12 shows PASCAL achieves throughput "within 3%" of baselines. But given that PASCAL adds:
- Instance-level scheduling overhead (Algorithm 1, 2 execution)
- KV cache migration at phase boundaries
- Priority queue management

Where does the overhead go? Either the scheduling decisions are negligible (good), or the simulation doesn't capture real scheduling latency (suspicious). The paper doesn't profile scheduler overhead.

**5. The "Reasoning-First" Policy Has Starvation Risk**
Section IV-C explains reasoning requests always preempt answering requests for GPU memory. Under sustained high arrival rates, answering requests could be indefinitely starved if new reasoning requests keep arriving. The demotion policy (reasoning >5000 tokens → low-priority) is a band-aid, not a principled solution. There's no formal guarantee of answering-phase progress.

**6. Multi-Turn Conversation Blind Spot**
Real chat deployments involve multi-turn conversations where prior context (including previous reasoning traces) are cached. The evaluation uses single-turn requests only. How does PASCAL handle:
- KV cache sharing across turns?
- Phase transitions within ongoing conversations?
- Prefix caching interaction with migration policies?