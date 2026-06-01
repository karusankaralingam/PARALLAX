# Paper Deconstruction: Oaken (ISCA '25)

## Q1: Whiteboard Explanation

Let me break down what this paper actually does in plain terms.

**The Problem:** When you're running an LLM service (like ChatGPT), you batch many user requests together to get better throughput. The problem is that each user's conversation has its own "memory" called the KV cache—this stores the keys and values from the attention mechanism so you don't recompute them for every token. Unlike model weights (which are shared across all users), KV caches are *per-request*. So with 256 concurrent users, you have 256 separate KV caches eating your memory bandwidth and capacity.

**The Core Tension:** HBM gives you bandwidth (2 TB/s on an A100) but limited capacity (80GB). LPDDR gives you capacity (256GB) but lower bandwidth (1.1 TB/s). Neither alone solves the problem. The KV cache becomes the dominant memory consumer at large batch sizes—Figure 5(a) shows that at batch size 256, the KV cache is ~94% of total memory usage for Llama2-13B.

**Oaken's Solution:** Quantize the KV cache to ~4.8 effective bits (from FP16's 16 bits), reducing both the bandwidth required to read it and the capacity needed to store it. This lets you use LPDDR (big capacity) while the reduced data size compensates for its lower bandwidth.

**The Trick:** Most KV cache quantization methods either:
1. Use expensive online operations (sorting to find outliers) that eat into your speedup, or
2. Use cheap coarse-grained quantization that hurts accuracy badly.

Oaken's insight is that the *thresholds* defining outliers are stable across different inputs for a given model/layer (Observation 2, Figure 6(b)). So you profile once offline to find these thresholds, then at runtime you just do cheap comparisons to separate values into three groups:
- **Outer group** (large magnitude outliers): ~4% of values
- **Middle group** (inliers): ~90% of values  
- **Inner group** (small magnitude outliers): ~6% of values

Each group gets quantized separately with its own scale, and they use a "group-shift" trick to narrow the range before quantization, avoiding the need for mixed-precision storage of outliers.

**The Hardware:** Custom quantization/dequantization engines in the DMA unit, plus a memory management unit (MMU) that handles both dense (inliers) and sparse (outlier metadata) data layouts efficiently with burst accesses.

---

## Q2: The Key Insight

**The Real Delta:** The core innovation is the *online-offline hybrid* approach that makes outlier-aware quantization practical for real-time inference.

