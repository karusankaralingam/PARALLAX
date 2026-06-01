# Paper Deconstruction: Hybe (ISCA '25)

## Important Note on Persona Mismatch

Before I begin, I must acknowledge that this paper is **not about Homomorphic Encryption**. It's about **LLM inference acceleration** with a focus on million-token context windows. My persona is designed for HE accelerator papers, so I'll adapt my skeptical, mechanism-focused approach to this domain. The core skills—finding the real contribution, checking evaluation methodology, and identifying what authors downplay—transfer directly.

---

## Q1: Whiteboard Explanation

Let me draw this out for you.

**The Problem in Three Sentences:**
LLM inference has two phases: **prefill** (process all input tokens at once → GEMM, compute-bound) and **decode** (generate one token at a time → GEMV, memory-bound). GPUs have thousands of cores optimized for GEMM, so during decode, ~99.6% of those cores sit idle while you're still burning 84% of peak power (Section 3.3, Figure 3). When context windows hit 1M tokens, the KV cache balloons to 137GB (Table 1), making batching impossible and leaving GPUs criminally underutilized.

**The Hybe Solution:**
Use the GPU *only* for prefill (where it excels), then hand off to a fleet of lightweight NPUs for decode (where you need memory bandwidth, not FLOPS). Think of it like using a sports car to sprint to the highway, then transferring to a fuel-efficient sedan for the long cruise.

**The NPU Architecture (Section 5, Figure 6):**
- Designed to be "bandwidth-balanced"—compute is sized *exactly* to saturate HBM bandwidth
- 32 MAC trees × 64-element vectors × 1GHz = 4 TFLOPS (matching 3.35 TB/s HBM3)
- Output-stationary dataflow with hardware-aware memory mapping (Figure 5)
- The entire chip is 0.84 mm² and draws 0.29W (Figure 10)—basically a rounding error next to an H100

**The Scheduling Magic (Section 6):**
1. **Fine-grained KV transmission (FGKVT):** Don't wait until prefill finishes to send KV cache. Send partial results immediately, overlapping transfer with remaining attention computation. This shrinks GPU memory requirements from 3× KV to just 1× KV (Figure 7).

2. **Stage-wise pipelining with overloading/offloading (Figure 9):** When decode finishes early (short output), let NPU start the next request's prefill. When prefill finishes early, let GPU steal work from NPU. Dynamic load balancing.

---

## Q2: The Key Insight

**The Real Contribution:** The paper's genuine insight is recognizing that the prefill/decode imbalance becomes *catastrophic* at million-token context scales, and that the solution isn't a better GPU or a better NPU—it's a **heterogeneous system with clean stage boundaries**.

Prior NPU-PIM hybrids (NeuPIMs, IANUS) split by *operation* (attention vs. FFN), forcing continuous interleaving between devices. Hybe splits by *stage*, which:
1. Eliminates mid-inference data shuffling
2. Enables clean pipelining across requests
3. Lets each device run in its optimal regime continuously

**The Mechanism:** The NPU itself isn't revolutionary—it's a bandwidth-balanced GEMV engine with an output-stationary dataflow. The innovation is the **fine-grained KV transmission protocol** (Section 6.1) with its on-the-fly data reshaper. This solves the awkward problem that GPU and NPU have different memory layouts: Keys are stored head-wise for Q×K^T, Values are stored with stride intervals for Score×V (Figure 8). The reshaper handles this translation at wire speed during DMA.

**What's Actually New vs. Engineering:**
- **New:** The FGKVT protocol that overlaps KV transfer with attention computation
- **New:** The stage-wise overloading/offloading scheduler (Algorithm 1)
- **Engineering:** The NPU architecture itself (standard bandwidth-balanced design)
- **Engineering:** Bus mastering for GPU-NPU PCIe transfers

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real GPU Baseline:** They use actual NVIDIA H100 GPUs running vLLM (Section 7.1), not some strawman implementation. This is refreshing—many papers compare against unoptimized baselines.

2. **Equal Device Count Comparison:** Table 1 shows they compare 1 GPU + 5 NPUs against 6 GPUs (or 3×2 GPU sets for larger models). This is a fair apples-to-apples on hardware resources.

3. **Realistic Input/Output Ratios:** They use 127:1 input:output ratio based on Google Gemini 1.5 Pro's actual configuration (Section 7.2). They also test robustness across different ratios (Figure 13b).

4. **End-to-End Metrics:** They report both throughput (tokens/sec) and latency (TTFT, TPOT in Table 2), not just cherry-picked micro-benchmarks.

