## Q1: Whiteboard Explanation

Let me walk you through how Focus actually works at the hardware level.

**The Problem Setup:**
Vision-Language Models (VLMs) process video by tokenizing frames into patches, creating thousands of visual tokens (e.g., 6,272 visual tokens vs. 109 text tokens per sample in their benchmarks). The LLM backbone dominates compute (99% of operations). The key observation is that adjacent video frames share massive redundancy—similar backgrounds, slowly moving objects.

**The Three-Level Compression Pipeline:**

**Level 1 - Semantic Concentrator (SEC):** Operates during attention computation. When computing Softmax(QK^T), Focus extracts the Text-to-Image attention block (T×M matrix). For each image token j, it computes importance as: `s_j = max over all heads and text tokens of I_{i,j}`. This produces a 1×M importance vector. A pipelined bubble sorter (built by chaining the same max units used for importance scoring) identifies top-k tokens in M·k/a cycles. The clever bit: this sorting overlaps with Q^(image)K^T computation, which takes M·(M+T)·h·n/(a·b) cycles—since h·n ≈ 3584 and b = 32, sorting finishes well before GEMM completes (Figure 5).

**Level 2 - Block-wise Similarity (where comparisons happen):** Tokens are reorganized into a 2×2×2 spatiotemporal block structure via a "convolution-style layouter." Each block spans 2 frames × 2 height × 2 width = 8 vectors. The key token (highest index) is compared against the other 7 tokens in its block. This is essentially a 3D sliding window sweep across the video (Figure 1b, Figure 6).

**Level 3 - Vector-wise Similarity (how fine the comparisons are):** Instead of comparing full 3584-dimensional tokens, Focus divides each token into 32-dimensional vectors. Cosine similarity is computed as:
```
(p·q) / (||p|| · ||q||)
```
The L2-norms are precomputed and buffered. If similarity > 0.9, vectors are merged. This 32-dimensional granularity is key—Figure 2(b) shows 64% of 8-vectors exceed 0.9 similarity vs. only 18% of full 3584-vectors.

**The Gather-Scatter Mechanism (Figure 6 and 8):**
- **Gather:** After GEMM produces an m×n output tile (1024×32), unique vectors are stored in a concentrated buffer; redundant vectors get mapped to their representative's index via a "Similarity Map"
- **Scatter:** During the next GEMM (operating on concentrated input), partial sums are replicated and redistributed to original token indices using the similarity map, then accumulated in a 2a-wide accumulator (64 units) to match throughput

**Memory Layout Trick (Figure 7):**
To avoid bank conflicts during parallel 2×2×2 block reads, tokens are mapped using:
```
Bank = f mod 2 × 4 + r mod 2 × 2 + c mod 2
Offset = floor(r/2) × ceil(W/2) + floor(c/2)
```
This guarantees all 8 vectors in any block reside in distinct banks—no data replication needed (unlike traditional CNN accelerators that duplicate up to 8×).

---

## Q2: The Key Insight

**The Core Trick:** Focus exploits the observation that **vector-level granularity (32 dimensions) reveals far more redundancy than token-level granularity (3584 dimensions)**, and this granularity happens to align perfectly with GEMM tile dimensions (m=1024, n=32) and systolic array dimensions (a=b=32).

This is the unifying insight that makes the whole architecture work:

1. **Algorithmic insight:** Figure 2(b) shows 64% of 8-dimensional vectors exceed 0.9 similarity, vs. 18% for full tokens. By operating at vector granularity, they achieve 82.8% sparsity vs. CMC's 58.6% at similar accuracy (Table II).

2. **Hardware alignment:** The vector size (32) equals the PE array width (a=n=32), equals the GEMM tile width (n=32), equals the number of output vectors streamed per cycle. This isn't a coincidence—it means similarity detection happens *in-place* within a single GEMM tile, with no off-chip access required.

3. **Streaming execution:** Because compression is tile-local, each 1024×32 output tile can be compressed immediately after generation. The similarity matcher needs at most 8×m cycles per tile, while GEMM needs K/b × m = 112×m cycles (for K=3584, b=32). Matching is never on the critical path unless K < 256.

**Why this differs from prior work:**
- CMC offloads compression to an external video codec *after* writing full tokens to DRAM—incurring high bandwidth (Figure 3a shows 79% of dense DRAM traffic despite 46% sparsity)
- AdapTiV does token-level merging, missing sub-token redundancy
- Focus compresses *before* DRAM writeback, at vector granularity, achieving 81% sparsity with only 21% DRAM traffic (Section VII-F)

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive comparison methodology:** The authors implement all baselines (AdapTiV, CMC, FrameFusion) in the same framework, synthesize all designs in the same 28nm TSMC process at 500MHz, and use identical PE arrays (32×32), buffer sizes (~730KB), and DRAM bandwidth (64GB/s) (Table III). This is unusually rigorous for an architecture paper.

