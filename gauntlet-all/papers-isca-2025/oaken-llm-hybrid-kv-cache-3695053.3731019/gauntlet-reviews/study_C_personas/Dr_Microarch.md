## Q1: Whiteboard Explanation

Let me walk you through Oaken's mechanism by drawing it out on the whiteboard.

**The Problem Setup:**
Batched LLM inference faces a fundamental tension. During the generation phase, each request needs its own KV cache (the stored keys and values from previous tokens). Unlike model weights which can be shared across a batch, KV caches are *per-request* and *per-token*, meaning they scale linearly with both batch size and sequence length. As Figure 2(b) shows, the "Activation-Activation" operations (Q×K^T and S×V) are labeled "Not Shared" — this is the memory bandwidth killer.

**The Core Dataflow:**

1. **Offline Phase (One-time profiling):**
   - Run ~100 inferences with sample prompts on your target model
   - For each decoder layer, collect the distribution of KV cache values
   - Extract four threshold boundaries: T^o_lo, T^i_lo, T^i_hi, T^o_hi (Equation 1, Section 4.3)
   - These define three groups: Outer (4%), Middle (90%), Inner (6%)
   - Store these thresholds per-layer — they're data-agnostic (Figure 6(b) confirms distribution is stable across datasets)

2. **Online Quantization (Per-token, as KV is generated):**
   - Fresh K,V vectors arrive from attention computation
   - **Decomposer module** (①, Figure 9a): Compare each element against the offline thresholds to assign it to outer/middle/inner group
   - **Group-shift**: Subtract the threshold from each value to compress the range (e.g., for outer group values > T^o_hi, compute x - T^o_hi)
   - **Min/Max Finder** + **σ Calculator**: Compute per-group scaling factors dynamically using Equation 2
   - **Quantizer**: Middle group → 4-bit INT; Inner/Outer groups → 5-bit INT

3. **Storage Layout (Fused Dense-and-Sparse):**
   - Middle group (inliers): Stored as dense 4-bit tensor
   - Inner/Outer groups (outliers): Stored in COO sparse format (6-bit index + 1-bit group flag + 1-bit sign = 8 bits per entry)
   - **The trick** (Figure 7c): The 4 MSBs of the 5-bit outlier value are embedded *into the zeroed slots* of the dense matrix. The remaining bits go into the sparse index structure.

4. **Online Dequantization (When reading KV cache for attention):**
   - Dense data streams to inlier dequantizer (④)
   - Sparse COO data goes through zero-insert shifter (⑤) to restore alignment
   - Both paths OR-merge and feed the Matrix Processing Unit

**Memory Management Unit (Figure 10):**
Two tables manage the virtual-to-physical mappings:
- Dense Management Table: Fixed-size entries (4 bytes per token-layer-head)
- Sparse Management Table: Variable-size entries (depends on outlier count)

The key design choice is organizing KV cache *per attention head, per token, sequentially* so that reading all past tokens for a given head is a single burst access.

---

## Q2: The Key Insight

**The "Magic Trick":** The central insight is that **outlier thresholds are model-dependent but data-independent** (Observation 2, Section 4.1, Figure 6(b)). This is the lynchpin that makes the whole system work.

Prior KV cache quantization methods like KVQuant require expensive online operations:
- **Online topK sorting** to find outliers: O(n log n) per vector — devastating at scale
- **Mixed-precision storage** (FP16 for outliers): 23 bits per outlier (16 value + 6 index + 1 flag)

Oaken's hybrid approach flips this:
1. **Offline**: Pay the O(n log n) cost *once during profiling* to find thresholds
2. **Online**: Simple threshold comparisons — O(1) per element — to classify values

The second clever piece is **group-shift quantization** (Section 4.4). Instead of storing outliers at FP16 (16 bits), they shift the value range by subtracting the threshold, then quantize to 5-bit. Combined with the fused encoding (where 4 bits hide in the zeroed dense slots), each outlier costs only **8 bits** instead of 23 bits.

**Structural delta from baseline:** The standard approach stores KV cache as FP16 dense tensors. Oaken adds:
- A threshold register file per layer (4 thresholds × 2 (K,V) × N_layers)
- Decomposer hardware to route values to three parallel paths
- Dual management tables in the MMU (dense + sparse)
- OR-merge logic on the dequantization path

This is fundamentally different from channel reordering approaches (QServe, Atom, Tender) which apply transformation matrices. Oaken keeps the original channel order but classifies *within* each per-token vector.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Apples-to-apples memory comparison (Table 1):** They configure Oaken-HBM with identical memory specs to A100 (80GB HBM, 2.0 TB/s). This isolates the algorithmic contribution from the memory technology. The LPDDR variant (256GB, 1.1 TB/s) then demonstrates the capacity scaling story separately.

2. **Comprehensive accuracy sweep (Table 2):** Eight models across four datasets with six baselines. Critically, they report **effective bitwidth** alongside accuracy — this is the honest metric. Oaken achieves 4.82-4.89 effective bits vs. KIVI's 4.99 bits, explaining the accuracy delta. The 0.87% average accuracy loss vs. FP16 is substantiated.

3. **Real-world trace evaluation (Figure 14):** Using Azure's Conversation and BurstGPT traces adds credibility. The Conversation trace has short output lengths where Oaken shows modest gains; BurstGPT with longer outputs shows larger gains — this is intellectually honest about when the technique helps.

