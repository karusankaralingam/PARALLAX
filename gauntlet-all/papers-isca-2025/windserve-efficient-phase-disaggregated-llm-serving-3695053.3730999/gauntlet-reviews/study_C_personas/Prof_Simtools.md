## Q1: Whiteboard Explanation

**WindServe in 5 Minutes: Phase-Disaggregated LLM Serving Done Right**

Picture this: You have an LLM serving system where requests go through two phases—**prefill** (compute-bound, processes all prompt tokens) and **decode** (I/O-bound, generates tokens one at a time). The hot architecture trend is to run these on *separate* GPU instances (Phase-Disaggregated, or PD architecture).

**The Problem WindServe Solves:**
Existing PD systems like DistServe use static scheduling—they allocate GPUs to prefill/decode instances and hope the workload matches. But reality is messy:
- When prefill instances overload → long TTFT queuing delays
- When decode instances run out of KV cache blocks → swapping to CPU memory, killing TPOT
- Meanwhile, the *other* instance might be sitting idle (Figure 1, Figure 2)

**WindServe's Three-Part Solution:**

1. **Global Scheduler with Dynamic Prefill Dispatch** (§3.2): A coordinator monitors both instances. When the prefill queue is too long (predicted TTFT exceeds threshold), it dispatches prefill jobs to the decode instance's idle compute. This is enabled by a Profiler that models iteration time as `T_prefill = aN + bN² + c` and `T_decode = a∑L + c` (Equations 1-2).

2. **Stall-free Rescheduling** (§3.3): When the decode instance is memory-starved, WindServe migrates KV cache of *long-context* requests back to the prefill instance. The key trick: decoding continues *during* the transfer—no blocking until the transfer is nearly done (Figure 6).

3. **Stream-based Disaggregation** (§3.4): When prefill and decode jobs must coexist (after dynamic dispatch), they run in separate CUDA streams. This exploits Hyper-Q for concurrent kernel execution, reducing interference compared to hybrid batching or chunked-prefill (Figure 7, Figure 8).

**The Intuition:** Instead of static GPU-level allocation, WindServe does *runtime job-level* scheduling, treating both instances as a shared resource pool.

---

## Q2: The Key Insight

**The Insight:** Phase-disaggregated LLM serving suffers from *asymmetric resource bottlenecks* that shift dynamically with workload, and the solution is to treat the prefill and decode instances not as isolated units but as a **shared resource pool** with fine-grained, workload-aware job migration.

**Why It Matters:**
The prefill instance has compute; the decode instance has memory (KV cache storage). But existing PD systems waste both:
- Prefill instances discard KV cache after transfer → decode instance becomes the sole KV storage (§2.2, "Insufficient and uneven resource utilization")
- Decode instances have spare compute cycles (Figure 2 shows decode tensor core utilization ~15-25%)

WindServe's conceptual leap is that **phase separation doesn't mean instance isolation**. By allowing jobs to *cross* instances at runtime—prefill jobs can temporarily execute on decode instances, and decode jobs can migrate back to prefill instances—the system adapts to wherever the bottleneck is.

**Why This Wasn't Obvious:**
Prior work (DistServe, Splitwise) focused on *static* placement optimization, treating the prefill/decode split as a deployment-time decision. WindServe recognizes this is insufficient because:
1. Workload patterns vary (ShareGPT has high variance in prompt lengths—Table 2 shows avg 768, P90 1556)
2. The bottleneck shifts during execution (see Figure 3: [TP-2,TP-1] is decode-limited, [TP-2,TP-2] is prefill-limited)

The stream-based disaggregation is the enabler: it makes co-location *tolerable* by reducing interference to ~10% overhead (Figure 8), turning what was a forbidden zone into a valid scheduling option.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real Hardware, Real Models:** All experiments run on actual NVIDIA A800-80GB GPUs (§5.1), not simulation. They test OPT-13B, OPT-66B, LLaMA2-13B, and LLaMA2-70B—spanning 13B to 70B parameters. This builds confidence that the techniques work in practice.

2. **Realistic Workloads:** ShareGPT (chatbot) and LongBench (summarization) represent meaningfully different distributions—ShareGPT has long outputs (avg 196 tokens), LongBench has long inputs (avg 2890 tokens) with short outputs (Table 2). This stress-tests both TTFT and TPOT.

3. **Comprehensive Metrics:** They report median TTFT, P99 TTFT, P90 TPOT, P99 TPOT, *and* SLO attainment rates (Figures 10, 11). This is important because a system can game median latency while having terrible tail latency.

4. **Strong Ablation Studies:** Figure 13 isolates the contribution of Stream-based Disaggregation (13a) and Dynamic Rescheduling (13b). WindServe-no-split shows TPOT P99 degrades ~2× at high rates; WindServe-no-resche shows TPOT P99 degrades ~1.5×. This confirms both techniques matter.

5. **Bottleneck-Awareness Demonstration:** Figure 12 explicitly shows WindServe adapting to different bottleneck scenarios ([TP-2,TP-1] vs [TP-2,TP-2]), validating the dynamic scheduling claim.

