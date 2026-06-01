# PASCAL Paper Deconstruction

## Q1: Whiteboard Explanation

Let me draw out what's actually happening here at the system level.

**The Problem Setup:**
Traditional LLM inference has two stages: prefill (process input, generate KV cache) → decode (autoregressive token generation). TTFT = prefill latency. TPOT = decode latency per token. Clean mapping.

Reasoning LLMs (DeepSeek-R1, o3) break this. The decode stage now has TWO phases:
- **Reasoning phase**: Model generates `<think>` tokens (r1, r2, r3...) that the user NEVER sees
- **Answering phase**: Model generates actual response tokens (t1, t2...) after a `</think>` delimiter

**The Wiring Diagram (Figure 6):**
```
                    ┌─────────────────────────────────┐
    New requests ──►│   Instance-Level Scheduler      │
                    │  ┌───────────┬───────────────┐  │
                    │  │ Algorithm 1│  Algorithm 2  │  │
                    │  │(reasoning) │  (answering)  │  │
                    │  └─────┬─────┴───────┬───────┘  │
                    └────────┼─────────────┼──────────┘
                             │             │
         ┌───────────────────┼─────────────┼───────────────────┐
         │ Instance Pool     ▼             ▼                   │
         │  ┌─────────────────────────────────────┐            │
         │  │        Per-GPU Instance             │            │
         │  │  ┌─────────────────────────────┐    │            │
         │  │  │ HIGH-PRIORITY QUEUE         │◄── Reasoning    │
         │  │  │ (RR scheduling, no preempt) │    requests     │
         │  │  └─────────────────────────────┘    │            │
         │  │  ┌─────────────────────────────┐    │            │
         │  │  │ LOW-PRIORITY QUEUE          │◄── Answering    │
         │  │  │ (RR + token pacer)          │    requests     │
         │  │  └─────────────────────────────┘    │            │
         │  │           │                         │            │
         │  │           ▼ GPU Memory (KV cache)   │            │
         │  └─────────────────────────────────────┘            │
         └─────────────────────────────────────────────────────┘
```

**The Key Mechanism - Phase Detection:**
The system monitors token output for the `</think>` delimiter. When detected:
1. Request transitions from high-priority → low-priority queue
2. Instance-level scheduler evaluates whether to **migrate** the request to a different GPU instance (Algorithm 2)
3. KV cache transfers over 100Gbps fabric if migration occurs

**Algorithm 1 (Reasoning Placement):** Route to instance with smallest KV cache footprint (mi), excluding instances already violating answering-phase SLOs.

**Algorithm 2 (Answering Placement):** Route to instance with fewest reasoning requests (ri), to minimize interference from high-priority work.

---

## Q2: The Key Insight

**The "Magic Trick":** The authors observe an **asymmetric sensitivity to preemption** between reasoning and answering phases.

- **Reasoning phase**: Latency is *additive* to TTFT. Any blocking or preemption directly inflates the time before the user sees ANY output. Figure 4 shows up to 5.14× latency inflation under FCFS for short reasoning requests.

- **Answering phase**: Latency is *threshold-sensitive*, not absolute-sensitive. Users only care if TPOT stays below ~100ms (human reading speed). Preemption that causes momentary stalls is absorbed by the **token pacer** buffer. Figure 5(b) shows RR achieving near-oracle SLO attainment despite 2× longer absolute latency.

