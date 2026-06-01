# Study C — Multi-Persona Synthesis
**Paper:** 1029986 Towards Resource Efficient Serverless LLM Inference with SLINFER  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:25

---

# Q1: Whiteboard Explanation

SLINFER addresses a fundamental resource allocation problem in serverless LLM hosting: when you have 64-128 different fine-tuned models (mostly ≤8B parameters) each receiving sparse traffic (56% get <5 requests/hour per Figure 3), existing systems like ServerlessLLM allocate an *entire GPU* per model instance. This leads to catastrophic underutilization—Figure 5 shows only 23% average GPU memory utilization while 33% of requests miss SLOs due to queuing (Section III-C, Figure 4).

**The Architecture Has Three Interlocking Components:**

1. **Token-Level Compute Scheduling (Section VI):** Rather than scheduling at request granularity, SLINFER schedules at *token iteration* granularity. Multiple LLM instances share a single GPU/CPU node, but only one computes at a time. The scheduler uses "headroom" (Equation 1): `ST + TTFT_SLO + TPOT_SLO × O - CT`—essentially "how many milliseconds until this request violates its SLO?" The most urgent instance (shortest headroom) runs next. Before accepting new requests, "shadow validation" (Figure 15) simulates future execution to ensure no existing request will be starved.

2. **Watermark-Based Memory Scaling (Section VII):** KV-cache memory fluctuates up to 12× between idle and peak (Figure 9). Resizing is expensive—Figure 17 shows scaling 32GB→64GB takes 1.9 seconds. SLINFER uses watermarks (w=25%): scale up eagerly to 125% of estimated need, scale down lazily only when dropping below the watermark. The "optimistic/pessimistic" budget system (Figure 19) allows parallel resize operations without OOM—scale-downs immediately reduce logical budget (optimistic), while scale-ups wait for physical confirmation (pessimistic).

3. **Instance Consolidation (Section VIII):** When resources are tight, SLINFER prefers "scale up" over "scale out." If instance A needs to grow but instance B is blocking it, A can *preempt* B—but only if A has a larger batch size and B's evicted requests can meet SLOs elsewhere. Reactive bin-packing routes new requests to larger-batch instances, naturally starving smaller fragments for reclamation.

**The CPU Opportunity:** Table I reveals that 4th-Gen Intel Xeons with AMX achieve 6.7-7.3× speedup over 3rd-Gen for TTFT. For 7B/13B models with inputs <4K tokens, AMX-equipped CPUs can independently meet production SLOs—this isn't "CPU-assisted GPU inference" but *CPU-independent serving* with transparent GPU fallback.

# Q2: The Key Insight

The central insight is that **the resource unit for serverless LLM serving shouldn't be a whole GPU—it should be token-level compute timeslices plus dynamically-sized memory blocks**. This reframes the problem from "GPU scarcity" to "resource fragmentation."

This insight rests on three observations the paper validates:

1. **Compute demand is episodic, not continuous:** Figure 1 shows compute spikes during prefill then drops during decode. Between requests, instances are idle. LLM inference is iterative—each output token requires one decode iteration (~70-250ms), with no data dependency across instances between iterations.

2. **Memory demand is highly variable:** Figure 9 shows even under the top-1% workload, 50% of the time a 7B model uses <17GB. Static worst-case allocation wastes enormous capacity.

3. **Serverless workloads are sparse but bursty:** Figure 12 shows concurrency swings from 1 to 128 for the same model. Exclusive allocation can't absorb bursts; naive static sharing (Table II) gives only ~50% aggregate concurrency of a single large instance.

The **headroom metric** (Equation 1) is the technical lever—it converts the complex multi-instance scheduling problem into a simple "serve the most urgent first" rule. The **shadow validation** procedure (Section VI-C) enables speculative admission control by simulating future execution with 10% overestimation for safety margins.

The secondary insight—that AMX-equipped CPUs are viable standalone inference engines for small models—is significant but hardware-contingent. Table I shows 3rd-Gen Xeons are 6.7× slower, making the CPU opportunity dependent on bleeding-edge hardware.

# Q3: Evaluation Critique

## Strengths

**Realistic Workload Composition:** The evaluation combines Azure Serverless Traces for invocation patterns (Figure 21) with Azure LLM Conversation/Code datasets for token lengths (Figure 34). Testing across 5 different datasets (Figure 35) including BurstGPT traces (Figure 27) captures both multi-model serverless patterns and realistic LLM workloads—far better than synthetic Poisson arrivals.

**Comprehensive Ablation (Figure 23):** Disabling each component isolates contributions: removing sharing drops SLO rate to 89%; removing CPUs increases GPU usage from 2.5 to 3.0; removing consolidation causes resource sprawl after load spikes. This is clean experimental design.

**Honest About Limitations:** Section IV-A2 explicitly states CPUs fail under tight SLOs (100ms TPOT limits batch to 9), large models (>13B), and long inputs (>5.6K tokens for 13B). Table I showing 3rd-Gen Xeon is unusable is refreshingly transparent.

