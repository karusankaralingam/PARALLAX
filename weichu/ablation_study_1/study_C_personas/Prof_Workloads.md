# Prof. Bench's Evaluation Report: SLINFER (HPCA 2026)

## Q1: Whiteboard Explanation

Let me draw this out for you. The problem SLINFER tackles is actually quite elegant in its framing:

**The Setup:** Cloud providers host many private LLMs (think fine-tuned Llama-7B variants). These models are small-to-mid-sized (≤13B parameters), and they receive *infrequent, bursty requests*—87% of HuggingFace downloads are for models ≤8B, and 56% of LMSYS models get fewer than 5 requests/hour (Section III-B, Figures 2-3).

**The Problem:** Existing serverless LLM solutions (ServerlessLLM, Medusa) allocate entire GPUs exclusively to each model. When you host 64 models on 4 A100-80GB GPUs, 33% of requests miss SLOs despite only 23% average memory utilization (Section III-C, Figures 4-5). This is classic resource fragmentation—GPUs sit idle while models queue.

**SLINFER's Solution:** Three-pronged approach:

1. **Heterogeneous Resource Pool:** Modern CPUs with AMX (Intel 4th-Gen Xeon) can actually serve small LLMs within SLOs. A 32-core Xeon 6462C achieves 567ms TTFT for 1K-token inputs on Llama-2-7B, meeting the 1-2 second SLO (Table I, Figure 6). This gives you "free" compute capacity since GPU clusters have abundant idle CPUs.

2. **Elastic Sharing via Token-Level Scheduling:** Instead of giving each model a full GPU, SLINFER time-multiplexes instances at token granularity. The "headroom" mechanism (Equation 1) tracks how much delay each request can tolerate before violating SLO. The scheduler always picks the instance with minimum headroom—essentially earliest-deadline-first at token level.

3. **Memory Subsystem with Hazard Awareness:** KV-cache scaling is expensive (Figure 17: 1.9s to scale from 32GB to 64GB). SLINFER uses watermark-based lazy scaling and a reservation station to prevent OOM during concurrent scaling operations.

**The Consolidation Trick:** When scaling would create fragmented instances (same model on multiple nodes), SLINFER proactively preempts smaller-batch neighbors or reactively bin-packs requests to larger instances.

## Q2: The Key Insight

The core insight isn't just "CPUs can run LLMs now"—that's been known since Intel shipped AMX. The real insight is this:

**Serverless LLM workloads exhibit a paradoxical resource usage pattern: peak demands require full hardware access (for batching efficiency), but average demands are tiny, and the two states oscillate rapidly at token granularity.**

Look at Table II carefully—this is the smoking gun. Partitioning a GPU into 3 smaller instances for 7B-2K workloads yields only 3×12=36 aggregate concurrency versus 66 for a single large instance. Static partitioning *destroys* batching efficiency. But Figure 12 shows that even the hottest 1% of models see concurrency ranging from 1 to 128, meaning you can't predict when you need full resources.

SLINFER's key technical contribution is recognizing that **compute and memory resources must be provisioned at different timescales and with different strategies**:
- Compute: Token-level (milliseconds), fully elastic, based on headroom
- Memory: Request-level (seconds), watermark-buffered, coordinated across instances

The "headroom" formulation (Section VI-A, Equation 1) elegantly captures this: `headroom = ST + TTFT_SLO + TPOT_SLO · O − CT`. This transforms a complex multi-objective scheduling problem into a simple priority queue sorted by urgency.

The second crucial insight is that **preemption for consolidation can actually improve overall efficiency**, not just the preemptor's performance. By allowing larger instances to preempt smaller neighbors (Section VIII-A), SLINFER enables batching opportunities that benefit throughput globally.

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Workload Characterization (Figures 6-12)**
The motivation section is unusually thorough. They don't just claim CPUs can serve LLMs—they provide TTFT/TPOT data across 3 model sizes, multiple input lengths, and batch sizes. Table I showing 6.7× speedup from AMX is concrete and reproducible. The memory footprint analysis (Figure 9) using real Azure traces grounds their sharing claims in empirical data.

**2. Strong Baselines with Fair Tuning**
I appreciate their honesty in Section IX-A: "we tried our best to conservatively tailor a set of higher concurrency limits for sllm and sllm+c." They explicitly state the limits (59, 15, 6 for CPU vs. 160, 32, 16 for GPU per model size). This is rare—most papers use default configs to make baselines look bad.

**3. Ablation Study Structure (Section IX-C, Figure 23)**
The ablation cleanly isolates each component's contribution. Disabling sharing drops SLO rate from 99% to 89%—this proves sharing isn't just nice-to-have but essential for the claimed workload scenario.

