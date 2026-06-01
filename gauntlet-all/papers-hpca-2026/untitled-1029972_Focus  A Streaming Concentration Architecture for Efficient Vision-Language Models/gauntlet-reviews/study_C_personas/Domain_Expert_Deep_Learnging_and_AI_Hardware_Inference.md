# Focus: A Streaming Concentration Architecture for Efficient Vision-Language Models

## Q1: Whiteboard Explanation

Alright, let me sketch this out for you like I'm drawing on a napkin at a conference dinner.

**The Problem They're Solving:**
Vision-Language Models (VLMs) process video by chopping each frame into patches, turning them into tokens, and feeding them through a Transformer. The issue? A video generates *thousands* of tokens (the paper notes 6,272 visual tokens vs. only 109 text tokens on average for LLaVA-OneVision on VideoMME). Most of these tokens are redundant—think of a video where the background barely changes frame-to-frame, or where most of the scene is irrelevant to the question being asked.

**The Core Idea (Multilevel Concentration):**
Focus removes redundancy at three progressively finer granularities:

1. **Semantic Level (Token Pruning):** "What does the question actually care about?" If you ask "What color is the flower?", tokens representing the dog are useless. Focus uses the cross-modal attention scores (the text-to-image attention block in the QK^T matrix) to identify which visual tokens the text query is actually "looking at." It keeps the top-k most important ones and throws away the rest. This happens *during* the attention computation itself—no extra pass needed.

2. **Block Level (Spatiotemporal Similarity):** Imagine a 2×2×2 cube spanning 2 spatial positions in height, 2 in width, and 2 frames in time. Within this cube, Focus picks one token as the "key" and compares it against its 7 neighbors. If they're similar enough, the redundant ones can be removed. This is like a 3D convolution sweep across the video.

3. **Vector Level (Sub-token Matching):** Here's the clever bit. A full token embedding might be 3584 dimensions. At that granularity, two tokens rarely match perfectly. But if you chop the embedding into 32-dimensional vectors, you find *way* more matches. Figure 2(b) shows that 64% of 8-dimensional vectors exceed 0.9 cosine similarity, versus only 18% for full 3584-dimensional tokens. Focus exploits this by doing similarity matching at the vector level, not the token level.

**The Architecture Magic:**
The killer insight is that all of this maps beautifully onto GEMM tiling, the standard way systolic arrays (like TPUs) process matrix multiplications. When a systolic array computes an output tile (say, 1024 tokens × 32 vector dimensions), Focus intercepts that tile *before* it gets written to DRAM, performs the similarity check on-chip, and only writes the *compressed* result to memory. This is the "streaming" part—no global buffering, no round-trip to DRAM for compression.

The "Convolution-style Layouter" (Section VI-B) is a neat trick to ensure that the 8 tokens in any 2×2×2 block land in 8 different SRAM banks, enabling conflict-free parallel reads without replicating data.

---

## Q2: The Key Insight

**The "Delta" is the realization that redundancy in VLMs exists at three distinct, exploitable granularities—and that all three can be removed *on-chip, in a streaming fashion, aligned with GEMM tiling*.**

Let me unpack this:

**Prior work operated at the wrong granularity and in the wrong place:**
- **AdapTiV [70]** merges *whole tokens* based on sign-bit similarity. This is coarse—it misses the sub-token redundancy that Focus captures.
- **CMC [56]** uses an external video codec (H.264-style) to find inter-frame redundancy. The problem? This requires writing all tokens to DRAM *first*, then reading them back into a codec unit, compressing, and writing the compressed result back. As Section III-B states, CMC achieves 46% sparsity but still incurs **79% of dense DRAM traffic**. The codec doesn't align with the GEMM compute flow.