2. **End-to-end accuracy validation:** They evaluate on 3 VLMs (Llava-Video, Llava-OneVision, MiniCPM) across 6 datasets (VideoMME, MLVU, MVBench, VQAv2, MME, MMBench), showing only 1.2% average accuracy degradation at 80% sparsity (Table II). This isn't cherry-picked—they report per-model, per-dataset numbers.

3. **RTL implementation with post-synthesis numbers:** Area (3.21mm²) and power (736mW) are from Synopsys Design Compiler synthesis at 28nm, with DRAM energy from DRAMsim3 (Section VII-A). The Focus unit adds only 2.7% area and 0.9% power overhead.

4. **Design space exploration:** Figure 10 systematically varies tile size, vector size, block size, and accumulator count, identifying sweet spots with clear tradeoffs explained.

**Weaknesses:**

1. **Systolic array baseline is artificially weak:** The "vanilla systolic array" processes dense inputs with no sparsity exploitation. A fairer comparison would be against a systolic array with standard weight sparsity or structured pruning. The 4.47× speedup over this baseline inflates the gains.

2. **Memory bandwidth assumptions favor Focus:** The 64GB/s DDR4 assumption (Table I) is reasonable for edge deployment but doesn't reflect data center scenarios with HBM (2-3TB/s). Focus's advantage from DRAM traffic reduction (Figure 12a) would diminish significantly with HBM.

3. **Semantic pruning ratios are manually tuned:** Table I shows retention ratios (40%/30%/20%/15%/10% at layers 3/6/9/18/26) were "searched" and selected for "best sparsity-accuracy tradeoff." This is dataset-dependent hyperparameter tuning, not adaptive behavior. They acknowledge this limitation in Section VII-D.

4. **Worst-case latency not fully characterized:** Figure 13 shows 92.2% average utilization, but the histogram reveals a tail of tiles with >800 vectors (near-zero compression). In real-time video processing, tail latency matters more than average.

5. **Limited quantization synergy analysis:** Table IV shows INT8 quantization with Focus loses 0.5% accuracy (vs. 0.02% for dense model). This suggests some tension between compression techniques, but the analysis is superficial.

---

## Q4: What the Authors Didn't Tell You

**Hidden Hardware Costs:**

1. **The 25KB importance buffer (Section V-A):** Storing the importance vector (1×M, FP16) for M=6272 tokens requires 12.5KB minimum. The 25KB allocation implies they're storing more (possibly multiple heads or intermediate results). This buffer is *additional* to the 734KB on-chip memory budget in Table I.

2. **The convolution-style layouter overhead:** Figure 7 shows 8 memory banks with complex addressing logic. The address computation `Bank = f mod 2 × 4 + r mod 2 × 2 + c mod 2` requires integer division/modulo operations for every token. They claim "no data replication" but don't report the logic overhead or latency of this reordering.

3. **The 2a-wide accumulator (Section VI-C):** To maintain throughput during scatter, they need 64 accumulators operating in parallel. This isn't reflected in the area breakdown (Figure 9c) which only shows SEC (1.9%) and SIC (0.8%). Where does the accumulator area go?

**Glossed-Over Latency Issues:**

4. **The bubble sorter is O(M·k):** Section V-B claims sorting completes in M·k/a cycles and overlaps with GEMM. But with M=6272 tokens and k=2500 (40% retention), that's 6272×2500/32 ≈ 490K cycles just for the first layer. They claim this is "well before Q^(i)K^T finishes"—but only because attention matrices are huge. For smaller models or batch sizes, this could become a bottleneck.

5. **The similarity threshold (0.9) is hardcoded:** Section VI-A mentions cosine similarity > 0.9 triggers merging. But Figure 2(b) shows the similarity distribution varies across layers. A fixed threshold means over-pruning in some layers and under-pruning in others. There's no adaptive mechanism.

**Missing System-Level Considerations:**

6. **No discussion of scheduling complexity:** Focus operates in attention layers (SEC) and FC layers (SIC) with different compression patterns. The controller must track which tokens were pruned, maintain offset encodings across layers, and synchronize gather/scatter operations. The paper treats this as trivial ("lightweight registers") but the scheduling complexity is non-trivial.

7. **Frame pairing assumption:** The 2×2×2 block structure assumes processing pairs of adjacent frames. What happens at video boundaries, scene cuts, or when frames arrive asynchronously? The paper doesn't address streaming video scenarios with variable inter-frame gaps.

8. **The "generalization" claims are weak:** Table V shows image VLM results, but speedups drop significantly (e.g., Qwen2.5-VL on VQAv2: 1.91× vs. 4.44× for Llava-OneVision). Without temporal redundancy, Focus's advantage diminishes substantially. The claim that "Focus effectively removes redundancy beyond the video domain" is oversold.