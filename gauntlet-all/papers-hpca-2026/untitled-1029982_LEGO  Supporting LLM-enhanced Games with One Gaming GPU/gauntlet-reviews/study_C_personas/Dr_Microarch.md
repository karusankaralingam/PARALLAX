# LEGO: Supporting LLM-enhanced Games with One Gaming GPU

## Q1: Whiteboard Explanation

Let me draw the core architecture of LEGO on the whiteboard.

**The Problem Setup:**
Imagine a timeline where a game like Black Myth: Wukong renders frames every 16.6ms (60 FPS), but the GPU is only busy ~61% of that time. Meanwhile, an LLM needs to generate combat actions—say, every 600ms for a 100 APM player, or every 200ms for a 300 APM professional.

The challenge: **Can we shove LLM inference into those GPU idle gaps?**

The answer is "almost, but not quite." Figure 4 (page 3) shows the brutal arithmetic: BlackMyth uses 60.8% of GPU time, but Llama3-8B at 100 APM needs 41.9%—that's 102.7% total. At 300 APM, the deficit gets worse.

**LEGO's Two-Part Solution:**

**Part 1: The Layer-Skipping Adaptor (Algorithm Side)**

Think of a 32-layer transformer as a pipeline. LEGO observes (Figure 8, page 6) that later layers have *high cosine similarity* between their inputs and outputs—meaning they're doing less "work" than early layers. 

The trick: Skip a contiguous block of these redundant later layers (e.g., layers 25-29 for 4-layer skip), and replace them with a single FFN "adaptor" layer trained to approximate what those skipped layers would have computed. This is *self-distillation*—the adaptor learns to mimic the transformation `T_k → T_{k+n}`.

The key insight from the heatmap (Figure 8): Don't skip the *final* layer (it interfaces with the output head), and skip *contiguous* blocks in the high-similarity region.

**Part 2: The Headroom-Maximizing Scheduler (System Side)**

Here's where it gets architecturally interesting. The scheduler must exploit two types of headroom:

1. **Inter-rendering headroom**: The gap *between* frames (e.g., if rendering takes 10ms, you have 6.6ms before the next frame starts).
2. **Intra-rendering headroom**: GPU idle time *within* a frame—game engines batch rendering subtasks, leaving gaps (average 0.24ms, up to 3.1ms per frame according to Section V-C).

The scheduler uses a simple **linear regression model** that predicts total headroom over the *entire LLM inference window* (e.g., 36 frames for 100 APM). It doesn't try to predict per-frame headroom—that's too noisy. Instead, it predicts aggregate headroom, selects the appropriate adaptor/layer-skip configuration, then dispatches inference subtasks opportunistically:

- **Fine-grained subtasks** (single transformer layer, ~0.4ms) for intra-rendering gaps.
- **Coarse-grained subtasks** (multiple layers) for inter-rendering gaps.

The enforcement condition (Section V-C): `ΣT_subtasks ≤ T_minimal`, where `T_minimal` is the smallest inter-rendering gap observed for that game. This guarantees no frame deadline violations.

---

## Q2: The Key Insight

**The Paper's "Magic Trick":**

The core architectural insight is that **resource-driven layer skipping can be made accuracy-preserving through targeted knowledge distillation, because later transformer layers are informationally redundant with respect to their immediate predecessors.**

Let me unpack this:

1. **Observation (Figure 8):** Cosine similarity between layer outputs increases dramatically in the later half of the network. Layers 25-31 in Llama3-8B produce outputs that are 0.8+ similar to their inputs. This is *not* the same as saying the layers are useless—they refine representations—but the *delta* they contribute is small and approximable.

