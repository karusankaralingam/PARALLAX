## Q1: Whiteboard Explanation

Let me walk you through the wiring diagram of WindServe by reverse-engineering Figure 4.

**The Starting Point: Phase-Disaggregated (PD) Architecture**

The baseline here is DistServe [45], which separates LLM inference into two physically distinct GPU pools:
- **Prefill Instance**: Handles the compute-bound prompt processing (all input tokens processed in parallel)
- **Decoding Instance**: Handles the I/O-bound autoregressive token generation (one token at a time)

Between them flows the KV cache—the key/value tensors that must be transferred after prefill completes. For OPT-13B with a 2048-token context, this is ~1.5GB per request (Section 2.2). On PCIe Gen4, that's ~65ms transfer time—potentially several decoding iterations worth of latency.

**The Problem WindServe Solves**

The authors observe in Figure 1 that under high load, the static assignment of GPUs to prefill vs. decode creates imbalance:
1. Prefill queues back up → TTFT suffers (Figure 3a shows 0.066s vs 0.179s average prefill queuing with different configs)
2. Decode instance runs out of KV cache blocks → swapping to CPU (Figure 1a shows ~2500 swapped blocks at 4 req/s)
3. Meanwhile, the *other* instance has idle resources (Figure 2 shows tensor core utilization only 20-60%)

**WindServe's Three-Part Solution**

1. **Dynamic Prefill Dispatch** (Algorithm 1, Section 3.2.2): When the Profiler predicts TTFT will exceed threshold `thrd`, AND the decode instance has available "slots" (memory + compute budget), dispatch new prefill jobs to the decode instance instead. The magic number here is `budget`—the maximum prefill tokens that won't violate TPOT SLO in a single forward pass.

2. **Stall-free Rescheduling** (Figure 6, Section 3.3): When decode instance KV blocks are nearly exhausted, migrate long-context requests *back* to prefill instance. The trick: don't block decoding—keep generating tokens while KV cache transfers in the background, only pausing when transfer is nearly complete.

3. **Stream-based Disaggregation** (Figure 7, Section 3.4): When prefill and decode jobs must coexist on the same GPU (due to dispatch), run them in separate CUDA blocking streams. This exploits Hyper-Q's 32 independent hardware queues (Kepler+) to overlap execution rather than serializing or batching them together.

**The Data Flow**

```
New Request → Global Scheduler (Profiler + Coordinator)
                    ↓
    [If TTFT_pred > thrd AND slots available]
         → Decode Instance (Prefill Stream + Decode Stream)
    [Else]
         → Prefill Instance → KV Transfer → Decode Instance
                    ↓
    [If decode KV blocks exhausted]
         → Stall-free migration back to Prefill Instance
```

---

## Q2: The Key Insight

**The "Magic Trick":** WindServe's central insight is that **CUDA streams provide zero-cost, runtime-reconfigurable resource sharing**—unlike MIG, MPS, or time-slicing, which require static partitioning or process-level reconfiguration.

Let me decode this from Section 3.4: The authors explicitly compare five GPU sharing technologies (Streams, MPS, Time-Slicing, MIG, vGPU) and choose streams because:
- MIG: Disables P2P communication between GPUs, has fixed partition specs
- MPS: Only reconfigurable at process launch
- Time-Slicing: Inappropriate for latency-sensitive tasks
- vGPU: No inter-partition communication

Streams let them dynamically decide "this iteration, run prefill in stream A and decode in stream B" without any reconfguration overhead. The cost? Poor isolation—they're "directly sharing the whole GPU resources" (Section 3.4), meaning a memory-hungry prefill kernel can still starve a decode kernel of SMs.

**The Hardware Reality:** Figure 8 shows the actual numbers. For LLaMA2-70B with 2048 prefill tokens:
- Regular hybrid batch: Decode latency spikes from ~0.35s baseline to ~0.5s
- Stream-based disaggregation: Decode latency stays at ~0.34s
- Prefill cost with SBD: ~0.75s vs ~0.5s baseline (1.5× overhead)

So the trade-off is: **prefill pays a 50% latency tax to protect decode latency**. This makes sense for chatbot SLOs where TPOT matters more than TTFT at the tail.

**The Profiler's Role:** Equations 1 and 2 (Section 3.2.1) model prefill as `T = aN + bN² + c` (compute-bound, quadratic in tokens) and decode as `T = a∑L + c` (I/O-bound, linear in total context). These are fitted via "quadratic regression before runtime." The Profiler enables the Coordinator to predict whether dispatching a prefill job will violate SLOs.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Ablation Study (Section 5.4, Figure 13):**
The authors actually decompose their gains. WindServe-no-split (without stream disaggregation) shows TPOT P99 jumping from ~0.1s to ~0.35s on Longbench. WindServe-no-resche (without dynamic rescheduling) shows TPOT P99 degradation from ~0.1s to ~0.4s on ShareGPT. This proves both mechanisms contribute independently.

**2. Diverse Workload Characterization (Table 2):**
They test both chatbot (ShareGPT: avg 768 prompt / 196 output tokens) and summarization (LongBench: avg 2890 prompt / 97 output tokens)—workloads with fundamentally different prefill/decode ratios. The fact that WindServe performs well on both (Figures 10a-d) demonstrates robustness.