Prior work like KVQuant [22] and KIVI [43] achieve good accuracy by identifying outliers per-token using topK operations (essentially sorting), but this has O(n log n) complexity and negates the speedup from quantization. Other work like QServe [41] and Atom [86] avoids this overhead by using coarse-grained channel reordering, but loses accuracy because they ignore the "exceptions to the pattern" (the scattered dots in Figure 6(c) that don't align with the vertical outlier channels).

**Oaken's insight** (Section 4.1, Observation 2): The *distribution* of KV cache values is determined by the model weights, not the input data. Figure 6(b) shows that min-max ranges across Wikitext2, PIQA, and Hellaswag datasets are nearly identical for Llama2-7B. This means you can profile thresholds offline once and reuse them forever—the heavy lifting is amortized away.

**The Magic Trick #2: Group-Shift Quantization (Section 4.4):** Previous methods store outliers in FP16 (16 bits + 6 index bits + 1 group bit = 23 bits per outlier), which is expensive. Oaken observes that after isolating outliers, their *shifted* range (subtracting the threshold) is narrow enough to quantize to 5 bits. Combined with "fused dense-and-sparse encoding" (Section 4.5), where the 4-bit portion of the outlier is stored in the zeroed position of the dense matrix, each outlier entry costs only 8 bits total—not 23 bits.

**Why This Matters:** This transforms outlier handling from a "necessary evil with high overhead" into something with predictable, low cost. Table 3 shows that the three-group design at 4.82 effective bits achieves perplexity of 5.526 (vs. 5.47 original), while QServe at 4.25 effective bits gets 5.67 perplexity. Oaken trades slightly higher bitwidth for significantly better accuracy.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Comprehensive Model Coverage:** They evaluate 8 models (OPT, Llama2, Mistral, Mixtral) across 4 datasets. Importantly, they include models with grouped-query attention (Mistral, Mixtral) and MoE (Mixtral-8x7B), which have smaller KV caches, showing Oaken still helps (Figure 14).

2. **Real-World Traces:** Figure 14 uses Azure production traces (Conversation, BurstGPT) with realistic input/output length distributions—not just synthetic 1K:1K benchmarks. The BurstGPT trace with longer outputs shows larger gains (as expected, since KV cache pressure grows with sequence length).

3. **Honest Comparison with Strong Baselines:** They compare against vLLM, QServe, KIVI, KVQuant, and Tender—all recent, state-of-the-art systems. They even implement Oaken's algorithm on GPU (Figure 12(b)) to show it doesn't work well there due to warp divergence, justifying the custom hardware.

4. **Latency Breakdown:** Figure 12(b) shows quantization is only 1.29% and dequantization 3.23% of total latency at batch size 64—the overhead is genuinely low.

5. **Area Overhead:** Table 4 shows the quantization engine is only 1.86% of core area—this is a legitimate "bolted-on" module, not a complete redesign.

### Weaknesses

1. **The Baseline Accelerator is Their Own Prior Work:** Oaken is built on top of LPU [48], which is from the same research group (HyperAccel). The "LPU" baseline in Figure 11 is essentially their own non-quantized accelerator. While this makes the comparison clean, we don't see Oaken modules integrated with a truly independent baseline like a TPU or GPU tensor core.

2. **HBM vs. LPDDR Comparison is Conflated:** Oaken-LPDDR wins at large batches partly because it has 256GB vs. 80GB capacity. The throughput comparison (Figure 11) conflates the algorithmic contribution (quantization) with the hardware choice (LPDDR vs. HBM). It would be cleaner to show: (a) LPU-HBM vs. Oaken-HBM, and (b) LPU-LPDDR vs. Oaken-LPDDR separately.

3. **Short Sequence Length Performance:** Figure 13 shows that at 1K-4K total sequence length, vLLM and QServe *outperform* Oaken because the KV cache isn't the bottleneck yet. They acknowledge this, but it means Oaken's value proposition is specific to long-context, large-batch scenarios.

4. **Mixtral Results are Weak:** Figure 14(c) and (d) show that for Mixtral-8x7B with grouped-query attention, Oaken-LPDDR barely beats vLLM. The paper admits "quantization baselines... show little to no performance gain" (Section 6.2). This suggests the technique's applicability narrows as models adopt KV-cache-efficient attention variants.

5. **Profiling Overhead is Handwaved:** Section 6.1 says offline profiling takes "approximately ten minutes" for Llama2-70B with "about a hundred inferences." This is reasonable for a static model, but if you're fine-tuning or doing continual learning, the thresholds might shift, requiring re-profiling. This isn't explored.

6. **No Comparison with FP8 KV Cache:** NVIDIA's Hopper architecture (H100) supports FP8 natively. A 2x compression with FP8 (no outlier handling needed) might be a simpler baseline that achieves reasonable accuracy. This is absent.

---

## Q4: What the Authors Didn't Tell You

1. **The "Effective Bitwidth" Hides Complexity:** Table 2 reports 4.82 effective bits for Oaken on Llama2-7B/13B, but this is an *average*. The actual storage format is heterogeneous: 4-bit dense for inliers, 8-bit COO entries for outliers. The memory controller must handle two different data streams. The MMU (Section 5.2, Figure 10) with separate "Dense Management Table" and "Sparse Management Table" adds complexity that isn't reflected in the area numbers (Table 4 doesn't break out MMU area).

2. **The Accuracy-Performance Trade-off is Baked In:** The 4%/90%/6% split is hardcoded across all models and datasets (Section 6.1). Table 3 shows this is *not* Pareto-optimal for all cases—using 10%/90%/0% (no inner group) gives 5.546 perplexity vs. 5.526 for the chosen split. The paper claims minimal impact, but different workloads might prefer different points. The "optimal group ratio varies slightly across LLMs" (Section 6.1) is underexplored.

3. **The "Streaming" Dequantization Claim Needs Context:** Section 5.2 says the dequantization engine "functions in a streaming manner" to maintain low latency. But Figure 9(b) shows a "Buffer" stage to synchronize inlier and outlier paths. If the outlier ratio varies significantly between tokens, this buffer size becomes a design parameter. At what outlier percentage does the buffer stall? This isn't discussed.

4. **Comparison with Tender is Unfair in One Direction, Fair in Another:** They "align Tender's memory specifications... with those of the A100 GPU" (Section 6.1), but Tender uses systolic arrays and suffers from "padding required by varying prompt lengths" (Section 6.2). Oaken's LPU baseline uses a different dataflow optimized for LLMs. Tender's poor showing (Table 2 shows NaN for Mixtral) might be a dataflow mismatch, not a quantization algorithm failure.

5. **The Prefill Phase is Ignored:** The entire paper focuses on the generation phase. Section 2.1 and Figure 2(a) acknowledge prefill exists, but there's no evaluation of prefill performance. During prefill, you're creating the KV cache (compute-bound), not reading it. Oaken's quantization overhead applies here, but the bandwidth benefit doesn't. For workloads with long prompts and short outputs, this matters.

6. **What About Speculative Decoding?** Modern serving systems increasingly use speculative decoding (draft models) to improve throughput. This changes the KV cache access pattern significantly. Oaken's page-based MMU design (Figure 10) assumes sequential token generation. Speculative decoding with rollback would require invalidating and potentially rewriting KV cache pages—this interaction isn't mentioned.

7. **Power Comparison is Apples-to-Oranges:** Section 6.2 claims "222.7W, which is 44.3% lower than the 400W TDP of the A100 GPU." But Oaken is synthesized at 28nm, while A100 is 7nm. A fair comparison would normalize for process node or compare total energy per token, not TDP.