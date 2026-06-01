# Paper Deconstruction: Focus — A Streaming Concentration Architecture for Efficient Vision-Language Models

## Q1: Whiteboard Explanation

Alright, let me draw this out for you. Forget the jargon for a moment.

**The Problem They're Solving:**

Vision-Language Models (VLMs) take video frames, chop them into patches (tokens), and feed them to a big language model alongside a text question. Here's the issue: a single video sample can have 6,272 visual tokens versus only 109 text tokens (Section II-A). Most of those visual tokens are *redundant*. Adjacent video frames show the same dog walking across the same grass. The model is doing massive matrix multiplications on pixels that are basically duplicates.

Prior work tried to fix this by "pruning" or "merging" tokens, but they operated at the **token level** (coarse-grained) and often required expensive **global comparisons** across all tokens. This doesn't map well onto hardware accelerators like systolic arrays, which process data in fixed-size **tiles**.

**What Focus Actually Does (The Three Levels):**

Think of it like a three-stage compression pipeline, each operating at a finer granularity:

1. **Semantic Concentration (Token Level):** This is the "prompt-aware attention filter." When you ask "What color is the *flower*?", the attention map tells you which tokens are relevant (the flower pixels). Focus extracts the text-to-image attention scores from the existing Softmax computation (it's already being computed!), identifies the top-k most important tokens, and *prunes* the rest. The key insight from Figure 2(a) is that "important" is prompt-dependent—asking about the dog vs. the flower activates different image regions.

2. **Block Concentration (Spatial-Temporal Level):** After pruning, you still have many tokens. Focus groups the remaining tokens into small `2×2×2` blocks—that's 2 pixels wide, 2 pixels tall, across 2 adjacent frames. Within each block, it picks one token as a "key" (the last one, token `h` in Fig. 1(b)) and compares it against its 7 neighbors. If they're similar (cosine similarity > 0.9), one is kept, the other is marked as a duplicate. This is like a **3D sliding window** (convolution-style), not a global all-to-all comparison.

3. **Vector Concentration (Sub-Token Level):** This is the "secret sauce." A token isn't an atomic unit—it's a vector of, say, 3584 dimensions. Focus splits each token vector into smaller **32-dimensional chunks**. The key observation from Figure 2(b) is that 64% of these small 8-dim vectors have cosine similarity > 0.9 with neighbors, but only 18% of full 3584-dim token vectors do. Smaller vectors reveal more redundancy. So Focus does its similarity matching at the **vector level**, not the token level. This is the core algorithmic novelty.

**The Hardware Trick:**

The beauty is how they align this with GEMM (matrix multiply) tiling. Systolic arrays process matrices in small tiles (e.g., 1024 vectors × 32 dimensions). Focus performs its concentration **within each tile**, entirely on-chip, *before* writing results to DRAM. See Figure 3(b): the tile is produced by the PE array, immediately sent to the "Focus Unit" for concentration, and only the compressed output goes to off-chip memory. This eliminates the need for global buffers or off-chip trips just to do compression—a critical flaw of prior work like CMC (Section III-B).

---

## Q2: The Key Insight

The **delta** of this paper is the decomposition of redundancy elimination into three orthogonal, hierarchically structured levels that *align naturally with hardware execution patterns*.

**The Real Insight is at the Vector Level (Mechanism):**

Prior work operated on whole tokens. Focus's core contribution is recognizing that a **token is not the right unit of granularity for similarity**. They show empirically (Fig. 2(b)) that sub-token vectors (e.g., 32-dim chunks) are far more likely to be redundant than full tokens. This is because of *motion*—a dog walking to the right means token `h` in frame B doesn't perfectly match token `h` in frame A, but *parts* of token `h` might match *parts* of neighboring tokens `c` and `d`. Vector-wise matching captures this "partial overlap" (Fig. 1(c)).

This is a representation/mechanism insight, not just a policy change. The claim in Figure 2(c) is the proof: their vector-wise method achieves **82.8% sparsity** compared to 73.0% for token-wise (AdapTiV) and 54.0% for codec-based (CMC), at equivalent or better accuracy.

**The Policy Insight (Semantic Level):**

The semantic concentration isn't novel in the sense of "use attention for pruning"—others have done this. The contribution here is making it **prompt-aware** and **integrated into the existing attention computation path** without adding overhead (Section V-A). They extract the `Text-to-Image` block from the `Softmax(QK^T)` matrix that's already being computed. The top-k sorter (Section V-B) is a pipelined bubble sort that completes while the main `Q^(image)K^T` GEMM is still running, so it's fully hidden.

**The System-Level Insight (Architecture):**

The architectural contribution is the **streaming, tile-local execution model**. CMC writes full tokens to DRAM, then runs a separate codec pass (Fig. 3(a)). Focus compresses *in-flight*, within the GEMM tile boundary, using only on-chip buffers (Fig. 3(b)). The "convolution-style layouter" (Section VI-B) is a clever addressing scheme that maps tokens from different frames into 8 distinct memory banks, enabling **conflict-free parallel reads** for the `2×2×2` block comparison without data duplication (a common cost in CNN accelerators).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Strong and Fair Baselines:** They compare against AdapTiV (MICRO'24) and CMC (ASPLOS'24), which are genuine state-of-the-art ViT/video transformer accelerators. They also compare against a vanilla systolic array and an NVIDIA Jetson Orin Nano GPU with FrameFusion (a recent algorithmic baseline). This is a solid baseline set—no strawmen here (Table III).

2. **Iso-Resource Comparison:** All hardware baselines are synthesized at the same 28nm node, 500MHz, 32×32 PE array, 64GB/s DRAM bandwidth (Table III). This is critical for fair comparison. They even implemented the baselines' core logic in SystemVerilog themselves for area/energy estimates.

3. **Comprehensive Accuracy Reporting:** Table II reports accuracy on three VLMs (Llava-Video, Llava-OneVision, MiniCPM) across three video benchmarks (VideoMME, MLVU, MVBench). The average accuracy degradation for Focus is only **1.20%** compared to dense models, while achieving **80.19% sparsity**. This directly addresses the "did they hide the accuracy impact?" question.

4. **Ablation Study is Informative:** Figure 11 shows the incremental contribution of each component. SEC alone gives 3.15× speedup; adding SIC pushes it to 4.53×. This confirms both modules contribute meaningfully.

5. **Memory Traffic Analysis:** Figure 12 shows Focus achieves **4.9× reduction in DRAM traffic** compared to dense, and **3.7× and 2.2× reductions** compared to CMC and AdapTiV, respectively. This directly validates the "on-chip, tile-local compression" claim.

### Weaknesses

1. **Context Length is Short:** The benchmarks used (VideoMME, MLVU, MVBench) produce ~6,272 visual tokens on average (Section II-A). This is not a "long context" setting by LLM standards (which now routinely handle 32k-128k tokens). The paper does not show what happens when the number of video frames scales significantly. Does the 80% sparsity hold for a 10-minute video vs. a 10-second clip? The design space exploration (Fig. 10(a)) hints at sensitivity to tile size, but doesn't explore scaling.

2. **No Perplexity or Generative Quality Metrics:** The accuracy metrics are all multiple-choice or score-based benchmarks (VideoMME accuracy, MME score). For VLMs that generate free-form text (e.g., video captioning), perplexity or BLEU/CIDEr scores would be more informative. The paper mentions video captioning as a use case (Section I) but doesn't evaluate it.

3. **Semantic Pruning Configuration is Static and Hand-Tuned:** Table I reveals the retention ratios are fixed per layer (40%/30%/20%/15%/10% at layers 3/6/9/18/26). Section VII-D acknowledges this: "Future work may further enhance this strategy by dynamically adapting to input contexts." This is a limitation for workloads with varying information density (e.g., a video transitioning from a static scene to rapid action).

4. **Comparison Threshold (0.9 cosine similarity) is a Hyperparameter:** The paper fixes the similarity threshold at 0.9 (Table I). What happens at 0.95 (more conservative, less sparsity) or 0.85 (more aggressive, potential quality loss)? The sensitivity analysis for this key parameter is missing.

5. **MiniCPM Results Show Fragility:** In Table II, CMC on MiniCPM-V with MLVU drops accuracy to **43.80%** (vs. 55.89% dense), and Focus itself drops to **53.59%**. This is a 2.3 percentage point loss vs. the ~1% average loss claimed. The method seems to interact poorly with certain model architectures or datasets.

6. **No Comparison to vLLM-style PagedAttention:** The paper is published at HPCA and focuses on systolic array accelerators, which is appropriate. However, the broader LLM systems community cares about GPU-based serving systems like vLLM. There's no discussion of whether Focus's techniques could apply to or compete with PagedAttention-based KV-cache management on GPUs during the decoding phase.

---

## Q4: What the Authors Didn't Tell You

1. **The "80% sparsity" claim buries the lede.** The sparsity is computed as "operations using the method / operations required by the systolic array with original input" (Section VII-B). This is *computational* sparsity. But the actual **memory footprint reduction** for activations is different. Figure 12(b) shows activation size is reduced to ~0.18× of dense—so ~82% reduction, which aligns. However, the **similarity map** itself (Section VI-A) is metadata overhead. It's described as "1 × m per tile" (m=1024), storing indices. This is a few KB per tile—small, but not zero. The paper doesn't quantify the total metadata overhead or its impact on DRAM traffic in detail.

2. **The Similarity Scatter/Gather Machinery is Complex.** Section VI-C describes how, because different sub-tiles have different subsets of concentrated vectors, you can't just accumulate partial sums naively. You need a "Similarity Scatter" module that uses the map from the *previous layer's* gather phase to replicate and redistribute partial sums. This is a non-trivial control flow dependency between layers. The paper claims it's "off the critical path," but the `2a=64` wide accumulator (for `a=32`) hints at the need to double accumulator width to handle the reconstruction throughput. This is a real area cost that's somewhat glossed over.

3. **The "Prompt-Aware" Claim Needs Nuance.** The semantic concentrator uses the **text-to-image attention** from the *current* layer being processed. This means the "importance" estimate is based on how much the *current* layer's text query attends to image tokens. But attention patterns evolve across layers. Early layers might have diffuse attention; later layers might be more focused. The paper prunes aggressively in early layers (40% retention at layer 3). Is the early-layer attention signal reliable enough for this? The accuracy results suggest "yes, empirically," but the theoretical justification is thin.

4. **Generalization to Image VLMs (Table V) is Weaker Than Video.** Table V shows results on image-only VLMs (Llava-OneVision, Qwen2.5-VL). The speedups are there (up to 4.44×), but for Qwen2.5-VL, AdapTiV *matches or beats* Focus on speedup (1.96× vs 1.91× on VQAv2, 1.89× vs 1.97× on MME, 1.93× vs 1.78× on MMBench), while Focus has better accuracy. The paper frames this as "Focus generalizes beyond video," but the honest read is that the *temporal* redundancy in video is where Focus shines. On single images, the advantage is marginal.

5. **The Worst-Case Analysis (Section VIII-B) is Hand-Wavy.** They state: "In the worst case, when no similarity exists... sparsity drops near zero. The design preserves the full tile length." This means in the worst case, you've paid the area cost of the Focus Unit (2.7%) and get zero benefit. The histogram in Figure 13 shows the distribution of compressed tile lengths, with average utilization at 92.2%. But the tail shows some tiles near 800-1000 vectors (i.e., little compression). How often do these occur, and on what types of content?

6. **No Discussion of Interaction with KV-Cache Compression.** For LLM inference (the 'L' in VLM), KV-cache memory is a major bottleneck during the decode phase. Focus targets the prefill phase (processing visual tokens). There's no discussion of whether Focus's sparsity propagates benefits to the decode phase, or whether the pruned tokens need to be "remembered" in the KV-cache. For pure visual QA (one-shot answer), this is fine. For multi-turn dialogue about a video, this could be a limitation.