**4. Sensitivity Analysis Coverage (Section IX-I)**
They test 5 different datasets (Figure 34-35), BurstGPT traces (Figure 27), varying CPU cores (Figure 29), keep-alive thresholds (Figure 30), and watermark settings (Figure 31). This breadth addresses many potential criticisms proactively.

### Weaknesses

**1. The Cherry-Pick Check: Workload Selection Bias**

The elephant in the room: **they're testing serverless traces, not actual multi-LLM traces**. Section IX-A admits: "Since LLM traces contain only a single model and lack the multi-model hot–cold characteristics, following ServerlessLLM, we use Azure Serverless Trace and map each LLM to a function."

This is a major assumption. Azure Functions (web services, IoT triggers) have fundamentally different arrival patterns than LLM inference. The LMSYS data they cite (Figure 3) shows invocation patterns, but they don't use LMSYS for evaluation—they only use Azure LLM trace for *token lengths*. The hot-cold distribution, burstiness, and temporal correlation of multi-LLM workloads remain unvalidated.

Figure 21 shows the trace characteristics, but these are *derived* from general serverless workloads, not observed from actual private LLM deployments.

**2. The Baseline Validity Problem**

ServerlessLLM's default concurrency limit of 2 is absurd for production—the authors acknowledge this. But then they set limits to (160, 32, 16) for GPU, which are essentially "unlimited" for their workloads (Figure 12 shows max concurrency rarely exceeds 128). This makes `sllm+c` a reasonable baseline, but the original `sllm` comparison in Figure 22 is against a strawman.

**3. The "Zero-Event" Reality: CPU Applicability**

Section IV-A2's "Limitations and Applicable Scenarios" is crucial but buried:
- CPUs only handle ≤13B models
- Short inputs only (≤5.6K for 13B)
- At 100ms TPOT SLO, only 7B models work
- At 50ms TPOT SLO, CPUs are infeasible entirely

Figure 35 confirms this: LongBench (32K inputs) forces SLINFER to abandon CPUs, and they note "sllm+c+s fully utilizes CPUs but violates 63.4% of SLOs."

The 250ms TPOT SLO they use is generous. Many production systems target 50-100ms for interactive applications. Under tighter SLOs, SLINFER's CPU advantage largely vanishes.

**4. Hardware Specificity**

Table I shows 3rd-Gen Xeon is 6.7× slower than 4th-Gen for TTFT. The entire CPU sharing opportunity depends on AMX availability. Yet they don't report what fraction of current datacenter CPUs have AMX. The 4th-Gen Xeon was released in January 2023—this represents cutting-edge hardware, not typical datacenter inventory.

**5. Prefill-Decode Disaggregation Dismissal (Section IX-G)**

Table III shows PD disaggregation hurts performance, but their explanation—"prefill instances spending 93% of their lifetime on average in cold starts or idle"—applies to their specific workload. Under higher load, disaggregation might help. They test only 32/64/128 models, all on the same trace. This single data point doesn't justify the design decision to always co-locate prefill and decode.

**6. Missing Tail Latency Analysis**

Figure 22 shows TTFT CDFs, but they flatten before reaching 1.0, meaning some requests are dropped. The paper focuses on SLO-met counts rather than P99/P99.9 latencies of *successful* requests. For serverless, tail latency is often more important than aggregate metrics.

**7. The Y-Axis Problem in Figure 22**

Look at the "Nodes Used" plots in Figure 22. The Y-axis runs from 0-4+, and they report decimal GPU usage (e.g., 0.9 GPUs). How do you use 0.9 GPUs? This appears to be time-averaged utilization, which obscures instantaneous fragmentation. A system that uses 4 GPUs for 50% of the time and 0 GPUs for 50% reports 2.0 average usage, but this is very different from steady 2-GPU usage.

### Quantitative Concerns

**Scaling Overhead (Figure 33):** Shadow validation takes 0.2-0.35ms and token-level scheduling ~0.05ms. For a 71ms TPOT (Table I, Llama-2-7B decode), this adds ~0.5% overhead. Acceptable, but they don't report tail cases where validation probes many candidates.

**KV-Cache Scaling (Figure 17):** Scaling from 32GB to 64GB takes 1.9 seconds. Under the 250ms TPOT SLO with continuous requests, this creates a 7.6-token stall. The watermark-based lazy scaling helps, but they don't report how often scaling actually occurs during experiments.

