# Paper Deconstruction: Oaken

## Q1: Whiteboard Explanation

Let me draw this out for you like we're at a whiteboard.

**The Core Problem:**
When you batch many LLM inference requests together, you hit a memory wall. Each request needs its own KV cache (the stored keys and values from previous tokens), and these caches:
1. Can't be shared across requests (unlike model weights)
2. Grow linearly with sequence length AND batch size
3. Must be read from memory every single generation step

So at batch size 256 with 1K tokens, you're drowning in memory traffic just for attention operations, while the actual compute units sit idle (see Figure 3(c) - only ~20% GPU utilization during multi-head attention).

**The Existing "Solution" Landscape:**
Prior KV cache quantization schemes fall into two camps:
- **High accuracy, high overhead:** KIVI, KVQuant use per-vector outlier detection with FP16 for outliers. Great accuracy, but online sorting (O(n log n)) or mixed-precision compute kills your throughput gains.
- **Low overhead, accuracy loss:** QServe, Atom, Tender use channel reordering and coarse-grained grouping. Fast, but they treat all channels the same way and miss the "exceptions" in the distribution (Figure 6(c) - those scattered dots outside the vertical lines).

**Oaken's Core Trick - The "Online-Offline Hybrid":**
Here's the insight: The *shape* of the KV cache distribution is stable across inputs (Figure 6(b) - same min/max range across Wikitext, PIQA, Hellaswag), but varies across models and layers (Figure 6(a)).

So Oaken does this:
1. **Offline (once per model):** Run ~100 calibration inferences to find four threshold values per layer that define "outer" (large outliers), "middle" (inliers), and "inner" (small magnitude) groups. These thresholds are *fixed* and stored with the model.
2. **Online (per token):** During inference, just compare each value against the pre-computed thresholds. No sorting! Then compute min/max *within each group* for the quantization scale.

**The Memory Layout Magic:**
The clever bit is "Fused Dense-and-Sparse Encoding" (Figure 7(c)):
- Inliers (middle group, ~90% of values) → 4-bit dense tensor
- Outliers → 5-bit quantized (not FP16!), but 4 bits go into the *zeroed slots* of the dense tensor where the outlier came from
- Only 8 bits per outlier stored in sparse COO format: 6 bits for index, 1 bit for group, 1 bit for sign

This keeps everything memory-aligned at byte boundaries (critical for bandwidth utilization) while achieving ~4.82 effective bits per value.

---

## Q2: The Key Insight

