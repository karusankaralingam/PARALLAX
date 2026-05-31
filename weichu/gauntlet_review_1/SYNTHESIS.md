# Master Class Reading Guide: SLINFER (HPCA 2026)

## 1. The "Real" Abstract (No-Hype Summary)

**What they actually built:** A scheduler that time-slices multiple small LLM instances (≤13B parameters) onto shared CPU and GPU nodes at token-generation granularity, using deadline-aware priority scheduling and dynamic KV-cache resizing.

**The core observation:** ServerlessLLM and similar systems allocate one entire GPU per active model, even when serving 5 requests/hour. With 128 models and 4 GPUs, you get 33% SLO violations despite 23% average memory utilization. SLINFER packs multiple models onto the same hardware by predicting per-token compute times and interleaving execution.

**The secondary observation:** Intel's 4th-gen Xeons with AMX (Advanced Matrix Extensions) can now meet production SLOs for 7B/13B models with short inputs. Those idle CPU cores on your GPU servers are suddenly useful.

**What they claim:** 47-62% serving capacity improvement through GPU sharing; 86-154% when also using CPUs. Tested on 4 GPUs + 4 CPUs with up to 128 models.

---

## 2. The "Rashomon" Synthesis (Conflicting Expert Perspectives)

The experts viewed this paper through fundamentally different lenses, and their disagreements reveal the paper's core tensions:

### The Microarchitecture View (Dr. Microarch)
Focused on the **hardware insight**: AMX delivers 6.7× TTFT speedup over previous-gen Xeons (Table I). The "headroom" scheduler is essentially Earliest Deadline First adapted for streaming token generation. *Concern:* The 2D linear interpolation for performance prediction assumes well-behaved compute surfaces, but cache effects and thermal throttling are non-linear.

### The Workloads View (Prof. Workloads)
Skeptical of the **evaluation scope**: The traces are synthetic (Azure Functions 2019 mapped to LLMs—not actual multi-tenant LLM patterns). The 47-154% improvements are measured against a strawman baseline (ServerlessLLM with exclusive GPU allocation). *Key observation:* At 128 models with 13B size, all systems saturate all resources—SLINFER's advantage disappears precisely when you need it most (Figure 22c).

### The Systems/Tooling View (Dr. Sim)
Concerned about **implementation artifacts**: The "4 GPU nodes" are actually 2 physical machines with 2 GPUs each—network latency is artificially low. The KV-cache scaling takes 1.9 seconds (Figure 17), which is 100× slower than theoretical memory bandwidth would suggest—this is vLLM/Python overhead, not fundamental. *Question:* Would these results hold on an actual multi-rack deployment?

### The Industry View (Chief Architect)
Worried about **verification complexity**: Shadow validation simulates future token generations at every request arrival—how do you prove this doesn't deadlock under adversarial workloads? The 10% overestimation buffer is a heuristic, not a guarantee. *Verdict:* Would ship the headroom scheduler and watermark-based KV-cache as vLLM plugins; would discard the shadow validation and proactive preemption as "verification debt."

### The Memory Management View (KV-Cache Expert)
Appreciated the **orchestration design**: The optimistic/pessimistic dual-tracking for memory operations is clever—optimistic for planning, pessimistic for execution, with a reservation station for pending scale-ups. *But:* The 25% watermark was tuned on Azure Conversation traces (97.9% inputs <4K tokens). For long-context workloads, this static watermark may be wrong.

---

## 3. The "Magic Trick" (The Core Mechanism)

The entire paper hinges on **one equation and one algorithm**:

### The Equation: Headroom
```
headroom = start_time + TTFT_SLO + TPOT_SLO × tokens_generated - current_time
```

This tells you: "How much slack does this request have before SLO violation?" The scheduler always picks the instance with the *shortest* headroom. This is Earliest Deadline First, but adapted for the streaming nature of LLM inference where each token has its own implicit deadline.

### The Algorithm: Shadow Validation
Before accepting a new request, SLINFER simulates the future schedule to check three failure modes:
1. Will the new request's prefill finish in time?
2. Will existing requests get delayed past their SLOs by the new prefill?
3. Will the aggregate decode time across all instances exceed TPOT SLO?

