# Study A — Simple Directive
**Paper:** 3695053.3731101  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 09:27

---

Q1: Whiteboard Explanation

Imagine you're explaining this to a colleague at a whiteboard:

**The Problem:** Large Language Models need massive compute, memory, and communication resources. A single GPU can't handle models like GPT-175B, so we distribute across devices. But interconnect bandwidth between devices becomes the bottleneck.

**The Opportunity:** Wafer-scale chips integrate many compute dies with high-bandwidth Die-to-Die (D2D) interconnects on a single wafer (~215mm×215mm). This gives us ~6× more inter-chip bandwidth than GPU clusters. But here's the catch: wafer area is fixed, so there's a fundamental trade-off between DRAM capacity (for weights/KV-cache) and compute resources.

**The Key Challenge:** LLM inference has two phases with different characteristics:
- *Prefill*: Process all input tokens in parallel → compute-intensive
- *Decoding*: Generate tokens one-by-one using KV-cache → memory-bandwidth-bound

Existing disaggregated systems (like Splitwise) separate these phases to different devices, but they don't optimize for wafer-scale topology and waste memory resources.

**WSC-LLM's Solution:** A co-exploration framework with three main components:
1. **Central Scheduler**: Finds optimal tensor parallelism size and resource allocation for each phase independently, then determines how many prefill vs. decoding instances to deploy
2. **Placement Strategy**: Places decoding instances centrally and prefill instances around the perimeter to minimize KV-cache transfer distances on the 2D mesh
3. **Memory Scheduler**: Exploits high D2D bandwidth to store KV-cache across dies along the transfer path, utilizing previously idle DRAM in prefill instances

The result: 3.12× better end-to-end latency versus state-of-the-art GPU-based systems.

---

Q2: The Key Insight

The central insight is that **wafer-scale chips' high D2D bandwidth fundamentally changes how we should manage KV-cache memory**. Unlike GPU clusters where inter-node communication is expensive and must be minimized, wafer-scale D2D bandwidth often exceeds DRAM bandwidth. This means cross-die memory access is constrained only by DRAM bandwidth, not communication bandwidth.

This enables a paradigm shift: rather than isolating each instance's memory and transferring KV-cache between phases, WSC-LLM treats the wafer's DRAMs as a distributed pool. The Memory Scheduler can place KV-cache along the transmission path from prefill to decoding instances, utilizing previously wasted DRAM in prefill instances. The ablation study confirms this—the Memory Scheduler contributes more to performance gains than compute optimizations, especially for larger models.

The secondary insight is that **moderate DRAM capacity per die optimally balances the compute/memory/communication trade-off**. Neither extreme (maximizing DRAM capacity or compute) wins; Case 3 with 64GB per die outperforms both higher (96GB) and lower (32-48GB) configurations because it maintains sufficient D2D bandwidth while providing adequate memory resources.

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**
1. **Comprehensive architecture exploration**: Testing 4 distinct wafer configurations with varying DRAM/D2D bandwidth trade-offs provides actionable design guidance beyond just scheduling improvements
2. **Strong ablation study**: Separately disabling Central Scheduler and Memory Scheduler reveals that memory optimization is more impactful for larger models—a non-obvious and valuable finding
3. **Realistic workloads**: Using Azure production traces (code and conversation) with Poisson arrival rates captures real-world workload dynamics rather than synthetic benchmarks
4. **Fair baseline comparison**: Despite SW-GPU having 6% more compute and 11% more memory, WSC-LLM still achieves 3.12× improvement, demonstrating scheduling efficiency
5. **Scalability validation**: Multi-wafer experiments (2×2 configuration) with varying W2W bandwidth show the approach generalizes beyond single-wafer scenarios

**Weaknesses:**
1. **Simulation-only evaluation**: No real hardware validation. The DNN-based lookup table for intra-die mapping introduces modeling uncertainty, and ASTRA-sim extensions are not validated against real wafer-scale systems
2. **Limited comparison points**: Only Splitwise is used as baseline. Missing comparisons with other disaggregated systems (DistServe, Mooncake) or wafer-scale specific scheduling approaches
3. **Static workload assumption**: Algorithm 1 optimizes based on resampled workload distributions, but doesn't adapt to dynamic workload changes at runtime
4. **Idealized communication model**: Assumes D2D bandwidth is always available and link congestion is avoided through placement, but doesn't model realistic contention scenarios
5. **DRAM configuration coupling**: DRAM bandwidth and capacity scale together in the configurations—it's unclear how to interpret results when these could be independently varied

---

Q4: What the Authors Didn't Tell You

**Implementation Complexity**: The Memory Scheduler's optimal KV-cache placement (Algorithm 2) requires real-time tracking of remaining DRAM capacity across all dies and coordination with both prefill and decoding instances. The paper claims "negligible overhead" but doesn't quantify the actual control plane latency or memory metadata management costs.

**Yield and Fault Tolerance**: Wafer-scale chips have notorious yield challenges—the paper assumes a perfect 6×9 or 7×9 die array. Real systems like Cerebras require significant redundancy and rerouting. How would WSC-LLM's placement strategy adapt when dies fail?

**Thermal and Power Constraints**: Dense wafer-scale integration creates thermal hotspots. Placing decoding instances centrally (which are more memory-bound) while prefill instances (more compute-bound) sit at the periphery might actually be thermally beneficial, but this isn't discussed.

**The ASTRA-sim Extension**: Extending a training simulator to handle dynamic LLM inference is non-trivial. The DNN-based lookup table for mapping results introduces an abstraction layer whose accuracy bounds aren't characterized. What's the error distribution of these fitted results?

**Scheduling Overhead**: Algorithm 1's O(DS) complexity is called "manageable" at "a few minutes," but this is offline. What about online adaptations when request patterns shift? The execution scheduler uses FCFS, which is known to perform poorly under certain load conditions.

**Why Case 3 Specifically?** The paper doesn't explain why 64GB/2TB bandwidth is the sweet spot beyond observing it performs best. A deeper analysis connecting this to LLM arithmetic intensity and communication-to-computation ratios would strengthen the architectural guidance.