**The Delta (What's Actually New):**

The *real* contribution is the **algorithm-hardware co-design that makes three-group outlier isolation practical without online sorting overhead.**

Specifically:
1. **Offline threshold determination** exploits the empirical observation (Section 4.1) that KV distributions are data-agnostic but model-specific. This is the key insight that prior work missed - you don't need to re-compute outlier boundaries for every token, you just need them once per layer.

2. **Group-shift quantization** (Section 4.4, Equation 4): Instead of storing outliers at full FP16 precision, they subtract the threshold from outlier values to "shift" them into a narrower range, then quantize to 5-bit. This converts a 23-bit sparse entry (16-bit value + 6-bit index + 1-bit group) into just 8 bits.

3. **The hardware incarnation** (Figures 8-10): Custom quantization/dequantization engines in the DMA path with a dedicated MMU that manages both dense and sparse matrices at page granularity. The streaming design means quant/dequant happens *during* memory transfers, not as a separate pass.

**What's NOT the innovation:**
- Dense-and-sparse encoding (SqueezeLLM [30] did this for weights)
- Per-token quantization (KIVI [43], KVQuant [22] proposed this)
- The observation that channels have different magnitudes (widely known)

The innovation is the *specific combination* that achieves O(1) per-element grouping (just threshold comparisons) instead of O(n log n) sorting, while maintaining per-token granularity.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **Comprehensive baseline coverage:** They compare against both software baselines (vLLM, QServe, KIVI, KVQuant) and hardware baselines (Tender, LPU). This is better than papers that only compare against one category.

2. **Real workload traces:** Figure 14 uses Azure production traces (Conversation, BurstGPT) with varying input/output lengths. This is crucial - synthetic 1K:1K experiments don't capture real serving patterns.

3. **Honest accuracy-performance tradeoff analysis:** Figure 12(a) shows the Pareto frontier explicitly. They don't hide that lower bitwidth = worse accuracy; they show you can navigate this space.

4. **Latency breakdown:** Figure 12(b) separates attention, non-attention, quantization, and dequantization times. This reveals that quant/dequant is only 4.52% of latency at batch=64 (1.29% + 3.23%), validating the low-overhead claim.

5. **Area overhead reported:** Table 4 shows the quantization engine is only 1.86% of compute core area. This is critical for hardware papers - you need to show the cost of your new modules.

### Weaknesses:

1. **The baseline comparison is asymmetric:**
   - GPU baselines run on real A100 hardware
   - Oaken runs on a *simulator* extended from LPU
   - Tender runs on its own simulator, "aligned" to A100 specs
   
   Section 6.1 states they "developed a hardware simulator by extending the existing hardware simulator of LPU." This is standard for ISCA papers, but the 1.58× speedup over QServe (a real GPU implementation) should be viewed with appropriate skepticism about simulator fidelity.

2. **The "Oaken-GPU" strawman in Figure 12(b):**
   They implement Oaken's algorithm on GPU and show it's slow due to "warp divergence in CUDA." But this comparison is unfair - they designed the algorithm *specifically* for their custom hardware. A proper GPU implementation might use different grouping strategies or batched operations.

3. **Missing throughput vs. latency tradeoff:**
   Figure 11 shows throughput only. What about time-to-first-token (TTFT)? For interactive applications, the prefill latency matters enormously. The paper focuses entirely on generation-phase throughput.

4. **Grouped Query Attention dilutes the gains:**
   Section 6.2 admits: "Mistral-7B, Mixtral-8x7B, and Llama2-70B models employ grouped-query attention to reduce KV cache size." For Mixtral-8x7B (Figure 14c,d), Oaken-LPDDR shows minimal gains over vLLM at batch=16-32. Modern models are moving toward GQA/MQA, which fundamentally reduces the KV cache bottleneck Oaken addresses.

5. **The 28nm synthesis is dated:**
   Table 4 uses TSMC 28nm. HBM-equipped accelerators typically use 7nm or 5nm. The area numbers can't be directly compared to modern GPUs or NPUs.

6. **Cherry-picked sequence length regime:**
   Figure 13 shows that for sequences <8K, "QServe and vLLM outperform Oaken." They argue longer sequences favor Oaken, but the "sweet spot" for Oaken (8K-32K sequences, large batches) is a specific operating regime, not universal.

---

## Q4: What the Authors Didn't Tell You

### The Hidden Costs They Minimize:

1. **Offline profiling is model-specific:**
   Section 6.1 mentions profiling takes "approximately ten minutes, even for the Llama2-70B model" with "about a hundred inferences." But this must be redone for every model variant, every fine-tuned checkpoint, and potentially every quantization configuration. In production serving with frequent model updates, this overhead compounds.

2. **The 4%/90%/6% split is fragile:**
   Section 6.1 states: "We set the outer, middle, and inner group ratio to 4%, 90%, and 6%, respectively. This global configuration applies to all models and datasets."
   
   But look at Table 3 - they only evaluated ratios summing to 10% outliers. What if a new model has 20% outliers? The paper claims the impact is "marginal" but never quantifies cross-model variance.

3. **The accuracy comparison baseline is FP16, not the best quantization:**
   Table 2 shows Oaken has 0.54% lower accuracy than KVQuant and 0.32% lower than KIVI. The abstract claims "minimal accuracy loss of only 0.54% on average compared to state-of-the-art KV cache quantization techniques." This is misleading - they're comparing to FP16 baseline, not to KVQuant's accuracy.

4. **LPDDR vs. HBM is an unfair capacity comparison:**
   Oaken-LPDDR has **256GB** capacity vs A100's **80GB**. Of course it handles larger batches! The fair comparison is Oaken-HBM vs A100, where capacity is equal. In that comparison (Figure 11), Oaken-HBM hits OOM at the same points as A100 for large models.

5. **The MMU complexity is hand-waved:**
   Section 5.2 describes the MMU with "dense management table" and "sparse management table" for handling variable-sized sparse matrices. But managing page-level allocation for variable-rate sparse data across thousands of requests is non-trivial. They report the dequantization engine is 6.35% of core area (Table 4) - that's 3x larger than the quantization engine, suggesting significant complexity.

6. **They don't discuss failure modes:**
   What happens when the offline-profiled thresholds encounter distribution shift at runtime? What if a novel prompt produces KV values outside the profiled range? The paper assumes distribution stability but provides no robustness analysis.

7. **Power numbers are incomplete:**
   Section 6.2 mentions "222.7W, which is 44.3% lower than the 400W TDP of the A100 GPU." But this compares Oaken's *estimated power* (from synthesis) against A100's *TDP* (thermal design power, the maximum). Actual GPU power during inference is typically 250-300W, not 400W. The comparison flatters Oaken.

8. **The vLLM baseline may be stale:**
   vLLM has evolved rapidly. The paper doesn't specify which vLLM version was used. Key optimizations like chunked prefill and prefix caching could significantly change the baseline performance.