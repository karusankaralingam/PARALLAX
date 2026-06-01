# Paper Deconstruction: Avant-Garde (ISCA '25)

## Q1: Whiteboard Explanation

Alright, let me sketch this out for you.

**The Problem They're Solving:**

Imagine you have a number format where instead of storing every number with its own full exponent (like regular floating point), you share one exponent across a *block* of numbers—say, 16 or 32 values. This is called a "scaled numeric format." Microsoft's MX formats and HBFP do this. The win? You save bits, which means more arithmetic density (operations per mm² of silicon).

But here's the catch: current NVIDIA GPUs (including H100) only natively support FP8, which has a single per-tensor scaling factor managed in software. If you want to use the fancier MX9 format—which has *two levels* of scaling factors (one for a block of 16, another for subsets of 2 within that block)—you're stuck doing the scaling arithmetic yourself on CUDA Cores before feeding data to Tensor Cores.

**What goes wrong?** Look at Figure 3 (page 5). For a single 16×16 MMA operation using scaled formats, you need:
- 4 global loads for scaling factors
- Multiple `mul` and `mad` instructions to apply those scaling factors
- This happens *per MMA operation*

Figure 4 quantifies the damage: **1.38× more registers** and **2.14× more instructions** compared to vanilla INT8.

**The Avant-Garde Solution:**

The core idea is elegantly simple: **flatten** multi-level scaled formats into single-level ones *in hardware*, once, as a preprocessing step. Then compute on the flattened representation using modified Tensor Cores.

