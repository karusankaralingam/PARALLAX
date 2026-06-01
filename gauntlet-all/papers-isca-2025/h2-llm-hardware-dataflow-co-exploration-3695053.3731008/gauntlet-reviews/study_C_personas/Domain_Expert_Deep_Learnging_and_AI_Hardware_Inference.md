# Paper Analysis: H2-LLM (ISCA 2025)

## Q1: Whiteboard Explanation

Imagine you're building an edge device—a smart home hub, a reception kiosk, or a private server—that needs to run a 7-8 billion parameter LLM with batch sizes of 1-16, responding in real-time. You have two fundamental problems:

**Problem 1: The Two-Phase Schizophrenia of LLM Inference.**
- *Prefill* (processing the prompt): You've got hundreds of tokens arriving at once. This is compute-bound—your systolic arrays can chew through the matrix multiplications efficiently.
- *Decode* (generating tokens one-by-one): You're processing ~1 token per batch element. This is brutally memory-bound because you need to load the entire weight matrix (gigabytes) just to produce a handful of output values. Your arithmetic intensity collapses.

**Problem 2: Existing Near-Memory Processing (NMP) is too weak.**
Current solutions (Samsung HBM-PIM, SK Hynix AiM) embed tiny processing engines *inside* the DRAM die itself. As Table 2 shows (Section 3.1), these achieve ~1 FLOP/Byte—barely enough compute to keep up with single-batch inference. The moment you increase batch size to 8-16 (still "low-batch" for edge), or use GQA/MQA attention variants that increase arithmetic intensity, these in-die NMP engines become the bottleneck (Figure 3).

**The H2-LLM Solution:**
Instead of putting wimpy compute *inside* the DRAM, use **hybrid bonding** to stack the DRAM die on top of a **separate logic die**. This logic die can hold much beefier processing engines—think of it as getting a custom co-processor that's directly bonded to DRAM, achieving ~25-50 GB/s bandwidth per channel via thousands of Cu-Cu connections (vs. the external LPDDR5 interface's ~12.8 GB/s per channel).

But here's the catch (Figure 4-(b)): Those hybrid bonding I/O pins need controllers, and controllers eat area on your logic die. Want more bandwidth? You need more pins. More pins = more controllers = less room for compute. This is the **computation-bandwidth tradeoff** the paper explores.

**The Dataflow Contribution:**
Beyond the hardware, the paper argues that *fixed* mappings (e.g., "always run attention on NMP, always run FFN on GPU") are suboptimal for low-batch edge inference. They propose a "data-centric" dataflow abstraction (Section 5) that:
1. Partitions operators into groups that can run in parallel.
2. Decides which *channels* (normal vs. NMP) each operator uses—not which *engine*.
3. Allows *operator fission*: splitting a single operator across both the centralized processor and the NMP engines.

A genetic-algorithm-based DSE framework (Section 6) then searches this co-design space.

---

## Q2: The Key Insight

The core insight is deceptively simple, but its implications are significant:

**"For edge-side low-batch LLM inference, the right amount of computation capacity and bandwidth in your near-memory processing unit is not a fixed number—it's a tunable tradeoff that must be co-optimized with the dataflow mapping."**

More concretely:

1. **In-die NMP's ~1 FLOP/Byte is fundamentally insufficient.** Section 3.1 and Figure 3 show that while in-die NMP helps for batch=1 and traditional MHA, it provides *no speedup* once batch size reaches 8, or when using MQA (where KV heads are shared, increasing arithmetic intensity of attention). The existing designs hit their compute ceiling immediately.

2. **Hybrid bonding unlocks a *design space*, not a single point.** Unlike in-die NMP (where you take what DRAM technology gives you), HB lets you choose: more HB I/O pins (more bandwidth, less compute area) vs. fewer pins (less bandwidth, more compute area). Table 6 and Figure 18 explore this concretely. For batch=1, bandwidth dominates; for batch=16, you need more compute.