**Why this matters:** Existing schedulers (FCFS, RR) treat all decode tokens uniformly. PASCAL exploits the fact that reasoning tokens have NO user-facing SLO (users don't see them), so they should be prioritized to minimize wall-clock time. Answering tokens have a TPOT SLO that's *generous* relative to GPU capabilities, so they can tolerate preemption.

**The structural delta from baseline:** Instead of one decode queue, PASCAL adds:
1. A hierarchical 2-queue structure per instance (high/low priority)
2. Phase detection logic (token pattern matching for `</think>`)
3. Inter-instance migration at phase boundaries with adaptive override

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Honest characterization methodology (Section III):** They isolate reasoning and answering phases in separate experiments (Figure 4, 5) before combining them. This is good experimental hygiene that lets readers understand the mechanism.

2. **Tail latency focus (Figure 10):** They correctly report tail TTFT rather than averages, using adaptive percentile selection based on bin sample size. The 72% tail TTFT reduction on Arena-Hard is meaningful.

3. **Ablation studies done right (Section V-D):** 
   - PASCAL(NoMigration) proves migration matters (Figure 13c shows 27.39s P99 blocking latency without it)
   - PASCAL(NonAdaptive) proves adaptive migration matters (7.45% vs 0.69% SLO violations)

4. **Throughput neutrality (Figure 12):** They show <3% throughput difference vs. baselines, avoiding the common trap of trading throughput for latency.

**Weaknesses:**

1. **Simulation-based evaluation:** Despite claiming a real H100 system (Section III-A), the main multi-instance results use a "cluster-level simulator" (Section V-A). The simulator validation shows 12.6% MAPE for mean TTFT—not terrible, but tail behavior (what they care about) isn't validated. Real systems have PCIe contention, CUDA stream scheduling, and memory fragmentation that simulators miss.

2. **Workload construction is synthetic:** They query OpenAI's o4-mini to get reasoning/answering token distributions (Section V-A), then replay these as traces. Real reasoning LLMs have *correlated* reasoning lengths with problem difficulty and prompt characteristics—this correlation is lost.

3. **Limited baseline comparison:** They only compare against FCFS and vanilla RR. Missing comparisons:
   - Andes [31] (the QoE-aware scheduler they cite for the token pacer)
   - Llumnix [44] (priority-aware migration)
   - FastServe [48] (deadline-aware scheduling)

4. **The "reasoning-heavy" scenario (Figure 16) shows diminishing returns:** When 50% of requests are from MATH-500/GPQA/LiveCodeBench (long reasoning, short answer), PASCAL's advantage over RR shrinks substantially. They acknowledge this but don't quantify the crossover point.

5. **Token quantum of 500 is unexplained:** Section V-A states quantum=500 without justification. This is a critical parameter—too small causes excessive preemption overhead, too large defeats the purpose.

---

## Q4: What the Authors Didn't Tell You

**1. The KV Cache Migration Cost is Hand-Waved:**

Section IV-B claims migration overhead is "negligible" with a 40ms transfer for 2048 tokens (citing Patel et al. [39]). But:
- DeepSeek-R1 uses GQA with 8 KV heads, not standard MHA. KV cache size is ~3.7MB per 1K tokens for their 32B model.
- At high load with simultaneous migrations, they observe 0.25s P99 transfer latency (Section V-C)—that's 6× higher than their claim.
- More critically: migration requires **synchronization**. The source instance must stop generating tokens, serialize the KV cache, and wait for ACK. This creates a bubble that affects *all* requests on that instance, not just the migrating one.

**2. Phase Detection Latency is Zero-Cost (Assumed):**

They assume the `</think>` token is detected instantaneously with no overhead. In reality:
- Token detokenization happens asynchronously
- The scheduler must inspect every generated token
- False positives (if the model generates `</think>` mid-reasoning) aren't discussed

**3. The "Demotion Policy" is a Hack:**

Section IV-C mentions that reasoning requests exceeding 5000 tokens are demoted to low-priority. This is a **hardcoded threshold** that:
- Contradicts their claim that reasoning should never be preempted
- Has no theoretical justification (why 5000? not 4000 or 6000?)
- Effectively admits their high-priority queue can't handle adversarial workloads

**4. Memory Accounting is Incomplete:**

Algorithm 1 uses "total memory occupied by KV cache (GPU + CPU)" as the routing metric. But:
- CPU memory (for offloaded KV cache) doesn't directly constrain GPU scheduling
- The metric should weight GPU-resident vs. CPU-offloaded differently
- They don't discuss memory fragmentation from PagedAttention

**5. The QoE Metric Modification Obscures Comparison:**

Section V-A states they "compute QoE solely from TPOT and evaluate TTFT separately" because reasoning LLMs have "highly variable reasoning lengths." This is convenient but:
- The original QoE definition from Andes [31] includes TTFT
- By separating them, they avoid showing the combined degradation
- A request with 200s TTFT but perfect TPOT looks great under their metric, terrible under the original

**6. No Discussion of Multi-Turn Conversations:**

Real chatbot deployments involve multi-turn sessions where:
- KV cache from previous turns must be preserved
- Reasoning in turn N may depend on context from turn N-1
- Instance affinity becomes important (migration breaks cache locality)

The paper evaluates only single-shot requests.