### Weaknesses

1. **Single-Node Testbed Only:** Section 7 acknowledges "we were unable to evaluate WindServe in a multi-node setting." This is a significant limitation. In production, LLM serving clusters span multiple nodes with GDR/InfiniBand interconnects. The 65ms PCIe KV cache transfer they mention (§2.2) would balloon to hundreds of milliseconds cross-node, potentially invalidating the stall-free rescheduling claims.

2. **Profiler Accuracy Not Quantified:** The Profiler uses quadratic regression (Equations 1-2) with parameters "obtained by profiling and quadratic regression before runtime" (§3.2.1). But they never show prediction error. How well does `T̂_prefill = a_p N + b_p N² + c_p` actually fit? Attention mechanisms with FlashAttention-2 optimizations may not follow this cleanly. The paper admits "due to certain optimizations in the attention mechanism, the attention elapsed time during the prefill phase is more linearly related to N"—so why use a quadratic model?

3. **Limited Baseline Comparisons:** They compare against DistServe and vLLM (v0.4.2). Missing: Splitwise [29], SARATHI-Serve [1], and Orca [42]—all cited in Related Work as relevant systems. vLLM v0.4.2 is from early 2024; by June 2025 (publication date), vLLM had significant improvements.

4. **Stream-based Disaggregation Overhead Characterization is Shallow:** Figure 8 shows single-forward-pass latencies, but real serving involves continuous batching with dynamic batch composition. The paper admits "independent execution of kernels doubles the model's I/O overhead" (§7, Limitations), yet this isn't quantified in evaluation.

5. **Threshold Sensitivity:** Figure 5 shows SLO attainment varies significantly with the overload threshold (0.1s to 0.5s for OPT-13B). They set it "slightly below the TTFT SLO"—but this requires knowing the SLO a priori and implies manual tuning per deployment. No automatic threshold adaptation is proposed.

6. **KV Cache Transfer Bandwidth Assumptions:** The paper assumes NVLink (400 GB/s bidirectional) for within-pair GPUs and PCIe Gen4 (64 GB/s bidirectional) otherwise (§5.1). But their topology (Figure 9) shows only 2-GPU NVLink bridges—meaning most transfers go over PCIe. They don't break down results by transfer path.

---

## Q4: What the Authors Didn't Tell You

### The Infrastructure Reality They Glossed Over

1. **The NVLink Topology is Peculiar:** Figure 9 reveals their testbed has NVLink *only* between pairs of GPUs (0-1, 2-3, etc.), not the full mesh you'd see in DGX systems. This means their [TP-2, PP-1] placements carefully avoid cross-pair transfers. What happens when you need 4-way tensor parallelism? They don't say.

2. **NCCL Communicator Overhead:** Section 4 mentions "each stream uses a separate NCCL communicator to avoid synchronization and blocking." But creating multiple NCCL communicators has memory overhead (~100MB+ per communicator for large models). For the 70B model with tensor parallelism, this could eat significant GPU memory—unreported.

3. **The "Stall-free" Migration Isn't Truly Stall-free:** Section 3.3 states "Once the remaining KV cache to be transferred falls below a certain threshold, the decoding instance pauses decoding for that request." So there *is* a stall at the end. How long? What's the threshold? Figure 6 shows the concept but no timing data.

4. **Chunked-Prefill in Prefill Instance is a Fallback:** When Dynamic Rescheduling sends decode jobs to the prefill instance, "the prefill jobs in it would be converted to chunked-prefill fashion" (§3.3). But they chose *not* to use Stream-based Disaggregation in the prefill instance because "its scheduling policy would be highly dependent on the Profiler's predicted completion time, leading to a lack of robustness" (§3.4). This admission suggests the Profiler isn't robust enough for bidirectional use.

5. **Memory Management Hacks:** Section 4 mentions "we allocate enough GPU memory to store [intermediate variables] when initializing the inference engine and design a naive memory management mechanism." This pre-allocation conflicts with PagedAttention's dynamic allocation philosophy. How much memory do they pre-allocate? Does this reduce the KV cache capacity?

6. **The SLO Numbers are Generous:** Table 4 sets TTFT SLO at 4s for LLaMA-13B on LongBench—but the median input is 2887 tokens (Table 2). Even without queuing, prefill for 2887 tokens takes ~1-2s. A 4s SLO means they're allowing 2-3s of queuing delay as acceptable. For chatbot (ShareGPT), the 0.25s TTFT SLO with 768 avg tokens is tighter but still generous for conversational use.

7. **Group Query Attention Impact is Underexplored:** They note LLaMA2-70B uses GQA, which "reduces the size of the KV cache tensors, thereby decreasing the transmission overhead" (§5.2). But GQA also changes the compute/memory ratio of the decode phase. Does Stream-based Disaggregation work differently for GQA models? Unaddressed.

8. **No Power or Cost Analysis:** Phase disaggregation's value proposition includes cost efficiency through heterogeneous GPU allocation (§7, Future Work mentions RTX 4090 for prefill). But they don't measure power consumption or discuss TCO implications of their scheduling decisions.