5. **RTL Implementation:** The NPU is synthesized in Samsung 4nm with actual power numbers (Figure 10), not just "estimated from synthesis."

### Weaknesses

1. **The Scalability Graph is Suspicious (Figure 16):** They show NPU achieves "near-perfect" 3.91× scaling with 5 devices, but only test up to 5 NPUs. The H100 comparison shows 1-3 devices with tensor parallelism struggling, but 6 devices with data parallelism should show linear scaling. The "N/A" entries for 4-5 GPUs are conveniently omitted.

2. **Latency Trade-off is Buried (Table 2):** Hybe's TTFT is 1.57× *worse* than the GPU baseline (424s vs 271s for Llama-3). The paper buries this in Section 8.3 and immediately pivots to talking about TPOT meeting SLO requirements. For interactive applications, waiting 7 minutes for the first token is unacceptable.

3. **The "Equal Device Count" Framing is Misleading:** Comparing 1 GPU + 5 NPUs to 6 GPUs by count ignores cost and area. An H100 SXM costs ~$25-30K; their NPU is 83.2 mm² in 4nm. They never discuss TCO, only "tokens/sec/W."

4. **Batching Analysis is Limited (Figure 13a):** They only test up to batch size 8, and admit Hybe loses advantage at batch ≥4. For datacenter deployments where batching is standard, this is a significant limitation buried in one paragraph.

5. **Group-Query Attention Reduces Gains (Section 8.2):** They note performance is higher for "models with multi-head attention (e.g., Phi) compared to models with group-query attention (e.g., Yi, Mistral, Llama)." Since *all modern LLMs* use GQA, their best results (10.5× on Phi) are for an architecture that's being deprecated.

6. **No Comparison to Other Hybrid Systems at Scale:** They compare utilization numbers with Splitwise/NeuPIMs/IANUS (Figure 15), but not end-to-end performance. The "NPU-PIM systems are expected to face further inefficiencies" claim (end of Section 8.2) is speculation, not measurement.

---

## Q4: What the Authors Didn't Tell You

### The TTFT Elephant in the Room
For Llama-3 with 1M context, Hybe takes **424 seconds** to produce the first token (Table 2). That's over 7 minutes of staring at a blank screen. The paper frames this as acceptable because "TTFT is solely based on prefill stage performance" (Section 8.3), but for any interactive use case, this is disqualifying. The 3.9× efficiency gain is irrelevant if users abandon the session.

### The NPU Can't Actually Handle Prefill
Section 6.2 describes "prefill offloading" where the NPU can help with prefill by "iterating the GEMV operation." But the NPU has 4 TFLOPS vs. H100's 1,979 TFLOPS—it's ~500× slower for compute-bound work. The offloading benefit (Algorithm 1) only kicks in for "deviations with significantly larger input tokens," which is precisely the regime where TTFT is already unacceptable.

### Memory Capacity Requirements are Enormous
The paper states NPUs need "enough memory capacity to support two requests with maximum context window" (Section 6.2). For Llama-3 at 1M tokens, that's 2×137.4GB = 275GB of HBM3 per NPU *just for KV cache*, plus model parameters. With 5 NPUs, you need 5×80GB = 400GB total HBM across NPUs. The paper hand-waves this by using "5 stacks of HBM3" per NPU (Figure 10) but never discusses the cost implications.

### The 10.5× Efficiency Claim is Cherry-Picked
The 10.5× efficiency gain on Phi-3 (Figure 11) comes from a model with:
- Multi-head attention (not GQA, which all modern models use)
- Smallest context window in the test set (131K)
- Smallest KV cache (51.5GB)

For Llama-3 with 1M context—the actual "million-token" claim in the title—the gain drops to 3.9×.

### No Discussion of Quantization Interaction
Section 8.4 states "quantization is not applied on model parameters and activations." But KV cache quantization (KVQuant, cited as [18]) is *the* dominant technique for long-context serving. How does Hybe interact with INT4/INT8 KV caches? If quantization reduces KV cache 4-8×, does the hybrid architecture still make sense?

### The Device Configuration Assumes Static Workloads
Section 4.2 derives the GPU:NPU ratio from input:output token ratio. But real serving has *dynamic* request patterns. The paper shows robustness testing (Figure 13b), but what happens when you have a sudden burst of short-context requests? The pre-configured 1:5 ratio becomes suboptimal, and there's no mechanism for dynamic reconfiguration.