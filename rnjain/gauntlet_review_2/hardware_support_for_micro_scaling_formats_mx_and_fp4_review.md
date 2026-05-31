# Deconstruction of "Avant-Garde: Empowering GPUs with Scaled Numeric Formats"

## The "No-BS" Summary

This paper addresses a real problem: modern GPUs only natively support FP8 with per-tensor scaling, but the industry is moving toward more sophisticated scaled numeric formats like MX (Microscaling) that use **multi-level, block-wise scaling factors**. When you try to run MX9 (a two-level format with 16-element blocks and 2-element subsets) on current GPUs, you're forced to implement the scaling factor application in software using CUDA cores, which balloons your instruction count by 2.14× and register usage by 1.38× compared to INT8.

Avant-Garde's solution: **flatten** multi-level scaled formats into a single-level representation in dedicated hardware (the "Operand Transformer"), then compute on that flattened representation using modified Tensor Cores that can apply a shared scaling factor to dot-product results before accumulation. The claimed benefit is 74% higher throughput and 44% lower inference time on transformer workloads, with negligible accuracy loss (<0.2%).

---

## The Core Mechanism: A Whiteboard Explanation

### What's a Scaled Numeric Format?

Imagine you have 32 tiny numbers, each stored in only 4-7 bits. That's not enough dynamic range to represent both 0.001 and 1000.0. The trick: store one shared "scaling factor" (essentially an exponent) that applies to all 32 numbers. Now each number is implicitly `element × 2^(scaling_factor)`. This is **Block Floating Point (BFP)** at its core.

**Single-level** (like HBFP or MXFP8): One scaling factor per block of 32-64 elements. Simple.

**Multi-level** (like MX9): A block of 16 elements shares a first-level scaling factor, but *within* that block, every 2 elements share a second-level "micro-exponent." It's scaling factors all the way down—a hierarchy.

### The Problem on Current GPUs

Current Tensor Cores do: `C = A × B + C` where A, B, C are matrices of standard types (FP16, INT8, FP8).

They do **not** do: "After computing the dot product of A's row and B's column, multiply by the combined scaling factor of A's block and B's block, *then* accumulate."

So if you want MX9, you must:
1. Load the scaling factors separately.
2. Use CUDA cores to multiply each partial result by the appropriate scaling factor.
3. Then accumulate.

This is death by a thousand instructions. Every MMA operation spawns a tail of `mul` and `mad` instructions.

### Avant-Garde's "Flattening" Trick

The insight: **You can pre-apply all the lower-level scaling factors to the elements, leaving only the top-level scaling factor to be handled during computation.**

For MX9:
- Original: 16 elements, 1 first-level scale, 8 second-level scales (one per pair).
- Flattened: 16 elements (each now absorbs its second-level scale), 1 first-level scale.

This is done once, in hardware, by the **Operand Transformer**—a pipeline stage with 16 FP8/INT8 multipliers that iteratively applies scaling factors. The result is a "flattened block" that looks like a simple single-level BFP format.

### The Modified Tensor Core

Avant-Garde's Tensor Core adds:
1. An **8-bit fixed-point adder** to combine the scaling factors of the two input operands (since scales are exponents, combining them is addition).
2. A **scaling unit** that multiplies the dot-product result by `2^(combined_scale)` before it hits the accumulator.

This is elegant because it's a small addition to the existing datapath—you're just inserting a shifter/multiplier after the dot-product reduction tree.

### Data Layout and API