3. **The dataflow must be prefill-aware and support fission.** The "compute-centric" approach from prior work (SpecPIM [47]) first decides "use NMP or use centralized processor" and *then* assigns channels. This constrains the external memory bandwidth available to the centralized processor during prefill. Since prefill is already fully parallelizable, starving it of bandwidth can shift it from compute-bound to memory-bound, especially in "prefill-heavy" scenarios (e.g., LooGLE dataset with 1971 avg prompt tokens but only 17 decode tokens, Table 1). Figure 14 shows prefill accounts for 36%-90% of latency in prefill-heavy cases.

The mechanism for *operator fission* (Section 5.2) is also elegant: for GEMMs, they split along the output dimension N; for attention, they assign different GEMMs (heads) to different engines. This avoids inter-engine interference and data dependencies.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **Comprehensive Benchmark Coverage:** They evaluate three models covering MHA (OPT 6.7B), GQA (LLaMA3 8B), and MQA (PaLM 8B)—the dominant attention variants (Table 4). They use four real-world datasets representing diverse use cases (Table 1): code completion, chatbot, context understanding, and question answering. This is better than showing ResNet-50 for an LLM accelerator.

2. **Honest Batch Size Reporting:** They explicitly target batch sizes 1, 4, and 16 (Section 7.1), which are realistic for edge (Figure 1 motivates this). They don't hide behind batch=256 throughput numbers that are irrelevant to interactive latency.

3. **Strong Baselines for an Architecture Paper:** They compare against:
   - *CP (Centralized Processor only)*: With doubled compute to account for NMP resources.
   - *ID-NMP*: Samsung LPDDR5-PIM spec (102.4 GFLOPS/channel).
   - *ID-NMP+*: Enhanced with AiM's 1GHz PEs—the *best* commodity in-die NMP.
   
   They apply their own *data-centric dataflow* to the baselines (Section 7.2), ensuring they're not comparing against strawmen.

4. **Dataflow Ablation is Excellent:** Figure 12 and 13 directly compare their dataflow against Attn-NMP [24], Attn-NMP-Split [60], FC-NMP [39], and CC-NMP [47] on the *same* H2-LLM architecture. This isolates the dataflow contribution from the hardware contribution. The result: dataflow exploration alone yields 1.37x over FC-NMP and 1.11x over CC-NMP.

5. **Energy Efficiency Reported:** Figure 10 (bottom row) shows energy efficiency, not just performance. H2-LLM achieves 1.48x/1.54x better efficiency than ID-NMP/ID-NMP+.

6. **DSE Analysis with Takeaways:** Section 7.4 provides actionable architectural insights (Takeaways 1-6), such as "with batch size increasing, sensitivity to weight buffer size diminishes" (Figure 19). This is valuable for future designers.

### Weaknesses:

1. **Simulation-Only, No Silicon:**  They use Ramulator2 [52] extended for NMP and Tileflow [89] for the centralized processor (Section 7.1). While Tileflow supports attention fusion, the integration between these models—especially for synchronization and data transfer overhead—is a potential source of error. The "rough performance model" acknowledged in the Artifact Appendix (Section A.6) for NMP/NPU operators is concerning for precise claims.

2. **Centralized Processor Configuration is Questionable:** Their centralized processor is a "TPU-like processor" with 8 128×128 systolic arrays and 8 SIMD-128 VPUs at 1GHz, totaling 262 TFLOPS (Section 7.1). This is a reasonable edge accelerator. However, the comparison against a "doubled" CP (to account for NMP resources) as a baseline is not entirely fair—doubling systolic arrays doesn't necessarily double effective performance for memory-bound decoding. The comparison against real edge GPUs (e.g., NVIDIA Orin) is absent.

3. **HBM Comparison is Dismissed Too Quickly:** Duplex [85] places PEs on HBM logic dies. The paper dismisses it for "high power consumption" (Section 3.2) unsuitable for edge. But they don't quantify this. What's the power budget? A 40W accelerator might be fine for a "private edge server" use case (Figure 1). This comparison would strengthen the "edge-only" positioning.

4. **Mixed Scenario Results are Only Summary (Figure 11):** The "mixed scenario" evaluation (requests with different lengths) is presented only as a geomean bar chart. For a real service, the *variance* and *tail latency* matter. They don't report p99 latency anywhere.

