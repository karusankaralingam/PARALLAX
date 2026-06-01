# Study A — Simple Directive
**Paper:** 1029998 FACE  Fully Overlapped PD Scheduling and Multi Level Architecture Co Exploration on Wafer  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:27

---

Q1: Whiteboard Explanation

Imagine you're designing a massive chip the size of a dinner plate (a 12-inch wafer) to run large language models like ChatGPT. The challenge is twofold: first, how do you arrange compute cores, memory, and interconnects across this wafer to maximize performance? Second, how do you schedule the two phases of LLM inference—prefill (processing the user's entire input) and decode (generating tokens one-by-one)—which have completely different resource demands?

The key problem with previous approaches is that they separated prefill and decode onto different parts of the wafer, which created bottlenecks: some instances were compute-starved, others memory-starved, and transferring data between them caused delays.

FACE's solution is elegant: run prefill and decode simultaneously within the same instance. Think of it like this—prefill is compute-hungry and decode is memory-hungry, so they can share hardware without fighting. The wafer-scale chip gives you fine-grained control over individual cores, so you can precisely partition attention computation tiles between both phases.

The framework works in three stages: (1) offline, explore all valid configurations of tile sizes and batch combinations that achieve overlap, storing results in a lookup table; (2) at runtime, dynamically assign incoming requests using this table to maintain optimal overlap; (3) leverage the wafer's high die-to-die bandwidth to flexibly place KV caches across instances without bottlenecks.

For hardware design, they found moderate SRAM per core (0.75MB), maximized compute/NoC resources, large die sizes near the reticle limit, and maximum HBM chiplets per die work best.

Q2: The Key Insight

The central insight is that wafer-scale chips' fine-grained operational control—the ability to dynamically manage individual core controllers and DMA engines—enables something impossible on GPUs: fully overlapping prefill and decode attention operations within a single instance, eliminating the fundamental interference between these phases that plagued all prior scheduling approaches.

Previous work either (a) ran both phases serially on the same hardware (unified scheduling), causing interference, or (b) separated them onto different hardware partitions (disaggregated scheduling), which wasted compute on decode instances and created KV cache transfer bottlenecks. Both approaches fundamentally serialize at least the attention operations.

FACE recognizes that prefill attention (compute-bound, operating on matrices) and decode attention (memory-bound, operating on vectors) have complementary resource demands. By precisely controlling tile sizes for both operations and scheduling them to execute concurrently on shared cores, the compute resources stay busy during decode while memory bandwidth is fully utilized during prefill—essentially getting "free" decode computation. This transforms what was a scheduling conflict into a symbiotic relationship.

This insight is non-obvious because it requires both the architectural capability (fine-grained NPU-like control) and the algorithmic machinery (offline configuration exploration, runtime lookup tables) to realize in practice.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive design space exploration**: The paper systematically evaluates 14 microarchitecture configurations across 3 die sizes and 10 architecture cases, providing actionable design guidelines rather than just point solutions.

2. **Real-world workloads**: Using Azure production traces with realistic arrival rates and token distributions strengthens practical relevance significantly over synthetic benchmarks.

3. **Multiple baselines**: Comparing against WSC-LLM (wafer-scale SOTA), vLLM (GPU SOTA), and both unified and disaggregated scheduling strategies demonstrates broad improvements.

4. **Consistent gains across scenarios**: 3.68× E2E improvement over WSC-LLM and 7.23× over vLLM across three model sizes (7B, 13B, 70B) and two datasets shows robustness.

**Weaknesses:**

1. **Simulation-based evaluation**: The evaluator is calibrated against one NPU and HBM specs, but there's no silicon validation. Real wafer-scale systems may exhibit unexpected behaviors (thermal throttling, yield issues, control overhead).

2. **Limited model diversity**: Only LLaMA variants are tested. MoE models (like Mixtral), multi-modal models, or models with different attention mechanisms (linear attention) might behave differently.

3. **Offline CSE assumptions**: The chunked prefill size is fixed to dataset average, and the LUT matching uses Euclidean distance approximation. How this degrades under highly variable workloads or distribution shift isn't explored.

4. **Missing energy analysis**: Wafer-scale chips face significant power delivery challenges; reporting only latency/throughput ignores whether the design is thermally feasible.

5. **Single-wafer scope**: Multi-wafer scaling is mentioned only briefly; real datacenter deployments would require this analysis.

Q4: What the Authors Didn't Tell You

**The control overhead is hand-waved**: Fine-grained PE-level control enabling dynamic tile sizing sounds powerful, but the paper never quantifies the control plane overhead. How many instructions does it take to reconfigure tiles? What's the latency? Real NPUs have non-trivial instruction dispatch costs that could eat into the gains from overlapping.

**The LUT size and lookup latency are unspecified**: With multiple dimensions (prefill chunks, decode batches, decode tokens, tile sizes), the configuration space could be large. They claim "one-dimensional Euclidean distance" lookup is fast, but don't show LUT sizes or lookup times for realistic configurations.

**Memory fragmentation is ignored**: OMM distributes KV caches across instances for long sequences, but managing fragmented memory across a wafer introduces complexity. How do you garbage collect? What happens under memory pressure with many concurrent long requests?

**The 0.75MB SRAM "sweet spot" may not generalize**: This optimal core configuration was found for FP16 LLaMA models with specific attention patterns. Different precisions, attention variants (MQA vs GQA vs MHA), or future models with longer contexts might shift this optimum significantly.

**Fairness of GPU comparison**: The 6-node A100 cluster comparison uses 7nm A100s against a hypothetical 7nm wafer-scale chip. But A100s are production silicon with all overheads, while the wafer-scale chip is simulated. The H100 or Blackwell architectures would be more contemporary comparisons.

**What happens when prefill and decode don't overlap perfectly?** The paper shows best-case fully-overlapped execution, but workload imbalance (short prefills with long decodes or vice versa) would cause one phase to wait. The sensitivity to workload mix asymmetry isn't characterized.