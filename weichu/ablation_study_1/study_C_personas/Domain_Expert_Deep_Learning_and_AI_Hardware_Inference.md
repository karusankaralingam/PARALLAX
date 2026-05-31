# Paper Deconstruction: SLINFER (HPCA 2026)

## Q1: Whiteboard Explanation

Let me sketch this for you as if we're at a whiteboard after a seminar.

**The Problem Setup:**
Imagine you're a cloud provider hosting private LLMs for thousands of users. Most of these models are small (3B-13B parameters), and most users invoke them infrequently—56% of LLMs on LMSYS get fewer than 5 requests per hour (Figure 3). Current serverless systems like ServerlessLLM allocate an *entire GPU* to each model when a request arrives. This creates a massive mismatch: when hosting 64 models on 4 A100-80GB GPUs, 33% of requests miss their SLOs due to queuing, yet average GPU memory utilization is only 23% (Figure 5). The GPUs are mostly sitting there with loaded model weights, doing nothing.

**The Core Mechanism:**
SLINFER's insight is deceptively simple: *share* the hardware across multiple LLM instances, both temporally and spatially. But the devil is in the details of *how* to share without causing SLO violations.

Here's the architecture (Figure 13):
1. **Unified Hardware Abstraction**: SLINFER treats both CPUs (with Intel AMX accelerators) and GPUs as interchangeable "nodes" that can host LLM instances. The key realization is that modern Intel Xeon CPUs with AMX can actually meet production-grade SLOs for small models—Table I shows a 4th Gen Xeon achieves 567ms TTFT for 7B models with 1K input tokens, down from 4113ms on 3rd Gen (a 7.3x speedup from AMX).

2. **Headroom-Driven Compute Scheduling (§VI)**: This is the clever bit. Each request has a "headroom"—the time buffer before it would violate its SLO. The formula (Equation 1) is straightforward: `headroom = StartTime + TTFT_SLO + TPOT_SLO × OutputTokens - CurrentTime`. At every scheduling cycle, SLINFER picks the instance with the *shortest* headroom across all co-located instances on a node (Figure 14). This is essentially an Earliest Deadline First (EDF) scheduler at token granularity.

3. **Shadow Validation (§VI-C)**: Before accepting a new request, SLINFER *simulates* the future execution to check three failure modes (Figure 15): (a) Will the new request's TTFT be violated? (b) Will existing requests be delayed past their TPOT? (c) Will the aggregate decode time across all instances exceed TPOT_SLO? Only if all checks pass does the request get accepted.

4. **Hazard-Aware Memory Subsystem (§VII)**: Multiple instances dynamically resize their KV-caches. This is dangerous—uncoordinated scaling can cause OOM (Figure 18). SLINFER uses a clever dual-accounting scheme: an *optimistic* budget for issuing operations and a *pessimistic* budget for actually executing them. Scale-down operations execute immediately but notify a reservation station; scale-up operations wait in the reservation station until it's safe.

5. **Consolidation (§VIII)**: When instances fragment across nodes (e.g., same model running with batch=2 on one node and batch=3 on another), SLINFER tries to consolidate. Proactive preemption lets a larger instance "evict" a smaller co-located instance. Reactive bin-packing routes new requests to the largest instance, letting smaller ones drain and die.

**The Punchline:**
The system achieves 47-62% improvement in serving capacity through GPU sharing alone, and 86-154% when adding CPUs (Abstract). The key enabler is *elastic, on-demand resource allocation at token granularity* rather than coarse-grained exclusive allocation.

---

## Q2: The Key Insight