**Focus's trick is to operate *inside* the GEMM tile:**
The paper's core architectural innovation is that vector-level similarity (32 dimensions) naturally aligns with the output tile dimensions of a systolic array (they set `m=1024` tokens and `n=32` vector width). When a tile is produced, Focus compares vectors *immediately*, discards redundant ones, and writes only the unique vectors plus a small "similarity map" to DRAM. This eliminates the round-trip that kills CMC.

**The Semantic Concentrator is integrated, not bolted-on:**
The importance analyzer (Section V-A) extracts the text-to-image attention scores *during* the normal softmax computation. The top-k sorter (Section V-B) runs *in parallel* with the image self-attention computation (Q^(image)K^T), which takes far longer. The scheduling analysis in Figure 5 shows that the sorter completes well before the GEMM finishes, so it's completely hidden.

**Quantitative proof of the insight:** Table II shows Focus achieves **80.19% average sparsity** versus AdapTiV's 42.8% and CMC's 48.2%, with *higher* accuracy. Figure 12 shows Focus reduces DRAM access to **21% of dense** versus CMC's 79%. This is the payoff of operating at the right granularity, in the right place.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Comprehensive Model and Dataset Coverage:** The evaluation spans three distinct VLMs (LLaVA-Video-7B, LLaVA-OneVision-7B, MiniCPM-V-2.6) and three video benchmarks (VideoMME, MLVU, MVBench). This isn't a one-model wonder. Table II shows consistent sparsity gains across all nine combinations.

2. **End-to-End Hardware Evaluation with RTL:** They don't just claim theoretical speedups. The architecture is implemented in SystemVerilog, synthesized at TSMC 28nm with a real memory compiler, and simulated cycle-accurately with SCALESim-v2 (Section VII-A). Table III provides apples-to-apples area/power comparisons: Focus adds only **2.7% area** and **0.9% power** over the vanilla systolic array.

3. **Fair Baseline Comparisons:** They re-implement AdapTiV and CMC in the same RTL toolchain (Section VII-A), ensuring the comparison isn't polluted by different process nodes or design assumptions. The GPU baseline includes the Jetson Orin Nano with and without FrameFusion.

4. **Ablation Study (Figure 11):** The paper cleanly decomposes the contribution: SEC alone gives 3.15× speedup over dense, SIC adds another 1.44× on top. This is good hygiene—you can see each component's value.

5. **Memory Traffic Analysis (Figure 12):** This is critical. They show Focus achieves **0.21× DRAM access** versus dense, while CMC is at 0.79×. This directly validates the "on-chip compression before write-back" claim.

6. **Quantization Synergy (Table IV):** They test INT8 quantization and show Focus maintains its sparsity benefits (only 0.13% sparsity change), addressing an obvious follow-up question.

### Weaknesses

1. **Baseline Vintage:** AdapTiV (MICRO'24) and CMC (ASPLOS'24) are recent, but the GPU baseline is a **Jetson Orin Nano**, a 7-15W edge device with modest Tensor Core capability. Comparing a custom 28nm ASIC against this is like comparing a sports car to a bicycle. Where's the comparison against an A100 or even an H100 running optimized inference (e.g., with vLLM or TensorRT)? The "7.90× speedup over GPU" claim (Section VII-C) is against this weak baseline.

2. **No Latency Breakdown for LLM Decoding:** VLMs have a prefill phase (processing all visual tokens) and a decode phase (generating text token-by-token). Focus clearly accelerates prefill, but the paper never isolates decode-phase performance. For interactive applications, decode latency often dominates user-perceived latency. Is the similarity map overhead amortized well during decode?

3. **Accuracy Degradation on MiniCPM:** Table II shows MiniCPM-V-2.6 accuracy drops significantly on MLVU (55.89 → 53.59, a 2.3 point drop) and MVBench (55.63 → 54.30). CMC fares even worse, but Focus's drop is non-trivial. The paper doesn't discuss *why* MiniCPM is more sensitive.

