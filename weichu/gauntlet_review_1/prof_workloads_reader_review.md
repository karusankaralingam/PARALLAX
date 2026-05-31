# Evaluation Methodology Audit: SLINFER

*Adjusts glasses and pulls up the experimental section*

Let me be direct with you: this paper makes some bold claims about 47%-154% improvements in serving capacity. Let's see if the numbers hold up under scrutiny.

---

## 1. Methodology Audit: What They Actually Tested

**Benchmark Suite:** Azure Serverless Trace (2019) + Azure LLM Inference Dataset (2023)

**Models:** Llama-3.2-3B, Llama-2-7B, Llama-2-13B

**Hardware:** 4× A100-80GB GPUs + 4× 32-core Intel Xeon 6462C CPUs

**This is a reasonable setup, BUT...**

Here's what concerns me immediately:

### The Trace Mismatch Problem
They're using a **serverless function trace** (Azure Functions 2019) to simulate **LLM invocation patterns**. Look at Section IX-A:

> *"Since LLM traces contain only a single model and lack the multi-model hot–cold characteristics, following ServerlessLLM, we use Azure Serverless Trace and map each LLM to a function."*

This is a **synthetic workload construction**. Real multi-tenant LLM deployments don't necessarily follow the same invocation patterns as generic serverless functions. The burstiness characteristics, request correlation, and temporal patterns could be fundamentally different.

**Question for you:** If you were deploying 64 fine-tuned LLMs for different enterprise customers, would their access patterns really look like Azure Functions from 2019?

---

## 2. The "Gotcha" Graphs

### Figure 22: Where the Magic Happens (and Doesn't)

Look carefully at Figure 22c (13B-sized cases):

| Models | SLINFER GPU Usage | sllm+c+s GPU Usage |
|--------|-------------------|---------------------|
| 32     | 2.4               | 3.3                 |
| 64     | 3.8               | 4.0                 |
| 128    | 4.0               | 4.0                 |

**Notice the convergence at 128 models.** When the system is saturated, SLINFER's advantage disappears. The paper acknowledges this:

> *"As the number of models increases or model size grows, the resource usage gap among four systems gradually narrows."*

This tells us SLINFER's benefits are **regime-dependent**. In high-load scenarios with larger models, you're back to square one.

### Figure 26: The Mixed Deployment Reality Check

This is the most honest figure in the paper. Look at the rightmost bar (0:0:0:1 - only 34B models):

All three systems use **2.2 GPUs**. SLINFER provides **zero benefit** when you can't share.

The paper's sweet spot is small models with low utilization. That's a valid use case, but it's not universal.

---

## 3. The Missing Data (What I Would Have Loved to See)

### 3.1 Tail Latency Under Contention
They show TTFT CDFs, but where's the **P99 TPOT under sustained load**? Figure 30 shows P95 TTFT, but during the decode phase—where users are actually reading—what happens to the tail?

### 3.2 Memory Fragmentation Over Time
The watermark-based KV-cache scaling (Section VII-B) is clever, but they only show **aggregate utilization** (Figure 25). What about:
- Memory fragmentation after hours of operation?
- The actual frequency of the "rare case" evictions mentioned in Section VII-D?

### 3.3 Cold Start Breakdown
They mention relaxing TTFT SLO by the cold-start duration, but don't quantify:
- What percentage of requests hit cold starts?
- How does this vary across the popularity distribution?

### 3.4 CPU Thermal Throttling
They're running 32-core Xeons at 3.3GHz under sustained matrix operations. Any thermal throttling over the 30-minute experiments? This matters for production deployments.

---

## 4. Baseline Validity: Is This a Fair Fight?

### The ServerlessLLM Comparison

ServerlessLLM is designed for **fast model loading**, not resource sharing. Comparing SLINFER's sharing capabilities against a system that wasn't designed for sharing is... convenient.

Look at their baseline configuration (Section IX-A):
> *"We tried our best to conservatively tailor a set of higher concurrency limits for sllm and sllm+c"*

They **manually tuned** the baselines to be more competitive. This is good scientific practice, but it also means the "out-of-box" ServerlessLLM numbers would look even worse—making their improvements look artificially large.

### The Missing Baseline: MuxServe
They cite MuxServe [24] as a GPU sharing system but don't compare against it directly:

> *"MuxServe adopts static GPU sharing for multi-LLM serving but relies on predictable workloads"*

Fair enough, but couldn't they have shown MuxServe's performance on the same traces? Even if MuxServe fails under bursty loads, showing *how* it fails would strengthen their argument.

---

## 5. The Cherry-Pick Check

### What's Included:
- ✅ Small models (3B, 7B, 13B)
- ✅ Moderate input lengths (mostly <4K tokens)
- ✅ Relaxed SLOs (250ms TPOT, up to 8s TTFT)

### What's Excluded or Marginalized:
- ❌ Large models (34B only appears in one experiment)
- ❌ Long-context workloads (LongBench results in Figure 35 show SLINFER using **more** resources than sllm+c+s on CPUs)
- ❌ Tight SLOs (they acknowledge CPUs fail at 50ms TPOT)

From Section IV-A2:
> *"Under a 100 ms TPOT SLO, only 7B or smaller LLMs are feasible, with batch sizes limited to 9 for 1K-length"*

So if your application needs snappy responses (real-time chat, code completion), the CPU sharing story falls apart.

---

## 6. The "Zero-Event" Reality Check

### Does the Problem Actually Exist at Scale?

The paper's premise is that serverless LLM deployments have:
1. Many small models
2. Infrequent invocations
3. Bursty access patterns

**Evidence provided:**
- Figure 2: HuggingFace download statistics (popularity ≠ deployment patterns)
- Figure 3: LMSYS-Chat-1M (25 models, academic dataset)

**Evidence NOT provided:**
- Actual production traces from cloud LLM hosting services
- Customer deployment statistics from Azure/AWS/GCP

The Azure Functions trace is from **2019**—before the LLM explosion. Are we sure serverless LLM workloads look like pre-GPT serverless functions?

---

## 7. Discussion Questions for You

1. **The CPU Opportunity Cost:** They claim CPUs are "idle" on GPU nodes. But in a real datacenter, those CPUs might be running preprocessing, logging, or other services. Is the "free CPU" assumption realistic?

2. **The Scaling Cliff:** Figure 24 shows diminishing returns when adding CPU nodes. At what point does the complexity of SLINFER's scheduling (shadow validation, memory orchestration, consolidation) outweigh the benefits?

3. **The Generalization Question:** They tested on Llama variants. Would these results hold for:
   - Mixture-of-Experts models (different memory access patterns)?
   - Vision-language models (different compute profiles)?
   - Models with different attention mechanisms (linear attention, sparse attention)?

4. **The Production Reality:** They use a 1-second keep-alive threshold. In production, you might want longer keep-alive for user experience. How does SLINFER perform with 30-second or 60-second thresholds?

---

## My Bottom Line

**What they proved:** SLINFER can improve resource efficiency for small-to-mid-sized LLMs under moderate SLOs when workloads are bursty and heterogeneous hardware is available.

**What they didn't prove:** That this generalizes to:
- Large models
- Tight latency requirements
- Sustained high-load scenarios
- Real production LLM serving patterns

The evaluation is **competent but narrow**. The benchmarks are reasonable for the claims they make, but the claims themselves are carefully scoped to where their system shines.

**Grade: B+** — Solid methodology within their chosen scope, but the scope itself is the limitation.

---

*Now, shall we dig into any specific figure or claim? I'm particularly suspicious of that 154% improvement number...*