**The Delta (What's Actually New):**

The *real* contribution isn't any single component—it's the *composition* of multiple mechanisms into a coherent system that enables fine-grained resource sharing for serverless LLM inference. Let me separate mechanism from policy:

**Mechanism Innovations:**
1. **Token-level scheduling with headroom-based prioritization**: While EDF scheduling is ancient, applying it at *per-token* granularity for LLM inference is novel. The insight that you can interleave decode iterations across multiple instances while still meeting SLOs is non-obvious.

2. **Shadow validation for admission control**: The three-case validation (Figure 15) that simulates future execution before accepting requests is a clean solution to the question "can I take this request without breaking my existing commitments?"

3. **Dual-accounting memory orchestration**: The optimistic/pessimistic budget scheme (Figure 19) is a textbook-worthy solution to the hazard problem in concurrent memory operations.

**Policy Insights:**
1. **CPUs with AMX can independently serve small LLMs**: Table I and Figures 6-8 show that 4th Gen Xeons can meet SLOs for ≤13B models with ≤4K input tokens. This isn't just "offload to CPU as backup"—it's "CPUs are first-class citizens for inference."

2. **Watermark-based lazy scaling**: Rather than aggressively resizing KV-cache on every request, using a 25% watermark (§VII-B, Figure 31) with early scale-up and lazy scale-down reduces scaling overhead from 11.3% to 1.4% of instance lifetime.

3. **Vertical scaling through preemption beats horizontal fragmentation**: The insight that letting a larger instance evict a smaller neighbor (proactive consolidation) is better than creating a new fragmented instance is subtle but important for efficiency.

**What's NOT New:**
- Using CPUs for LLM inference (NEO [32], FastDecode [29], PowerInfer [62] all do this)
- Paged attention for KV-cache (vLLM [37])
- Serverless LLM serving (ServerlessLLM [26], Medusa [72])
- Continuous batching (Orca [71])

The magic is in showing that these pieces can be orchestrated together to enable multi-tenant sharing while maintaining SLOs. This is systems integration done right.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

**1. Comprehensive Baseline Comparison (§IX-B):**
The authors compare against ServerlessLLM (`sllm`), with two fair extensions: `sllm+c` (adding CPU support) and `sllm+c+s` (adding time-sharing). This isolates the contributions of CPU utilization vs. dynamic sharing. Critically, they manually tuned concurrency limits for baselines (59/15/6 for 3B/7B/13B on GPU) rather than using defaults—showing intellectual honesty.

**2. End-to-End Realism:**
- Real hardware: 4×A100-80GB GPUs, 4×32-core Intel Xeon 6462C CPUs
- Real workloads: Azure LLM Trace [54] for request characteristics, Azure Serverless Trace [61] for invocation patterns
- Real models: Llama-3.2-3B, Llama-2-7B, Llama-2-13B
- Proper SLO definitions matching prior work [16, 75]: TTFT = min(max(0.5, L/512), 8)s, TPOT = 0.25s

**3. Ablation Study (§IX-C, Figure 23):**
Disabling each component shows meaningful degradation. Notably, disabling sharing drops SLO rate to 89%—validating that sharing is the critical enabler. The timeline view shows consolidation specifically helps during load fluctuations.

**4. Sensitivity Analysis (§IX-I):**
Testing across five different LLM datasets (Figure 34-35), different invocation traces (BurstGPT, Figure 27), varying CPU resources (Figure 29), keep-alive thresholds (Figure 30), and watermark values (Figure 31) demonstrates robustness.

### Weaknesses:

**1. Limited Model Scale:**
All experiments use models ≤13B parameters. The authors acknowledge in §X that "for large models, SLINFER falls back to ServerlessLLM's exclusive allocation approach." Figure 26 shows that when large models dominate (1:1:4:1 popularity ratio with 34B models), SLINFER's advantage shrinks to near-zero. This is a significant scope limitation given that many production workloads involve 70B+ models.

**2. Questionable CPU Deployment Assumption:**
The claim that "clusters have abundant idle CPUs" (§IV) relies on citations [31, 33] about GPU cluster CPU utilization. However, Figure 28 shows vLLM uses <1 CPU core even with 8 co-located instances. The question is: in practice, will cloud providers actually *let* users consume those CPU cores for LLM inference, or are they reserved for other tenants/purposes? The paper assumes frictionless access to spare CPUs.

**3. Memory Bandwidth Not Analyzed:**
For LLM inference, memory bandwidth is often the bottleneck. The paper never reports memory bandwidth utilization on GPUs. When serving 8 instances on one GPU (Figure 25 shows batch sizes up to 16), are they hitting the HBM bandwidth wall? The 4% degradation in Figure 25's batch size improvement for SLINFER vs. sllm might be masking bandwidth saturation.

**4. Cold Start Still Matters:**
Despite using ServerlessLLM's fast loader, the authors admit "requests that experience cold-start may still violate the TTFT SLO" and add a "grace window" (§IX-A). This is a significant confounder. How many requests actually benefit from this grace period? The paper doesn't report cold-start frequency separately from SLO metrics.

**5. Missing Head-to-Head with MuxServe:**
MuxServe [24] (cited in §II) also does spatial-temporal GPU multiplexing for multi-LLM serving. The authors dismiss it as requiring "predictable workloads" but never experimentally compare. Given that MuxServe is the closest related work, this omission is glaring.

**6. Prefill-Decode Disaggregation Strawman:**
Table III shows PD disaggregation hurts performance, but the implementation details are sparse. The 100Gbps bandwidth seems adequate, but the claim that "prefill instances spend 93% of their lifetime on cold starts or idle" suggests a naive implementation without instance sharing for the prefill stage.

---

## Q4: What the Authors Didn't Tell You

**1. The Intel Marketing Angle:**
The entire CPU utilization story hinges on Intel AMX. Table I shows 3rd Gen Xeon (without AMX) gets 4113ms TTFT vs. 567ms with 4th Gen (with AMX)—a 7.3x difference. The paper positions this as "modern CPUs can serve LLMs," but the reality is "Intel's newest CPUs with specialized matrix accelerators can serve small LLMs under relaxed SLOs." What about AMD CPUs? ARM-based Graviton instances? The generalizability is unclear.

**2. The 250ms TPOT SLO is Generous:**
Human reading speed is ~250 tokens/min, implying 240ms/token is acceptable (§III-A). But many production systems target 50-100ms TPOT for responsive chat interfaces. The authors acknowledge in §IV-A2 that "at 100ms TPOT SLO, only 7B or smaller LLMs are feasible" on CPU, and "at 50ms, even 7B LLMs become infeasible." The CPU story completely falls apart under tighter SLOs.

**3. The Scaling Overhead is Hidden:**
Figure 17 shows KV-cache scaling overhead: 0.3s to scale down 32GB to 16GB, and 1.9s to scale up to 64GB. With TPOT SLO of 250ms, a 1.9s scale-up operation blocks ~8 tokens of decode. The watermark mechanism (§VII-B) mitigates this, but the authors don't report how often scale-up operations actually delay tokens. Figure 31 shows 1.4% overhead at 25% watermark, but this is aggregate—what's the P99 latency impact?

**4. The "86-154% improvement" Number is Cherry-Picked:**
This headline number (Abstract, Figure 22) compares SLINFER against *baseline* ServerlessLLM (`sllm`), not against `sllm+c+s`. The apples-to-apples comparison with `sllm+c+s` (which also uses CPUs and sharing) shows only 18-70% improvement. The larger number requires you to give SLINFER credit for the *entire* benefit of using CPUs, which isn't a novel contribution.

**5. Instance Fragmentation Metrics Missing:**
The consolidation mechanism (§VIII) is claimed to reduce fragmentation, but the paper never reports fragmentation metrics directly. How often does proactive preemption trigger? What's the success rate of reactive bin-packing? Figure 23's ablation shows GPU usage increases without consolidation, but this is an indirect proxy.

**6. vLLM Modification Depth Unknown:**
The paper mentions "modified vLLM" (Appendix D.2) but doesn't detail what was changed beyond the standard API. The token-level scheduling (§VI-A) requires intercepting vLLM's internal batch management. How invasive are these changes? Would they break with vLLM updates? The released code should clarify this, but the paper doesn't discuss engineering complexity.

**7. Request Migration Cost:**
When memory underestimation occurs, "SLINFER will evict and re-schedule the request with the longest headroom" (§VII-D). What's the cost of this migration? The KV-cache for a long-running request could be gigabytes. Figure 31 mentions 0-0.3% migration rate, but the latency impact of individual migrations isn't reported.

**8. The Real Competition is Cloud Provider Optimizations:**
The paper compares against academic systems (ServerlessLLM, vLLM), but cloud providers like AWS (SageMaker), Azure (ML), and GCP (Vertex AI) have proprietary optimizations for serverless inference. The paper's framing suggests deployment on these platforms, but the actual competitive landscape is opaque.

**9. Mixed Precision Overlooked:**
All experiments use 16-bit precision models. Section X briefly mentions that INT4 quantization "reduced GPU usage from 3.8 to 2.6" for 22B models, but this is a single data point. Quantization is standard practice for serving—the paper should have systematically evaluated how quantization affects the sharing potential.

**10. The "Serverless" Framing is Loose:**
True serverless implies per-invocation billing and zero infrastructure management. SLINFER still requires dedicated CPU/GPU nodes provisioned in advance. The paper uses "serverless" to mean "event-driven instance management," which is a weaker definition than what AWS Lambda or Google Cloud Functions provide. This isn't wrong, but readers should understand the paper addresses multi-tenant LLM serving, not serverless in the FaaS sense.