The API (`flatten()` function, extended WMMA) lets programmers:
1. Flatten weights once before inference (they're static).
2. Flatten activations once per layer (they flow through).
3. Call `FMMA` (Flattened MMA) instructions that operate on flattened blocks.

The flattened format is stored contiguously in memory and registers. The block size is fixed at 32 elements (warp size) for alignment with GPU execution.

---

## The Critique: Strengths & Weaknesses

### Why It Got In (Strengths)

1. **Addresses a Real, Timely Problem:** The MX format is an OCP standard backed by Microsoft, AMD, and NVIDIA. The paper correctly identifies that current GPUs are caught flat-footed—they support FP8 but not the more sophisticated MX variants. This is a genuine gap.

2. **Clean Architectural Insight:** The "flatten once, compute many times" strategy is simple and effective. It's the kind of idea that makes you say "why didn't I think of that?"—the hallmark of good systems work.

3. **Reasonable Overhead:** 1.4% area and 1.2% power overhead for the Operand Transformer and modified Tensor Core is modest. They're not proposing a wholesale redesign.

4. **Functional Accuracy Validation:** They actually ran ViT-Base, BERT, and GPT-2 through a modified Microsoft MX emulator and showed <0.2% accuracy deviation. This is more than many papers do.

5. **Solid Baseline Comparison:** They compare against a software implementation of MX on the same GPU (H100), not against a strawman. The 2.14× instruction overhead they measure is believable.

### Where It's Weak (The Skeleton in the Closet)

1. **Simulation-Only Evaluation:** This is Accel-Sim, not silicon. They synthesized the Operand Transformer in FreePDK 45nm (!) to get area/power numbers, but that's a far cry from a 4nm FinFET tape-out. The 1.4% area overhead claim is hand-wavy at best. Real Tensor Core integration would require careful timing closure and verification.

2. **No Training Results:** The paper focuses entirely on inference. They mention "unflattening" for training (to write gradients back in the original format), but there's no training convergence data. Training is where scaled formats get tricky—gradient accumulation, loss scaling, and optimizer state all interact with the format. This is a significant omission for a paper claiming to "empower GPUs" for DNN workloads.

3. **Block Size Sensitivity Glossed Over:** They claim <1% performance variation when block size changes from 32 to 512, but this is buried in Section 5.6 with no graph. What happens when block size is 8 or 4? Outlier-aware quantization methods (like those in MicroScopiQ [39]) often need smaller blocks to handle activation outliers. The paper doesn't address this.

4. **Scale Factor Bandwidth Not Analyzed:** For HBFP with block size 64, you have 1 scale factor per 64 elements—low overhead. For MX9 with block size 16, you have 1 scale factor per 16 elements. As block size shrinks, scale factor storage and bandwidth become non-trivial. The paper doesn't quantify this. What's the memory traffic increase for scale factors in a real transformer attention layer?

5. **Comparison Against Optimized FP8 Baseline:** The paper compares MX9 on Avant-Garde against MX9 on baseline (software). But the real question is: **Does MX9 on Avant-Garde beat well-optimized FP8 on the same baseline?** FP8 is natively supported on H100. If FP8 with per-tensor scaling is "good enough" for most workloads, the value proposition of Avant-Garde diminishes. They show MX9 accuracy is comparable to FP32, but they don't show it's *better* than FP8.

6. **Limited Model Diversity:** ViT-Base, ViT-Large, BERT, GPT-2. No CNNs (where depthwise convolutions have different scaling characteristics), no mixture-of-experts models, no diffusion models. The transformer-heavy benchmark suite is convenient but not comprehensive.

7. **Flattening Latency Hidden by Warp Interleaving:** They claim flattening latency is "often hidden" by interleaved warp execution. This is plausible for large batch sizes, but what about batch-1 inference (the latency-sensitive case)? No data provided.

---

## Discussion Questions for the Student

1. **The Bandwidth Question:** The paper assumes scale factors are "stored side by side" with elements in memory. For a transformer with 12 attention heads and 768-dimensional embeddings, calculate the memory traffic overhead of scale factors for MX9 (block size 16) versus MXFP8 (block size 32) versus FP8 (per-tensor scaling). At what point does scale factor bandwidth become a bottleneck?

2. **The Training Gap:** The paper punts on training. If you were to extend Avant-Garde to support training, what additional hardware would you need? Consider: (a) gradient accumulation in flattened format, (b) loss scaling interaction with block scaling, (c) optimizer state (Adam's first and second moments) in scaled formats. Would you need to "unflatten" after every backward pass, or could you keep gradients in flattened format?

3. **The FP8 Counterfactual:** NVIDIA's H100 already supports FP8 with per-tensor scaling. Recent work (e.g., FP8-LM from Microsoft) shows FP8 can train GPT-3 scale models with minimal accuracy loss. Under what conditions would MX9 on Avant-Garde provide a meaningful advantage over FP8 on stock H100? Is the 74% throughput improvement over *software MX9* the right comparison, or should they be comparing against *native FP8*?

---

## Contextual Fit: Where Does This Sit in the Literature?

This paper is a direct response to the OCP MX specification [2] and Microsoft's shared microexponent work [4, 42]. It's saying: "You've defined these formats, but nobody can run them efficiently on GPUs. Here's how."

It builds on the Block Floating Point lineage:
- **HBFP (Drumond et al., 2018)** [9]: Showed BFP can train DNNs. Single-level, large blocks.
- **MSFP (Rouhani et al., 2020)** [41]: Shared exponents for inference on FPGAs.
- **MX (Rouhani et al., 2023)** [42]: Multi-level scaling, OCP standardization.

It's also related to:
- **FAST (Zhang et al., 2022)** [50]: Variable precision BFP with stochastic rounding.
- **Bucket Getter (Lo & Liu, 2023)** [29]: Addresses BFP accumulation overhead.

The key differentiator: Avant-Garde targets **GPUs** (not FPGAs or custom accelerators) and handles **multi-level** formats (not just single-level BFP). The "flattening" insight is the novel contribution—prior work either assumed single-level formats or required custom datapaths.

---

## Final Verdict

This is a solid ISCA paper that identifies a real problem (MX format support on GPUs), proposes a clean solution (flatten + modified Tensor Core), and provides reasonable (if simulation-only) evaluation. The 74% throughput improvement is impressive, but the comparison is against a software baseline, not native FP8. The lack of training results and the glossed-over block size sensitivity are weaknesses.

**What the student should take away:** This paper is a good example of "architectural judo"—using a small hardware addition (Operand Transformer, modified Tensor Core) to unlock a large software benefit. But always ask: *What's the right baseline?* and *What workloads are missing?*