5. **Prefill Latency Ratio Data (Figure 14):** They show prefill accounts for up to 90% of latency in prefill-heavy scenarios. But then, the prefill speedup is only 1.27x (geomean) over CC-NMP. If prefill dominates, why isn't the end-to-end speedup higher? This suggests the baseline CC-NMP's prefill performance was already decent, or the prefill stage itself is hard to optimize further.

6. **Controller Area Model Details are Light:** They mention their HB controller area model comes from "in-house implementation using 40nm technology" (Section 3.2) and "real-chip tape-out [55]" (Section 7.1). The actual overhead percentages (4.6%-40.2% of PE area for 128-1024 pins, Figure 4-(b)) are stated without sufficient detail on what "HB controller" includes (PHY? Logic? ECC?).

---

## Q4: What the Authors Didn't Tell You

1. **The "Optimal" Design Changes Per Workload:** Figure 15 shows full architecture DSE yields 1.38x speedup over the *fixed* design. This means the fixed design (underlined in Table 5) is *not* universally optimal. In practice, you tape out *one* chip. The paper doesn't discuss how to pick a single robust design across a portfolio of workloads—though the multi-workload DSE extension is mentioned (Section 6.2), its effectiveness isn't evaluated.

2. **Compiler/Runtime Complexity is Handwaved:** Section 6.3 describes the "Model Compilation Flow." For centralized processor operators, they punt to "existing xPU compilers [10, 87, 88]." For NMP operators, they "adopt NMP operator templates... manually designed." They explicitly state "how to automatically generate operator templates will be our future work." This means deploying a new operator type (e.g., a novel attention variant, or a new activation function) requires manual hardware template engineering.

3. **Synchronization Overhead is Suspiciously Low:** Figure 16 shows synchronization and data transfer overhead of 1.6%-15.7%. They claim "synchronization bubble is effectively eliminated by dataflow exploration." But their synchronization model (Section 5.3, Case 4) relies on "roofline-model-based latency estimation" to predict synchronization points. If this estimation is inaccurate (and analytical models often are, especially for complex memory systems), actual runtime synchronization could introduce stalls not captured by the simulator.

4. **The Elephant in the Room: Cost and Yield.** Hybrid bonding is a cutting-edge 3D integration technology. They cite references [7, 16, 33, 55, 79, 84] from 2020-2024, including AMD's V-Cache and their own tape-out [55]. But they never discuss cost or yield. LPDDR5-PIM or GDDR6-AiM are modifications to *commodity* DRAM. Hybrid-bonding-based logic-on-DRAM is *not* commodity. For an "edge" device, cost is paramount. A 2.72x speedup is less compelling if the memory module costs 10x more.

5. **Model Sizes are Stuck at ~8B.** They evaluate OPT 6.7B, LLaMA3 8B, and PaLM 8B (Table 4). These fit in 8 channels × 16 banks × 256MB = 32GB (FP16 weights are ~16GB for an 8B model). But the trend is towards larger edge models (70B quantized to INT4, for instance). They briefly mention their memory capacity is "adequate" but don't discuss scalability to larger models or the implications of needing more channels.

6. **What About Flash Attention and Other Software Optimizations?** The comparison to the centralized processor uses "Tileflow's performance model [89]... supports the evaluation of attention operator fusion." But state-of-the-art LLM serving uses FlashAttention, PagedAttention, continuous batching, and speculative decoding. While speculative decoding is addressed in a prior work (SpecPIM [47]), the evaluation here doesn't consider whether these software optimizations would change the compute-vs-memory balance that motivates NMP in the first place.

7. **The Genetic Algorithm DSE is a Black Box for Deployment.** The DSE uses 100 rounds × 5k individuals × Top-50 selection (Section 7.2). This takes ~9 hours (Artifact Appendix). For a fixed architecture, users must re-run this for their specific model and workload distribution. This is fine for research, but operationally cumbersome. They don't discuss convergence, sensitivity to random seeds, or solution stability.