**Thorough Sensitivity Analysis (Section IX-I):** Testing varying CPU cores (Figure 29), keep-alive thresholds (Figure 30), watermark settings (Figure 31), and the watermark sweet-spot analysis at 25% demonstrates parameter robustness.

## Weaknesses

**Baseline Configuration Issues:** The `sllm` baseline uses fixed concurrency limit of 2 (Section IX-A), which authors admit "leads to extreme inefficiency." The `sllm+c+s` baseline uses fixed 50% resource partitioning—a strawman. MuxServe does adaptive spatial-temporal multiplexing but wasn't compared. The manually-tuned concurrency limits (59,15,6)/(160,32,16) weren't applied consistently across baselines.

**Limited Hardware Diversity:** All GPU experiments use A100-80GB; no H100, A10g, or AMD MI300 despite claiming "hardware-agnostic" (Section V). All CPU experiments use a single Intel Xeon 6462C. The 4th-Gen Xeon dependency is underplayed—most datacenters still run older CPUs where SLINFER's CPU benefit vanishes.

**Missing Tail Latency Analysis:** While TTFT CDFs appear in Figures 22a-c, TPOT distributions are largely absent. For interactive LLM serving, P99 TPOT matters enormously. Only Figure 30 shows P95 TTFT.

**Cold-Start Accounting:** Section IX-A states they "relax the TTFT requirement for requests that experience cold-start by allowing a grace window equal to the cold-start duration." This methodological choice hides cold-start impact in the reported numbers. The cold-start rate under different thresholds is never characterized.

**Scale Limitations:** Experiments use only 4 CPUs + 4 GPUs serving 32-128 models. Figure 33 suggests scheduling overhead grows with cluster size, but scalability beyond 8 nodes is untested. Real serverless platforms host thousands of models.

**SLO Definition Favors the Approach:** TPOT SLO = 250ms is quite generous. No experiments at 50ms or 100ms TPOT SLOs, yet many production systems target these tighter bounds.

# Q4: What the Authors Didn't Tell You

**The AMX Dependency is a Deployment Blocker:** The entire CPU-serving story requires Intel 4th-Gen Xeons with AMX (released 2023-2024). Table I shows without AMX, a 7B model takes 4.1 seconds for TTFT with 1K inputs—exceeding the 8-second SLO. The "86%-154% improvement" headline requires the full heterogeneous setup; without CPUs, improvement drops to 47%-62% (Abstract). Most existing datacenter hardware cannot realize the CPU benefits.

**NVIDIA MPS is a Hidden Requirement:** Section IX-A mentions `nvidia-cuda-mps-control -d` is enabled. MPS (Multi-Process Service) enables concurrent CUDA kernel execution from multiple processes. Without MPS, token-level interleaving would serialize at the GPU driver level, destroying the benefit. This critical deployment requirement is buried in implementation details.

**This is Temporal, Not Spatial Sharing:** Despite the "resource-efficient" framing, SLINFER doesn't partition GPU SMs or memory banks. Figure 14 shows it schedules "one instance at a time to compute one iteration"—pure temporal multiplexing. The "sharing" is that multiple instances' weights stay resident in GPU memory simultaneously. True spatial sharing (MPS partitioning, MIG) would allow concurrent kernel execution.

**Token-Level Scheduling Creates Head-of-Line Blocking:** When Instance A generates a token, Instances B, C, D wait. Decode iterations (~71ms for 7B on CPU) are manageable, but prefill can take 567ms for 1K tokens (Table I). During that 567ms, all other instances are blocked. Shadow validation mitigates but doesn't eliminate this fundamental serialization.

**Memory Operations Have Hidden Costs:** Figure 17 reveals KV-cache scaling requires allocating new blocks, copying used cache pages, and deleting old blocks—a full stop-and-copy operation. The "watermark" mechanism is really a heuristic to *avoid* scaling, not to make it efficient. The 1.4% "scaling overhead" in Figure 31 seems suspiciously low given these mechanics.

**Preemption Has Unexamined Cascades:** Section VIII-A's proactive preemption allows evicting smaller-batch neighbors. Preempted requests must pass shadow validation for rescheduling—but if the cluster is congested enough to trigger preemption, won't shadow validation fail too? The 0-0.3% migration rate (Section IX-I5) is measured under their specific workloads; adversarial patterns could spike this.

**The Keep-Alive Sensitivity Reveals Scheduling Limitations:** Figure 30 shows P95 TTFT *increases* when extending keep-alive from 1s to 8s. Their explanation—"prolonged idle instances exacerbate resource contention"—implies the scheduling algorithm doesn't handle mixed warm/cold instances well. Production serverless systems typically use 10-15 minute keep-alive.

**No Tensor Parallelism Support for Sharing:** Section IX-E mentions CodeLlama-34B uses 2-GPU tensor parallelism with *exclusive* allocation. SLINFER's sharing only works for models fitting on a single device, limiting applicability as 70B+ models become common.