**3. Bottleneck Adaptivity (Figure 12):**
This is the money shot. In [TP-2, TP-1] config, TPOT limits SLO attainment—WindServe's Dynamic Rescheduling helps. In [TP-2, TP-2] config, TTFT is the bottleneck—Dynamic Prefill Dispatch helps. The system self-adapts without replanning.

### Weaknesses

**1. Single-Node Only:**
Section 7 explicitly admits: "we were unable to evaluate our WindServe in a multi-node setting." The testbed (Figure 9) has only 8 GPUs with NVLink pairs connected via PCIe. In production clusters, KV cache would traverse RDMA/InfiniBand with different latency characteristics. The 65ms PCIe transfer assumption may not hold.

**2. The Threshold `thrd` is a Tunable Knob:**
Figure 5 shows SLO attainment is sensitive to threshold setting. For OPT-13B, optimal is ~0.2s; for LLaMA2-13B, it's ~2.0s. The paper says they "set the threshold slightly below the TTFT SLO," but this requires knowing the SLO a priori and assumes SLOs don't change dynamically.

**3. Stream-based Disaggregation I/O Overhead:**
Section 7 (Limitations) acknowledges: "the independent execution of kernels doubles the model's I/O overhead." When prefill and decode run in separate streams, they each load model weights independently. For a 13B model at FP16, that's ~26GB of redundant HBM→SRAM transfers per forward pass.

**4. GQA Reduces Their Advantage:**
Figure 10d shows the TPOT improvement for LLaMA2-70B (GQA) is much smaller than LLaMA2-13B (MHA). The authors note "GQA reduces the size of the KV cache tensors, thereby decreasing the transmission overhead." As newer models adopt GQA universally, WindServe's KV migration optimizations become less impactful.

**5. No Comparison with Sarathi-Serve:**
The authors cite SARATHI [1, 2] repeatedly and acknowledge chunked-prefill as an alternative, but only compare against vLLM's chunked-prefill (v0.4.2), not against Sarathi-Serve (OSDI'24). Given that Sarathi-Serve specifically targets the prefill-decode interference problem, this is a notable omission.

---

## Q4: What the Authors Didn't Tell You

**1. The Memory Overhead of Dual Stream Execution:**
Section 4 mentions they had to "allocate enough GPU memory to store [intermediate variables] when initializing the inference engine" to avoid implicit CUDA synchronization. How much? For OPT-13B, each forward pass needs input token buffers, projection buffers, and attention outputs. Running two streams means 2× these buffers. On an 80GB A800, this could easily be 5-10GB of "hidden" reservation that reduces effective KV cache capacity.

**2. The NCCL Communicator Multiplication:**
"When WindServe triggers Stream-based Disaggregation, each stream uses a separate NCCL communicator" (Section 4). NCCL communicators are heavyweight objects with their own memory pools. For tensor parallelism, you need P-1 connections per GPU. Doubling communicators means doubling the memory footprint of the communication layer.

**3. The Profiler Accuracy is Unquantified:**
Equations 1 and 2 model prefill/decode time, but the paper never reports prediction accuracy. What's the R² of the quadratic fit? What's the 95th percentile prediction error? If predictions are off by 20%, the Coordinator makes wrong dispatch decisions, potentially oscillating between overloading prefill and decode instances.

**4. The "Budget" Calculation is Hand-Waved:**
Algorithm 1 references `budget`—"the maximum number of prefill tokens that do not exceed the TPOT SLO in a single forward pass"—determined via "simulation and profiling before runtime." But this is a function of current decode batch size, context lengths, and GPU state. A static budget calculated offline may not match runtime conditions.

**5. Stall-Free Rescheduling's "Threshold" is Undefined:**
Section 3.3 says "Once the remaining KV cache to be transferred falls below a certain threshold, the decoding instance pauses decoding for that request." What threshold? This is a critical parameter that determines whether you get "stall-free" behavior or a long blocking pause. No sensitivity analysis is provided.

**6. The Ray Overhead:**
"Each serving instance is backed by a parallel inference engine that employs Ray actors" (Section 4). Ray introduces non-trivial scheduling overhead—typically 0.5-2ms per task dispatch. At 4 req/s with continuous batching, that's potentially 4-16ms/s of overhead, which matters when TPOT targets are 100ms.

**7. KV Backup Strategy is Opaque:**
Section 3.3 mentions "the prefill instance dynamically backs up the KV cache of some long-context requests when there is sufficient KV blocks." What triggers backup? What's the storage cost? If you're backing up 10% of requests' KV caches in prefill instance, that's 10% less memory for actual prefilling.

**8. The PCIe Bandwidth Assumption:**
The paper assumes PCIe Gen4 x16 at 32 GB/s (Section 2.2). But Figure 9 shows cross-NUMA transfers go through the Root Complex. Real-world QPI bandwidth between NUMA nodes is ~40GB/s bidirectional, but latency is significantly higher. The 65ms KV transfer estimate may be optimistic for cross-socket communication.