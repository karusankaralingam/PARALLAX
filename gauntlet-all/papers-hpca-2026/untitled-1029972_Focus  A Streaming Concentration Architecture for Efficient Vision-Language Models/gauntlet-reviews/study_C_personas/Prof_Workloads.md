Q1: Whiteboard Explanation

Imagine you're watching a video and answering questions about it. A Vision-Language Model (VLM) processes video frames by converting them into thousands of "tokens" (small image patches) and combines them with text tokens from your question. The problem? Most of these visual tokens are redundant—adjacent frames share similar backgrounds, and many patches within frames look alike.

**Focus** is a hardware accelerator that removes this redundancy at three levels:

1. **Semantic Level (Token Pruning):** Which tokens matter *for this specific question*? If you ask "What color is the dog?", Focus uses cross-modal attention scores to identify that dog-related tokens are important, while background tokens can be pruned. This is prompt-aware—asking about the flower shifts attention entirely.

2. **Block Level (Spatial-Temporal Grouping):** Focus groups remaining tokens into 2×2×2 blocks spanning space (2×2 within a frame) and time (2 adjacent frames). Within each block, it compares the "key" token against its 7 neighbors using cosine similarity.

3. **Vector Level (Fine-Grained Matching):** Instead of comparing entire tokens (3584 dimensions), Focus breaks them into 32-dimensional vectors. This captures *partial* overlaps—when motion causes a token in Frame A to partially match multiple tokens in Frame B.

The key architectural insight: all compression happens **on-chip, within GEMM tiles**, avoiding expensive off-chip memory traffic. The compressed output is written back to DRAM, not the original dense representation.

---

Q2: The Key Insight

The central insight is **granularity matters for redundancy detection**: token-level similarity matching (used by prior work like CMC and AdapTiV) misses fine-grained redundancy caused by motion and partial overlaps. Figure 2(b) reveals this starkly: only 18% of full 3584-dimensional tokens exceed 0.9 cosine similarity, but **64% of 8-dimensional vectors do**. By operating at vector granularity within spatiotemporal blocks, Focus achieves 82.8% sparsity versus ~50% for baselines (Figure 2(c), Table II).

The second insight is **hardware-algorithm co-design**: prior methods like CMC perform compression *after* writing to DRAM (Figure 3a), wasting bandwidth. Focus performs compression immediately after each GEMM tile completes, before write-back (Figure 3b). This transforms algorithmic sparsity into actual memory savings—Focus uses only 21% of dense DRAM traffic versus CMC's 79% (Section VII-F).

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive benchmark coverage:** Three VLMs (Llava-Video, Llava-OneVision, MiniCPM) across three video understanding datasets (VideoMME, MLVU, MVBench) and three image datasets (VQAv2, MME, MMBench). This diversity strengthens generalization claims.

2. **Apples-to-apples hardware comparison (Table III):** Same technology node (28nm), frequency (500MHz), PE array size (1024 PEs), and DRAM bandwidth (64GB/s) across all baselines. The authors even re-implemented AdapTiV and CMC in SystemVerilog for fair area/energy comparison.

3. **Ablation study (Figure 11):** Clearly decomposes contributions—SEC alone gives 3.15× speedup, adding SIC provides additional 1.44×. This validates that both components are necessary.

4. **Memory traffic analysis (Section VII-F, Figure 12):** Direct measurement showing Focus achieves 4.9× DRAM traffic reduction versus dense baseline, not just theoretical sparsity.

**Weaknesses:**

1. **The "Cherry-Pick" Check—Workload Selection:** All evaluated VLMs are 7B-parameter models with similar architectures (Qwen2-based LLMs, ViT encoders). The paper claims Focus is "the first architecture tailored for VLMs" but doesn't evaluate on architecturally diverse models (e.g., Flamingo-style cross-attention VLMs, or smaller/larger model scales). MiniCPM shows notably worse accuracy degradation on MLVU (55.89→53.59) suggesting model sensitivity.

2. **Baseline Validity Concerns:** AdapTiV [70] and CMC [56] were designed for Vision Transformers/video transformers, not VLMs. The paper "extends their designs to make them compatible with VLMs" (Section VII-A), but doesn't detail these modifications. Did they tune hyperparameters fairly? CMC's accuracy collapse on MiniCPM-MLVU (55.89→43.80, Table II) suggests possible implementation issues rather than fundamental limitations.

3. **The "Zero-Event" Reality—Semantic Pruning Configuration:** The retention schedule (40%/30%/20%/15%/10% at layers 3/6/9/18/26, Table I) was determined through hyperparameter search but is **static across all inputs**. The authors acknowledge this: "Future work may further enhance this strategy by dynamically adapting to input contexts" (Section VII-D). For videos with uniformly important content (e.g., dense action sequences), this fixed schedule may be suboptimal.

4. **GPU Comparison is Weak:** Comparison against Jetson Orin Nano (an edge GPU) shows 7.9× speedup, but this is comparing a custom ASIC to a mobile SoC. No comparison against datacenter GPUs (A100/H100) running optimized kernels, which is where VLM deployment actually happens.

5. **Figure Presentation Issues:** Figure 10(a) Y-axis starts at 0, which is good, but the "Normalized Latency" metric obscures absolute performance. Figure 9's speedup bars lack error bars despite processing multiple samples per dataset.

---

Q4: What the Authors Didn't Tell You

1. **Worst-case accuracy degradation is concerning:** While average accuracy drop is 1.20% (Section VII-B), MiniCPM on MVBench drops from 55.63% to 54.30% (1.33 pp), and on MLVU from 55.89% to 53.59% (2.30 pp). Table II shows CMC completely fails on MiniCPM-MLVU (43.80% accuracy), but Focus also shows notable degradation there. The authors don't analyze *which* question types suffer most from pruning.

2. **The 2.7% area overhead claim hides absolute numbers:** The Focus Unit adds 0.09mm² to a 3.12mm² baseline (Table III), but this excludes the 16KB layouter buffer (Section VI-B). The "734KB total" buffer (Table I) is actually the same as baseline, achieved by repurposing existing output buffer capacity.

3. **Semantic concentration only works in attention layers:** SEC operates during attention computation to extract cross-modal importance (Section V). For models with different attention patterns (e.g., sparse attention, grouped-query attention), the importance analyzer design may need modification.

4. **The convolution-style layout introduces constraints:** The 2×2×2 block structure (Figure 6) assumes regular spatial token layout. For models with dynamic resolution or non-square patches (like Qwen2.5-VL's native resolution handling), the fixed block structure may not align well—Table V shows smaller speedups on Qwen2.5-VL (1.78-1.97×) versus Llava-OneVision (4.25-4.44×).

5. **INT8 quantization interaction (Table IV) shows compounding error:** While presented positively, combining Focus with INT8 causes 0.5% additional accuracy loss versus 0.02% for dense+INT8. This suggests Focus's similarity matching is sensitive to quantization noise—a practical concern since production systems typically use quantization.

6. **The "streaming" claim has caveats:** While SEC and SIC are described as streaming, the similarity scatter phase (Section VI-C) requires accumulating partial sums across K/k iterations before producing final outputs. For layers with large K (e.g., K=3584), this accumulation dominates the pipeline and limits true streaming benefits.