4. **Latency breakdown (Figure 12(b)):** They explicitly show quantization/dequantization overhead (1.29% and 3.23% at batch 64). The "Oaken-GPU" column reveals the algorithm doesn't map well to GPU (warp divergence), justifying the custom hardware.

5. **Hardware synthesis (Table 4):** RTL synthesis on TSMC 28nm with actual area numbers (quantization engine: 0.074mm², 1.86% of core). This is concrete evidence, not hand-wavy estimation.

### Weaknesses

1. **Baseline hardware inconsistency:** Tender is given A100-equivalent specs "for fair comparison" (Section 6.1), but it's fundamentally a different microarchitecture (systolic arrays with tensor decomposition). The comparison conflates algorithmic and architectural differences. A fairer comparison would run Oaken's algorithm on Tender's architecture and vice versa.

2. **Profiling cost underspecified:** "Approximately ten minutes" for Llama2-70B profiling (Section 6.1) hides critical details. What hardware? Is this amortized across serving instances? What if the model is fine-tuned — do thresholds need re-profiling?

3. **Outlier ratio sensitivity glossed over:** They fix outer/middle/inner at 4%/90%/6% globally (Section 6.1), claiming "marginal" impact. But Table 3 shows Wikitext2 perplexity varies from 5.516 to 5.804 depending on configuration — that's a 5% relative change. For production, this hyperparameter tuning is non-trivial.

4. **Missing memory bandwidth utilization:** The paper claims "maximizes memory bandwidth utilization" (Section 5.2) but never reports actual achieved bandwidth. Figure 11's throughput could be compute-limited or bandwidth-limited — we can't tell.

5. **No power normalization:** Table 4 reports 222.7W total power but compares throughput against A100 at 400W TDP. A throughput/Watt comparison would be more meaningful, especially since the capacity story targets cost-efficiency.

6. **Sparse matrix overhead variability:** The 4%/6% outlier ratio is global, but Figure 6(c) shows outliers cluster in specific channels. Per-layer outlier ratios must vary significantly. How does the MMU handle this variance? What's the worst-case storage overhead?

---

## Q4: What the Authors Didn't Tell You

### Hidden Hardware Costs

1. **The threshold register file:** Four FP16 thresholds per layer, per K/V, per attention head. For Llama2-70B with 80 layers and 64 heads: 4 × 2 × 80 × 64 × 2 bytes = 81.92 KB of on-chip storage. Not huge, but unmentioned.

2. **Dual management tables scale poorly:** Figure 10 shows tables indexed by "up to the maximum sequence length per attention head." For 32K sequences with 32 heads and 80 layers, the dense table alone needs 32K × 32 × 80 × 8 bytes = 655 MB of metadata. The sparse table adds variable overhead. Where does this live? They never say.

3. **Zero-remove and zero-insert shifters:** These are barrel shifters with variable shift amounts, cited to [57, 67] (Minsoo Rhu's compressing DMA work). For a 64-wide datapath, that's 64× log2(64) = 384 muxes per shifter, with two shifters (quant + dequant). The "streaming" claim (Section 5.2) requires these to match memory bandwidth — non-trivial.

4. **Decomposer comparison parallelism:** Every element must be compared against 4 thresholds simultaneously. For a 256-element vector arriving per cycle, that's 1024 comparators in the critical path.

### Assumptions That May Not Hold

1. **Data-agnostic thresholds (Observation 2):** Figure 6(b) shows key range stability across Wikitext2/PIQA/Hellaswag. But these are all relatively "normal" text. What about code generation? Mathematical reasoning? Multilingual prompts? The authors never test distribution shift.

2. **Outlier locality assumption:** The fused dense-and-sparse encoding assumes outliers have meaningful location information worth 6 bits of index. But if outliers are essentially random (the "discontinuous dots" in Figure 6(c)), this encoding is suboptimal vs. simple run-length encoding.

3. **Burst access feasibility:** Section 5.2 claims sequential layout enables burst reads. But LPDDR5 has strict burst length requirements (typically 16 or 32 beats). If a token's KV cache doesn't align to burst boundaries, they waste bandwidth. The sparse matrix's variable size makes this worse.

### What the Evaluation Hides

1. **Prefill phase is untouched:** All throughput numbers in Figure 11 measure generation phase. The prefill phase processes multiple input tokens and is compute-bound (Figure 3(c) shows high utilization during QKV Gen). For short prompts with long outputs, this matters less; for RAG workloads with 32K context, prefill dominates.

2. **The 1K:1K sequence length configuration:** Most experiments use 1K input, 1K output. Figure 13 sweeps sequence length but only for batch=16. The cross-product of (large batch) × (long sequence) is the hard case — they show batch=256 at 1K:1K, or batch=16 at 32K, but not batch=128 at 16K.

3. **Temperature effects:** The authors profile thresholds offline at some inference temperature. KV cache distributions shift with sampling temperature — confident predictions (low temp) produce sharper attention patterns and potentially more outliers.

4. **GQA/MQA favorable to baselines:** Mistral-7B, Mixtral-8x7B, and Llama2-70B use grouped-query attention which reduces KV cache size by 4-8×. The paper notes this "alleviate[s] bandwidth bottlenecks even without KV quantization" (Section 6.2). These models favor the FP16 baselines — Oaken's relative gains are smaller precisely where modern models are heading.