Picture it like this:
1. **Before:** You have a hierarchical tree of scaling factors (L1 scale → L2 scale → element)
2. **Operand Transformer:** Hardware multiplies the L2 scales into the elements, collapsing to (L1 scale → scaled element)
3. **Flattened Tensor Core:** Now you just have one scaling factor per block—the Tensor Core computes the dot product on elements, adds the two input scaling factors (they're exponents, so addition = multiplication), and multiplies the combined scale into the result before accumulating.

The key insight (see Figure 5, page 6) is that regardless of block size, you can slice/coalesce blocks to match GPU warp size (32 threads). Small blocks get merged; big blocks get split.

**What's physically new?** (Figure 6-8, pages 6-8)
- **Operand Transformer:** 16 FP8/INT8 multipliers + 32 temp registers, sitting between operand read and execute stages
- **Modified Tensor Core:** An 8-bit adder for combining scaling factors + a "scaling unit" that applies the combined scale to the dot product output before accumulation

## Q2: The Key Insight

**The Real Innovation:**

The paper's core intellectual contribution is recognizing that **all scaled numeric formats—regardless of their hierarchy depth or block size—can be canonicalized to a single internal representation** that maps cleanly onto existing Tensor Core datapaths.

Specifically: scaling factors are exponents. Exponents add under multiplication. So if you "pre-bake" all lower-level scaling factors into the mantissa values (the flattening step), you're left with a single-level format where the Tensor Core just needs to:
1. Compute the integer/fixed-point dot product (already supported)
2. Add two 8-bit exponents (trivial)
3. Apply the combined exponent to the result (one multiplication)

This is clever because it decouples the *storage format* (however many scaling levels the algorithm designer wants) from the *compute format* (always single-level). The conversion is done once per operand load, not per MMA operation.

**Why this matters for the field:**

The OCP Microscaling (MX) spec [2] defines formats with varying block sizes and scaling hierarchies. Without hardware support, these remain academic curiosities—the software overhead makes them impractical. Avant-Garde provides a *general-purpose* mechanism that doesn't require new Tensor Core variants for each new format. You just need to teach the Operand Transformer the format's parameters (scaling levels, block size, bitwidths), and it handles the rest.

The analog to RISC-V's philosophy is apt here: rather than building specialized instructions for each format, they build a minimal hardware primitive (flatten + scaled-MMA) that composes well.

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Solid baseline choice (mostly):** They compare against H100 with software-based scaling factor management, which is the realistic deployment scenario today. The baseline isn't some strawman unoptimized code—they use the WMMA API with CUDA Core scaling adjustments (Section 4, page 10).

2. **Metrics that matter:** They report throughput (ops/cycle), execution time, instruction count, *and* energy consumption. Figure 12 (page 11) showing 52-66% instruction count reduction is compelling—it directly validates their claim about eliminating software overhead.

3. **Accuracy validation:** Table 4 (page 12) shows ViT-Base, BERT, and GPT-2 accuracy/perplexity with flattened MX9 vs. non-flattened MX9 vs. FP32. The deviation is ≤0.2%. This is critical—they used Microsoft's MX emulator [31] to verify functional correctness.

4. **Sensitivity analysis:** Section 5.6 (page 12) tests scaling from 2 to 4 levels and block sizes from 32 to 512. The <1% execution time impact from operand transformation is reassuring.

5. **Silicon overhead is reasonable:** Section 3.3 (page 9) reports 1.4% area and 1.2% power overhead relative to baseline SM. Synthesized with FreePDK 45nm, which is conservative.

**Weaknesses:**

1. **Simulation-only evaluation:** They use Accel-Sim [21], not real silicon. While Accel-Sim is validated against NVIDIA GPUs, it's still a model. The "74% throughput improvement" (Abstract) comes from simulation. Real systems have memory controller quirks, thermal throttling, and cache behavior that simulators approximate.

2. **No comparison to NVIDIA's own FP8 path:** They compare Avant-Garde with MX9/HBFP against the baseline with MX9/HBFP, but don't show: "What if you just used FP8 natively?" For inference, FP8 is *already* hardware-supported on H100. The question becomes: does MX9+Avant-Garde beat native FP8? Section 5.1 (page 10) notes HBFP and MXFP8 get "identical performance improvements"—but that's comparing software MX vs. Avant-Garde MX, not Avant-Garde MX vs. native FP8.

3. **No training workload evaluation:** The abstract claims applicability to "training and inference," but all experiments are inference-only. Section 3.2 mentions an "unflattening API" for training (page 9), but there's no training benchmark. Unflattening on CUDA Cores "introduces long latency" per their own admission—how long? What's the actual training overhead?

4. **Limited model diversity:** ViT (86M, 307M params), BERT (110M), GPT-2 Small (124M). These are small by 2025 standards—no billion-parameter models, no LLaMA, no Mixtral. The claim about "larger models require more frequent memory accesses" (page 10-11) hints that benefits might diminish at scale, but they don't actually test it.

5. **Microbenchmark dominates the "harmonic mean":** The microbenchmark (1M params, pure MMA) shows the largest gains (up to 3× for MX9 in Figure 10b). Including it in the harmonic mean inflates the reported "1.74× overall improvement." The DNN models alone show ~1.5-1.65× improvement (still good, but not 1.74×).

6. **Block size mismatch with production formats:** HBFP uses block size 64 (Table 2), but OCP MX formats specify block size 32. They evaluate HBFP64 and MX9 (block 16), but not the standard MXINT8 or MXFP4. The evaluation formats feel selected for convenience rather than comprehensiveness.

## Q4: What the Authors Didn't Tell You

**The Hidden Assumptions:**

1. **The "flattening is free" narrative is oversimplified.** They claim operand transformation is "less than 1% of total execution time" (Section 5.6, page 12). But this assumes weights are flattened *once* before inference. For streaming inference with dynamic batching (vLLM-style), each new batch of activations requires flattening. Section 3.2 (page 7-8) notes that for input data, "operand flattening is applied at the beginning of computation"—but in an LLM serving scenario with continuous batching, "the beginning" happens constantly.

2. **KV-cache implications are unaddressed.** For LLM inference, the KV-cache is the memory bottleneck. If you store KV-cache in a scaled format (for memory savings), you need to flatten it on every attention computation. They never discuss this use case—their experiments are single-pass inference (no autoregressive decoding). GPT-2 is in the benchmarks, but they don't mention whether they tested prefill vs. decode, or what happens to the ~44% execution time reduction when you're memory-bound during decode.

3. **The register file utilization paradox.** Section 3.1 (page 7) claims "non-GEMM operations do not significantly affect average register file utilization" because they're a small portion of the workload. But attention layers in transformers are not pure GEMM—softmax, LayerNorm, and activation functions are all non-GEMM. Do these operations dominate register pressure in practice?

4. **The unflattening cost is hand-waved.** For training, gradients need to go back to the original format. The unflattening API (Section 3.2, page 9) uses CUDA Cores and "introduces long latency." They never quantify this. If your training iteration is 60% forward pass and 40% backward pass, and unflattening adds 20% overhead to the backward pass, that's an 8% training slowdown they're not reporting.

5. **Memory bandwidth isn't the bottleneck here.** Their improvements come from reducing *compute* overhead (fewer instructions). But for large models, memory bandwidth is often the bottleneck, not compute. The flattened format doesn't reduce memory traffic—it still stores the same number of bits. The memory savings from scaled formats exist regardless of Avant-Garde; Avant-Garde just makes the compute side efficient.

6. **What about existing TensorRT-LLM or vLLM integration?** Production LLM serving systems have their own quantization pipelines (GPTQ, AWQ, SmoothQuant). The paper doesn't discuss how Avant-Garde's API would integrate with these ecosystems. Is the `flatten()` call compatible with CUDA Graphs? Does it work with async memory copies?

7. **The 45nm synthesis is ancient.** FreePDK 45nm is great for area/power *comparisons*, but H100 is built on TSMC 4N. The absolute area/power numbers are irrelevant; only the relative overhead (1.4% area) matters. More importantly, they don't discuss timing closure—can Avant-Garde's Tensor Core modifications meet H100's clock targets?

**The Bottom Line:**

Avant-Garde is a clean architectural idea: canonicalize scaled formats to a single internal representation, add minimal hardware support, and eliminate software overhead. The 1.5-1.9× throughput gains are real for the workloads tested. But the paper is conspicuously silent on autoregressive LLM inference, training overhead, and integration with production serving stacks. If you're evaluating this for actual deployment, you'd want answers to those questions first.