**Memory Estimation Accuracy:** Section VII-A mentions estimating output length as "at least the average output length Ō." For Azure Conversation (Figure 34), output lengths vary from 0 to 1K tokens. What's the underestimation rate? They mention "0–0.3% request migration rate" in Section IX-I5, but this is for a specific watermark setting.

## Q4: What the Authors Didn't Tell You

**1. The Real Competition Isn't ServerlessLLM**

The paper frames ServerlessLLM as the state-of-the-art, but MuxServe [24] already does spatial-temporal GPU multiplexing for multi-LLM serving. Section II dismisses it as "relies on predictable workloads, which does not hold in serverless settings"—but they don't actually compare against it. MuxServe with prediction uncertainty bounds could be a stronger baseline than ServerlessLLM + static sharing.

**2. The CPU Story Has Significant Caveats**

They don't mention:
- **Power consumption:** A 32-core Xeon 6462C has 300W TDP. Running 4 CPU nodes (1200W) versus 1 A100 (400W) for comparable throughput changes the TCO calculation.
- **Memory bandwidth:** CPUs are memory-bandwidth-limited for LLM inference. They achieve 196ms TPOT for 32-batch 1K-length on 7B (Figure 7), but this is 2.8× slower than GPU's ~70ms. The "spare CPU" argument assumes these CPUs are truly idle, not used for data preprocessing or other datacenter tasks.

**3. The Preemption Strategy Has Hidden Costs**

Section VIII-A says preempted requests are "rescheduled to other nodes." But what's the overhead of:
- Transferring KV-cache state?
- Re-prefilling if cache is discarded?
- Cold-starting the model on a new node?

They validate that preempted requests can "still meet their SLOs after rescheduling," but don't report how often preemption fails validation or what fraction of requests experience preemption.

**4. Instance Fragmentation Metrics Are Absent**

The consolidation mechanism (Section VIII) aims to reduce fragmentation, but they never report:
- Average number of instances per model
- Instance lifetime distribution
- Preemption frequency
- Consolidation success rate

These operational metrics are crucial for understanding real-world behavior.

**5. The "Mixed Deployment" Scenario (Section IX-E) Reveals Limitations**

Figure 26 shows that as larger models dominate (1:1:4:1 ratio for 3B:7B:13B:34B), SLINFER's GPU savings shrink from 2.6 to 3.6-3.8. For the pure 34B case (0:0:0:1), SLINFER matches ServerlessLLM exactly. This means:
- SLINFER provides no benefit for models ≥34B
- The 47-62% improvement headline applies only to small-model-dominated workloads
- As models grow (the historical trend), SLINFER's advantages diminish

**6. The Keep-Alive Threshold Analysis (Figure 30) Is Counterintuitive**

They recommend 1s keep-alive, showing that longer thresholds *worsen* TTFT. But this contradicts typical serverless logic where warm instances reduce latency. The explanation—"prolonged idle instances exacerbates resource contention"—suggests their system can't efficiently reclaim idle resources. This hints at memory subsystem limitations they don't fully explore.

**7. The 250ms TPOT SLO Is Historical, Not Production-Grade**

They cite "human reading speed of 250 tokens/min" [16] to justify 250ms TPOT. But modern LLM products (ChatGPT, Claude) stream much faster. Tighter SLOs would invalidate much of their CPU opportunity and force more GPU usage, shrinking the resource savings.

**8. Tensor Parallelism Complexity Is Hand-Waved**

Section IX-E mentions CodeLlama-34B uses "tensor parallelism (2 GPUs/instance)" but doesn't explain how SLINFER handles cross-GPU communication, synchronization overhead, or whether their token-level scheduling works across GPU pairs. This is listed as a limitation but the implications aren't explored.

**9. The Quantization Note (Section X) Hints at a Different Paper**

"Applying INT4 quantization reduced GPU usage from 3.8 to 2.6" for 22B models. This 32% improvement from quantization is orthogonal to SLINFER's contribution but potentially more impactful. A system combining quantization with SLINFER's scheduling could be substantially more efficient, but they leave this unexplored.

**10. Cold Start Latency Gaming**

Section IX-A admits: "we relax the TTFT requirement for such requests by allowing a grace window equal to the cold-start duration." This is fair for comparing systems with similar cold-start behavior, but it means their SLO-met numbers aren't directly applicable to real deployments where users don't get grace windows.

---

**Bottom Line:** SLINFER solves a real problem with a principled approach, and the evaluation is more thorough than typical systems papers. However, the reliance on synthetic multi-LLM workloads, generous SLO assumptions, and cutting-edge hardware requirements limit the generalizability of the claimed 47-154% improvements. The CPU opportunity is real but narrow, and the paper undersells just how constrained that sweet spot is.