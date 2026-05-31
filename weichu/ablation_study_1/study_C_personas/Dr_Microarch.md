# SLINFER Architecture Deconstruction

## Q1: Whiteboard Explanation

Let me draw you the actual execution flow here, because the block diagram in Figure 13 obscures what's really happening at the microarchitectural level.

**The Core Problem They're Solving:**
Existing serverless LLM systems (like ServerlessLLM) give each model instance an entire GPU exclusively. When you have 64 small models (3B-13B parameters) and only 4 GPUs, most requests queue forever—Figure 4 shows 33% SLO violations despite only 23% average GPU memory utilization (Figure 5). The resource is wasted because each instance claims a whole GPU but rarely uses it fully.

**The Hardware Insight:**
The paper makes two key observations about modern hardware:
1. **Intel AMX (Advanced Matrix Extensions)** on 4th-gen Xeon CPUs can now handle small LLMs independently—Table I shows 6.7-7.3× TTFT speedup versus 3rd-gen Xeon. This isn't just "CPUs are faster"; it's a specific hardware block for matrix ops.
2. **GPU memory is over-provisioned**: A 7B model needs ~14GB base memory, but instances get allocated 80GB on an A100.

**How SLINFER Actually Works at Runtime:**

*Step 1: Request Arrives*
- Compute subsystem calculates "headroom" per Equation (1): `headroom = ST + TTFT_SLO + TPOT_SLO × O - CT`
- This is essentially a deadline-based priority scheduler at token granularity.

*Step 2: Shadow Validation (The Critical Path)*
- Before dispatching, SLINFER simulates future token generation (Figure 15)
- Uses 2D linear interpolation from pre-profiled tables (Section VI-B) to estimate iteration time
- Table lookups are O(log L_max × log B_max)—they claim "few hundred samples" for profiling

*Step 3: Token-Level Time-Multiplexing*
- Figure 14 shows the actual scheduling: one instance computes one iteration, then yields
- This is NOT spatial partitioning—it's temporal multiplexing at iteration boundaries
- The scheduler picks instance with shortest headroom each cycle

*Step 4: Memory Orchestration*
- KV-cache scales dynamically using watermarks (Section VII-B)
- Key mechanism in Figure 19: optimistic budgeting for scale-down (immediate budget update), pessimistic execution for scale-up (reservation station queues operations)
- This prevents OOM races when multiple instances resize simultaneously

**The Actual Hardware Configuration:**
- 4× 32-core Intel Xeon 6462C (with AMX)
- 4× A100-80GB GPUs
- CPUs handle small models (≤13B) with inputs ≤5.6K tokens
- GPUs handle larger models or longer sequences

## Q2: The Key Insight

The **one clever hardware insight** is the **token-granularity time-multiplexing** combined with **headroom-based deadline scheduling**. 

Let me be precise: The "magic trick" is that LLM inference is fundamentally iterative—each token requires a complete forward pass. SLINFER exploits this by treating each iteration as a schedulable unit. Unlike traditional GPU sharing (MPS, MIG) which spatially partitions resources, SLINFER temporally multiplexes at natural iteration boundaries.

Why this works mathematically: From Table II, a full GPU can handle 66 concurrent 7B-2K requests. If you partition into 2× half-instances, you get only 2×26=52. Static partitioning loses ~21% capacity because:
1. Batching efficiency is sub-linear (Figure 7 shows TPOT grows slowly with batch size)
2. A smaller instance can't absorb bursts

SLINFER avoids this by letting any instance temporarily use full resources when needed. The headroom formula (Equation 1) ensures that instances with urgent deadlines get priority without starving others.

**The AMX exploitation** is the secondary insight: They benchmarked that 4th-gen Xeon with AMX achieves 149ms TTFT for 7B@256 tokens versus 1003ms on 3rd-gen (Table I). This makes CPUs viable for the prefill-heavy workloads that dominate serverless (short prompts, infrequent requests). The 6.7-7.3× speedup comes specifically from the AMX matrix acceleration unit, not general CPU improvements.

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Real Hardware, Real Workloads**: They use actual A100s and AMX-equipped Xeons with Azure production traces (Figure 21). This isn't simulation. The Azure Serverless Trace captures the hot/cold model distribution that drives their design.

2. **Comprehensive Metrics**: Figure 22 shows four complementary metrics (TTFT CDF, SLO-met requests, decode speed, nodes used) across three model sizes. The 47-62% improvement over sllm+c and 86-154% over sllm when using CPUs is reproducible given the setup.

3. **Sensitivity Analysis Coverage**: Section IX-I tests five parameters (length patterns, invocation traces, CPU resources, keep-alive threshold, watermark). Figure 31's watermark analysis shows the overhead/utilization tradeoff clearly.

4. **Ablation is Honest**: Figure 23 shows disabling sharing drops SLO rate to 89%. Each component contributes measurably.