2. **Exploitation:** Instead of using runtime confidence thresholds (like LITE's early-exit approach, which causes 47.1% of inferences to violate SLOs per Figure 5), LEGO makes the skip decision *before* inference based on predicted resource availability. This inverts the causality: resources determine skip count, not token difficulty.

3. **The Adaptor as a Compression Trick:** The FFN adaptor is trained via MSE loss to approximate `f_{k+n} = FFN(f_k)`. This is a form of *layer collapse*—compressing N transformer layers into 1 FFN. The adaptor is lightweight (268.8 MB per adaptor, Section VII-K), and crucially, *static*—no runtime branching or confidence computation.

**Why This Matters Architecturally:**

Prior layer-skipping methods (CALM, LITE) are *token-adaptive*—they skip different layers for different tokens based on runtime confidence. This introduces variance in execution time, which is catastrophic for real-time scheduling. LEGO's approach is *resource-adaptive*—the skip pattern is fixed per inference window, making execution time deterministic and schedulable.

The "magic" is recognizing that the similarity heatmap gives you a principled way to choose *which* layers to skip (highest similarity contiguous block), and distillation gives you a way to recover *some* of the lost information without runtime overhead.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive End-to-End Evaluation (Section VII-B):**
Figure 12 demonstrates 99th-percentile FPS and APM across 18 configurations (3 games × 2 LLMs × 3 APM levels). LEGO maintains both targets while SmallModel fails at 300 APM (26.2% FPS drop) and LayerSkip fails at 200/300 APM (28.6% APM drop). This is the right metric—tail latency matters for real-time systems.

**2. Ablation via Real Gaming (Section VII-D):**
The Street Fighter III tournament (Figure 13) is clever—it demonstrates that accuracy metrics (MMLU, ARC-C) translate to actual gameplay performance. LEGO-4 beats LITE-4 with 85% win rate, and LEGO-12 matches Llama3-3B despite having equivalent compute cost.

**3. Honest Headroom Accounting (Figure 15):**
The authors explicitly measure headroom utilization, showing LEGO captures 28.6% more headroom than SmallModel at 200 APM. This validates the intra-rendering discovery (Section V-A) rather than just claiming it works.

**4. Prediction Model Validation (Table II):**
The LR model achieves 0.6% average prediction error using inference-window-granularity rather than per-frame granularity. This is a key architectural decision—aggregating over 12-36 frames smooths variance.

### Weaknesses

**1. The Similarity Heuristic is Empirically Justified, Not Principled:**
The claim that high cosine similarity implies "reduced contribution of unique knowledge" (Section IV-B) is a heuristic. The heatmaps (Figure 8) show correlation, not causation. Why doesn't similarity between layers 1 and 5 (lower left of heatmap, similarity ~0.4) hurt more than between layers 25 and 29? The paper doesn't provide a theoretical grounding for why *contiguous* skipping outperforms *distributed* skipping—only an experimental claim (Section VIII).

**2. Adaptor Training Cost is Buried:**
Section IV-C states "up to 14 LLM adaptors are required, and the total training time is approximately 36 hours." For a game company shipping a product, this means:
- Pre-computing adaptors for every (game, LLM, APM-range) combination.
- 3.23 GB additional storage for 12 adaptors per LLM.
- Retraining if the LLM is updated.

The paper positions this as "negligible offline overhead," but it's actually a significant deployment constraint.

**3. Memory Bandwidth Analysis is Missing:**
LLM inference on gaming GPUs is often *memory-bound* during decode (loading KV cache and weights). The paper profiles compute time (Figure 3, Table II) but doesn't analyze whether the fragmented scheduling increases memory bandwidth pressure. Interleaving rendering and LLM inference could cause cache thrashing in shared L2.

**4. The 300 APM Scenario is Marginal:**
Table IV shows LEGO-13 (skip 13 layers) achieves 63.9% on MMLU—worse than Llama3-3B's 58.2% baseline. In Section VII-D, LEGO-12 has a 47.5% win rate against Llama3-3B. At 300 APM, LEGO is essentially trading accuracy for feasibility, and the "up to 86.3% accuracy loss reduction" claim (abstract) applies only to moderate skip counts.

**5. Variable-Length Prompt Handling is Incomplete:**
Section V-D mentions adding a "duration predictor" for variable-length prompts but doesn't evaluate it. Figure 14 shows fixed-range sampling [256, 1024], not truly variable prompts. The re-prediction after first-token generation (Section V-D) adds scheduling overhead that isn't quantified.

---

## Q4: What the Authors Didn't Tell You

**1. The "Intra-Rendering Headroom" is Game-Engine-Specific:**
The observation that rendering subtasks leave GPU gaps (Section V-A, "average 0.24ms, 90% < 0.73ms") depends entirely on how Unreal Engine 4 batches draw calls. Different engines (Unity, custom engines) or different rendering pipelines (forward vs. deferred, ray tracing) will have completely different gap distributions. The paper evaluates only UE4 games—generalization is unproven.

**2. The Scheduling Relies on Polling, Not Interrupts:**
Section VI describes monitoring "rendering task state variables" and launching inference subtasks "upon rendering completion." This is a polling-based approach that consumes CPU cycles and adds latency. The paper doesn't disclose polling frequency or CPU overhead. True zero-overhead scheduling would require GPU-side preemption, which consumer GPUs don't expose.

**3. The LR Model is Surprisingly Naive:**
Using 3 input windows to predict 1 output window via linear regression (Section V-B) is a simple moving-average-style predictor. The 0.6% average error is impressive, but the paper doesn't explain *why* this works—presumably rendering workload is autocorrelated over 36-frame windows. What happens during scene transitions, cutscenes, or loading screens?

**4. KV Cache Management During Layer Skipping is Unaddressed:**
When you skip layers 25-29, what happens to the KV cache for those layers? The paper mentions LITE uses "KV replication to fill the KV cache of skipped layers" (Section VII-C), causing accuracy loss. LEGO presumably doesn't need KV cache for skipped layers since the adaptor is a single FFN, but this is never explicitly stated. If true, this saves ~40% KV cache memory—a significant unreported benefit.

**5. The Comparison to NVIDIA ACE is Misleading:**
Section VII-E claims LEGO "outperforms NVIDIA ACE" because INT4-Nemotron3-4B loses to FP16-Llama3-8B. But ACE is designed for NPCs and dialogue, not combat APM scenarios. The comparison is apples-to-oranges—ACE optimizes for on-device deployment at low precision, while LEGO optimizes for headroom utilization at higher precision.

**6. The 60 FPS Assumption is Critical but Unstated:**
The entire system is designed around 16.6ms frame deadlines (60 FPS). Modern games increasingly target 120 FPS (8.3ms deadline) or use variable refresh rate. At 120 FPS, inter-rendering headroom shrinks proportionally, and intra-rendering gaps become harder to exploit. The paper doesn't discuss scalability to higher frame rates.

**7. The Hardware Tax:**
Each adaptor is an FFN with dimensions matching the transformer hidden size (4096 for Llama3-8B). At 268.8 MB per adaptor, and up to 14 adaptors needed for BlackMyth (Section IV-C), the total adaptor storage (3.76 GB) approaches the size of a quantized small model. The paper doesn't compare against simply shipping Llama3-3B + quantization, which might achieve similar accuracy with less complexity.