4. **Fixed Semantic Pruning Schedule (Table I):** The retention ratios (40%/30%/20%/15%/10% at layers 3/6/9/18/26) are manually tuned. Section VII-D acknowledges "future work may further enhance this strategy by dynamically adapting to input contexts." This is a significant limitation—the optimal schedule likely varies by video content and query complexity.

5. **Limited Scalability Analysis:** The evaluation is on 7B parameter models. Modern VLMs (GPT-4V, Gemini 1.5, LLaVA-OneVision-72B) are much larger. Does the convolution-style layouter scale? Does the 2×2×2 block size remain optimal at 72B scale?

6. **Image VLM Evaluation is Thin:** Table V shows results on image VLMs (Qwen2.5-VL, LLaVA-OneVision on VQAv2/MME/MMBench), but the speedups are notably lower (1.78× to 4.44×) than video (2.35× to 4.47× average). The paper admits "temporal similarity is no longer present" but doesn't deeply analyze whether Focus is worth the area overhead for image-only workloads.

7. **No Comparison to FlashAttention or Paged Attention:** For software baselines, they compare against vanilla PyTorch and FrameFusion. But FlashAttention-2 is the de facto standard for attention acceleration on GPUs. How does Focus compare against a highly optimized software stack on a high-end GPU?

---

## Q4: What the Authors Didn't Tell You

1. **The "80% sparsity" claim hides real-world irregularity.**
   Figure 13 shows a *distribution* of concentrated tile lengths, with an average utilization of 92.2%. But look at the tails: some tiles compress heavily (high sparsity, low utilization), others barely compress at all. The paper mentions the "worst case" (near-zero sparsity) preserves correctness, but doesn't quantify *how often* worst-case tiles occur or their impact on tail latency. For real-time video applications, tail latency matters.

2. **The "streaming" claim has a subtle caveat.**
   Similarity Gather operates on-chip, but Similarity Scatter (Section VI-C) must *reconstruct* the full m=1024 output before the next GEMM layer can proceed. They use a 2a-wide accumulator (64 elements) to overlap accumulation with streaming, but this fundamentally limits pipeline depth. If the K dimension is small (K < 256, per Section VI-A), the similarity matcher *can* become the critical path. The paper hand-waves this as a "corner case" but doesn't quantify how often modern VLM layers hit this regime.

3. **The convolution-style layouter assumes spatial locality persists after semantic pruning.**
   Semantic pruning removes tokens based on *attention scores*, not spatial position. After pruning, the remaining tokens may be scattered across the frame, breaking the 2×2×2 spatial structure. The paper addresses this with "offset encoding" (Section V-C, Figure 7), but this adds metadata overhead and complexity. The paper never quantifies the overhead of this offset encoding in terms of bits per token or memory traffic.

4. **The evaluation doesn't stress-test adversarial inputs.**
   What happens with a video of rapidly changing, visually distinct frames (e.g., a montage or fast-cut action sequence)? The paper shows results on VideoMME, MLVU, and MVBench, which likely contain "normal" video content. An adversarial evaluation with high-motion, low-redundancy content would better characterize the design's robustness.

5. **The area comparison is somewhat misleading.**
   Table III shows Focus at 3.21 mm² versus CMC at 3.58 mm² and AdapTiV at 3.38 mm². But CMC includes an *external video codec*, which is not a fair comparison if you're only accelerating VLMs. The relevant comparison is Focus's 2.7% overhead versus the vanilla systolic array—but then you should compare against AdapTiV's simpler similarity logic, which also adds minimal overhead.

6. **No discussion of training or fine-tuning.**
   Focus assumes inference on pre-trained models. But if the model were *aware* of Focus's concentration strategy during training, accuracy might be preserved better. The paper is purely inference-focused, which is valid, but limits the optimization space.

7. **The generalization to Vision-Language-Action (VLA) models (Section VIII-A) is speculative.**
   The paper claims Focus "can potentially be extended to VLA models" but provides no evidence. VLA models have action prediction heads that may be sensitive to token ordering or spatial structure in ways that differ from VQA tasks.