**Weaknesses:**

1. **The "4 CPUs + 4 GPUs" Configuration is Suspiciously Convenient**: They have exactly 4 CPU nodes and 4 GPU nodes. Figure 24 shows adding CPUs helps, but the crossover point (3-4 CPUs = 1 GPU) suggests CPUs are marginally useful. In production, would you really dedicate 4 CPU nodes to LLM inference?

2. **Memory Scaling Overhead is Buried**: Figure 17 shows scaling 32GB KV-cache takes 0.3-1.9 seconds. But Section VII-C's orchestration mechanism adds latency when operations queue in the "reservation station." They never report the p99 latency impact of memory operations blocking compute.

3. **The Profiling Cost is Hand-Waved**: Section VI-B claims "few hundred samples" completed "within minutes." But this is per-model, per-hardware-type. With 64 models × 2 hardware types = 128 profiling runs. They don't report the actual time or whether models must be offline during profiling.

4. **Prefill-Decode Disaggregation Comparison is Unfair**: Table III shows PD disaggregation hurts performance, but their implementation uses "dedicated instances for each stage per model." DistServe [75] uses model-level disaggregation, not per-model disaggregation. This comparison strawmans the alternative.

5. **Long-Sequence Performance is Weak**: Figure 35 admits "CPUs cannot satisfy the long-sequence TTFT SLO" for LongBench. Section IV-A2 says CPUs handle ≤5.6K tokens for 13B models. In practice, context windows are growing (8K→32K→128K). SLINFER's CPU advantage evaporates for these workloads.

6. **No Tail Latency Analysis**: They show CDF curves but never report p99 TTFT or TPOT explicitly. The curves in Figure 22 flatten before reaching 1.0, indicating dropped requests, but they don't quantify the tail behavior.

## Q4: What the Authors Didn't Tell You

**Hidden Hardware Costs:**

1. **The Shadow Validation is on the Critical Path**: Every request dispatch requires simulating future token generation (Figure 15). They claim 0.2-0.4ms overhead (Figure 33), but this is per-request. At 300 RPM (Figure 21's 128-model trace), that's 5 requests/second. If shadow validation takes 0.4ms and involves probing multiple instances, you're adding serialization delay. They don't discuss whether shadow validation can be parallelized across instances.

2. **The Reservation Station is Unbounded**: Figure 19 shows scale-up operations queue in a "reservation station" when pessimistic tracking detects OOM risk. But they never specify the queue depth or what happens under sustained memory pressure. Does the queue overflow? Do operations get dropped?

3. **KV-Cache Migration During Scale-Up**: Figure 16 shows the 3-step process (create blocks → copy used cache → delete old). The "copy used cache" step requires holding both old and new buffers simultaneously. For a scale-up from 32GB to 64GB, you need 96GB transiently. On an 80GB A100, this limits how much you can actually scale.

4. **OpenVINO Backend Maturity**: They use vLLM for GPUs but OpenVINO for CPUs (Section IX-A). OpenVINO's paged attention implementation may not be as optimized as vLLM's CUDA kernels. The CPU performance numbers might improve with better runtime support, but they also might be artificially limited by software inefficiency.

**Assumptions That Could Break:**

1. **Homogeneous Model Architecture**: All experiments use Llama variants. Mixture-of-experts models (like Mixtral) have different compute/memory profiles—sparse activation means memory bandwidth dominates. SLINFER's profiling approach assumes dense models.

2. **Single-Node GPU Configuration**: They use 4 A100s but don't discuss tensor parallelism across nodes. The "34B model with 2 GPUs/instance" in Section IX-E hints at this, but inter-node communication would add latency that breaks their tight timing assumptions.

3. **No Concurrent Prefill**: The shadow validation (Figure 15) assumes prefill blocks subsequent operations. If two requests arrive simultaneously for the same model, one must wait. High-burst scenarios might serialize more than expected.

**What They Glossed Over:**

- **Cold Start is Still There**: Section IX-A mentions "1 second to load a 7B model" with ServerlessLLM's loader, and they "relax the TTFT requirement for such requests." This effectively excludes cold-start latency from their SLO metrics.

- **CPU Memory Capacity**: CPUs have DRAM bandwidth (~200 GB/s for DDR5) versus HBM3 (~2 TB/s on A100). They never discuss whether CPU inference is memory-bandwidth-bound for attention operations. The 32GB KV-cache limit in their CPU experiments (Section IX-B: "--cpu_kv_gb 16") suggests memory is constrained.

- **Proactive Preemption Side Effects**: Section VIII-A allows larger instances to preempt smaller ones. But preempted requests get "rescheduled to other nodes." If all nodes are saturated, where do they go? The bin-packing fallback (Section VIII-B) assumes spare capacity exists somewhere.