**Why this matters:** In a shared environment, accepting one request can cause *other* requests to miss their SLOs. The shadow validation catches this before commitment.

**The hidden assumption:** Performance is predictable enough that 2D linear interpolation (batch size × token length) with a 10% safety margin is sufficient. The paper claims 5.9%/3.9% average prediction error for TTFT/TPOT, but doesn't report the distribution of errors or behavior under contention.

---

## 4. The "Skeleton in the Closet" (What They Didn't Tell You)

### Skeleton #1: The CPU Story Is Narrower Than Advertised
- **Only works with 4th-gen+ Intel Xeons** (AMX-equipped). Most datacenters still run older hardware.
- **Limited to ≤13B models, ≤4K input tokens, ≥250ms TPOT SLO.** Under 100ms TPOT, even 7B models are infeasible on CPU.
- **3-4 CPU nodes ≈ 1 GPU node** in serving capacity (Figure 24). CPUs are a cost-inefficient fallback, not a first-class resource.

### Skeleton #2: The Evaluation Is Small-Scale and Synthetic
- **4 GPUs + 4 CPUs, 128 models max.** Real cloud deployments have thousands of GPUs.
- **Traces are synthetic.** They map Azure Functions 2019 invocation patterns to LLMs. Real multi-tenant LLM workloads may have different burstiness characteristics.
- **No comparison to MuxServe** [24], which also does GPU sharing for multi-LLM serving.

### Skeleton #3: Memory Scaling Overhead Is Non-Trivial
Figure 17 shows scaling a 32GB KV-cache takes **0.3-1.9 seconds**. During bursty traffic, this could cause cascading SLO violations. The watermark mechanism reduces *average* overhead to 1.4%, but what about the tail?

### Skeleton #4: The Consolidation Mechanism Has Corner Cases
- **Proactive preemption** evicts smaller-batch neighbors to make room for growth. But what if the small-batch instance is serving a latency-critical request? Priority inversion is possible.
- **Preempted requests must be "rescheduled to other nodes."** The migration latency (KV-cache serialization, network transfer, deserialization) isn't quantified.

### Skeleton #5: The "Heterogeneous Cluster" Is Really Two Servers
Section IX-A reveals: "4 GPU nodes... logically separated from two physical machines with 2 GPUs each." The network latency for request routing is artificially low. Their consolidation benefits may not transfer to distributed deployments.

---

## 5. The Verdict (Why This Matters)

### Why We're Reading This
This paper represents a **design philosophy shift** in serverless LLM serving: from "one model, one GPU" to "elastic resource pools shared at token granularity." Whether or not SLINFER's specific implementation survives, this framing will influence future systems.

### The Genuine Contributions
1. **The AMX characterization** (Table I) is actionable. If you're procuring hardware for small-model inference, this data matters.
2. **The headroom scheduler** is a clean abstraction that naturally handles prefill/decode heterogeneity, different model sizes, and mixed CPU/GPU deployments.
3. **The watermark-based KV-cache scaling** with optimistic/pessimistic budgeting is a reasonable engineering solution to multi-tenant memory management.

### The Limitations to Remember
1. **Regime-dependent benefits.** At high load with large models, SLINFER converges to the same behavior as baselines (Figure 22c, Figure 26 rightmost bar).
2. **Verification complexity.** Shadow validation and proactive preemption introduce state machine complexity that may be hard to debug in production.
3. **Narrow CPU applicability.** The AMX story only works for small models, short inputs, and relaxed SLOs on recent Intel hardware.

### The Takeaway for Your Research
When reading systems papers, ask: **"What regime are they optimizing for, and what happens at the boundaries?"** SLINFER optimizes for many small models with infrequent, bursty requests on heterogeneous hardware with relaxed SLOs. Outside this regime—large models, sustained load, tight latency requirements—the benefits evaporate. The paper is honest about this in the text, but the abstract and introduction emphasize the best-case numbers.

**Final assessment:** A solid systems paper that identifies a real inefficiency and proposes a reasonable solution, but the evaluation scope is narrower than the claims suggest. Read it for the mechanisms (headroom scheduling, watermark-based memory management), but remain skeptical of the generality.