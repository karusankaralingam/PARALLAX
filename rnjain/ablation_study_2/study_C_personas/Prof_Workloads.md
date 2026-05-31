# Evaluation Critique: Avant-Garde

## Q1: Whiteboard Explanation

Let me explain this paper as if I'm drawing on a whiteboard.

**The Problem:**
Modern DNNs use "scaled numeric formats" like MX9 and HBFP to save memory and computation. These formats group numbers into blocks that share scaling factors—think of it like scientific notation where a group of mantissas share one exponent.

*[Drawing a block of 16 numbers with one shared 8-bit scaling factor]*

The issue: Current GPUs (like H100) only natively support FP8. For other scaled formats, you need software to:
1. Load the scaling factors
2. Apply them to each element
3. Do the actual computation
4. Scale the results back

*[Drawing Figure 3: showing ~20 instructions for one MMA operation]*

This creates massive overhead—2.14× more instructions and 1.38× more register usage compared to INT8 (Figure 4).

**The Solution - "Flattening":**
Avant-Garde's key trick: Convert any multi-level scaled format into a single-level format in hardware, then compute on that.

*[Drawing Figure 5: Multi-level format → Flatten → Single-level format]*

For a 2-level format like MX9:
- Level 1: 16 elements share an 8-bit scaling factor
- Level 2: Each pair of elements shares a 1-bit scaling factor

Flattening multiplies the Level-2 scaling factors into the elements, leaving only Level-1 scaling factors. This happens in a new "Operand Transformer" hardware unit (Figure 7).

**The New Pipeline:**
*[Drawing Figure 6: Fetch → Decode → Issue → Read → **Operand Transform** → Execute → WB]*

The Operand Transformer has 16 FP8/INT8 multipliers that flatten operands. The modified Tensor Core (Figure 8) has an 8-bit adder for combining scaling factors and a "scaling unit" that multiplies results by the combined factor before accumulation.

**Result:** 74% higher throughput, 44% lower execution time, ~1.4% area overhead.

## Q2: The Key Insight

The fundamental insight is deceptively simple: **All scaled numeric formats, regardless of their hierarchical depth, can be "collapsed" into a single-level representation at the microarchitecture level, and this flattened representation should become the canonical internal format for computation.**

This is elegant because it decouples the *storage format* (which can be arbitrarily complex for compression) from the *compute format* (which is uniform and hardware-friendly). 

The paper makes a crucial observation in Section 3: "Both single-level and multi-level scaled numeric formats follow a common flattening process." For multi-level formats, you recursively apply scaling factors one level at a time until you reach a single-level representation. This flattened block then stays in this format throughout the workload's execution—you flatten once, compute many times.

The second insight is about alignment with GPU execution: They set the flattened block size to match the warp size (32 threads/128 bytes). This isn't arbitrary—it ensures that the SIMT execution model isn't fighting against the scaled format's block boundaries. Smaller blocks get coalesced; larger blocks get sliced (Figure 5).

What makes this work is that the "flattening overhead" is amortized:
- For weights: Flatten once before inference
- For inputs: Flatten at load time
- For activations: Computed in flattened format, stay flattened

The operand transformation accounts for <1% of execution time (Section 5.6) because it's a preprocessing step, not a per-operation step.

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Real Hardware Baseline (H100)**
The authors model NVIDIA H100 with Accel-Sim (Table 1), which is the current state-of-the-art GPU for ML workloads. This is commendable—they're not comparing against an artificially weak GPU.

**2. Multiple Scaled Formats Tested**
They evaluate three distinct formats (Table 2):
- HBFP: Single-level, 64 block size, 8-bit elements
- MX9: Two-level (16→2), 8-bit elements
- MXFP8: Single-level, 32 block size, FP8 elements

This shows the architecture generalizes across format types.

**3. Accuracy Validation (Table 4)**
They actually verify that flattening doesn't destroy model accuracy. ViT-Base achieves identical 80.3% accuracy across FP32, flattened MX9, and non-flattened MX9. BERT and GPT-2 perplexity differs by only 0.01-0.02. This is critical for a numerical format paper.

**4. Instruction Count Analysis (Figure 12)**
They show the mechanism: 52.2% reduction for single-level formats, 65.7% for two-level formats. This explains *why* the throughput improves—fewer instructions to issue and execute.

### Weaknesses

**1. The "Cherry-Pick" Problem: Benchmark Selection**

Look at Table 3. The benchmarks are:
- Microbenchmark (1M parameters)
- ViT-Base (86M), ViT-Large (307M)
- BERT (110M)
- GPT-2 (124M)

Where are the actually large models? GPT-3 has 175B parameters (mentioned in Section 1!), LLaMA-70B, Mixtral-8x7B? The evaluation caps out at 307M parameters. The authors even note in Section 5.1: "The throughput improvement of Avant-Garde slightly decreases as model size increases." 

For ViT-Base vs ViT-Large, there's already a 6% degradation in improvement. What happens at 10B or 100B parameters? This trend is concerning and unexplored.

**2. Baseline Implementation Quality**

The baseline for scaled formats is described in Section 4: "we implement a DNN model that handles the scaling factor in software to support the scaled numeric formats."

This is a *custom implementation* by the authors. Is it optimized? Did they use the best available software techniques? Section 2.2 references prior work [15, 16] on software stacks for MX formats, but they don't compare against these existing implementations. Their baseline could be a strawman.

**3. Missing Memory Bandwidth Analysis**

The paper focuses heavily on compute (instruction count, throughput) but barely addresses memory. Section 5.1 mentions: "Larger models require more frequent memory accesses... the operand flattening mechanism may introduce additional accesses for scaling factors."

Where's the quantification? For memory-bound workloads (which most large LLM inference is), this could be a killer. They never show L1/L2 hit rates, memory bandwidth utilization, or how the flattened format affects data movement.

**4. Simulation-Only Validation**

All results are from Accel-Sim simulation. While they claim to model H100 (Table 1), there's no validation against real hardware, even for the baseline case. The power modeling extends AccelWattch with FP8 characteristics "by scaling the power values of INT8 Tensor Core operations" (Section 4)—this is an approximation, not measurement.

**5. The Sensitivity Study is Weak (Section 5.6)**

They claim to evaluate "various scaling levels and block sizes" but only test up to 4 levels and 512 block size on a single model (ViT-Large). The result? "Execution time increases by only 1.1% relative to the baseline."

This seems too good. Where's the breakdown of *why* different configurations behave similarly? Why not show these results in a figure instead of just claiming "we omit a plot for this analysis"?

**6. No Comparison to Custom Accelerators**

Section 6 discusses related work on BFP accelerators (DBPS [26], MSFP [41], FAST [50]). But they never compare Avant-Garde's efficiency against these designs. The paper positions itself as a GPU solution, but doesn't quantify the tradeoff: how much performance are you leaving on the table by not using a custom accelerator?

**7. Training Evaluation is Missing**

The "unflattening" API for training is described in Section 3.2, but all evaluations are inference-only. They claim Avant-Garde supports training, but never demonstrate it. The unflattening operation "introduces a long latency" and runs on CUDA cores—how often does this happen during training, and what's the actual overhead?

**8. Figure 4's Normalized Y-Axis**

Look carefully at Figure 4: "All results are normalized to INT8." The register file usage for MX9 is 1.38× higher—but what's the absolute utilization? If INT8 uses 30% of registers, 1.38× means 41%, which isn't critical. If INT8 uses 70%, then 97% could cause occupancy problems. Without absolute numbers, we can't assess severity.

**9. Energy Results Lack Breakdown**

Figure 13 shows 40-49% energy reduction, but this is just the total. What's the breakdown between dynamic and leakage? Between Tensor Cores and memory? Between compute and data movement? The paper mentions 0.1% leakage overhead in Section 5.4, but the overall energy picture is opaque.

## Q4: What the Authors Didn't Tell You

**1. The FP8 Elephant in the Room**

NVIDIA's H100 and B100 already support FP8 natively with per-tensor scaling in hardware. The authors acknowledge this (Section 1, Section 4) but never directly compare: What's the benefit of MX9 over FP8 in a *fair* setting?

Table 4 shows MX9 (flattened) achieves 80.3% accuracy on ViT-Base—identical to FP32. But what does FP8 achieve? If FP8 also gets 80.3%, then why bother with the complexity of MX9 and Avant-Garde? The paper never makes this comparison.

**2. The "Real Datacenter" Question**

The benchmarks are academic models on academic datasets (ImageNet, English Wikipedia). Real datacenter workloads include:
- Batched inference with dynamic batching
- Multi-tenant GPU sharing
- Speculative decoding for LLMs
- KV-cache management

How does Avant-Garde interact with these? The 44% execution time reduction (Figure 11) is for single-inference passes. What about throughput under real serving conditions?

**3. The Scaling Factor Precision Tradeoff**

Section 3.1 states: "As scaling factors are encoded as exponents, the 8-bit fixed-point adder computes the sum of scaling factors."

But wait—combining two 8-bit scaling factors could overflow. What happens then? Is there clamping? Saturation? This isn't discussed, but it could cause silent accuracy degradation for workloads with high dynamic range.

**4. The Memory Layout Tax**

Section 3.1 mentions: "For example, with the MX6 format, Avant-Garde requires only 192 bytes for a block, occupying two warp registers and leaving 64 bytes unused."

That's 25% wasted storage! For memory-bound workloads, this fragmentation could hurt. The paper doesn't quantify the effective memory amplification across different formats.

**5. Future Format Compatibility is Questionable**

The authors claim (Section 3): "By combining Avant-Garde with... VitBit, future scaled numeric formats can be accommodated with minimal hardware."

But the Operand Transformer is hardwired for specific block sizes and scaling levels. What if a future format uses 3-level scaling with prime block sizes? The 16 FP8/INT8 multipliers (Figure 7) are a fixed resource—this design isn't as flexible as claimed.

**6. The Missing Sparsity Story**

Modern DNNs increasingly use sparse formats (2:4 sparsity on A100/H100). How does Avant-Garde interact with sparsity? Can you have a sparse, scaled numeric format? This combination could be critical for efficiency but is completely unaddressed.

**7. The Silicon Overhead Numbers are Suspicious**

Section 3.3 claims 1.4% area and 1.2% power overhead. These numbers come from synthesis "using FreePDK 45nm technology"—a 2007 academic PDK. Modern GPUs are on 4nm (TSMC N4 for H100). Scaling these numbers across 10+ technology generations is highly uncertain.

**8. The API Burden**

The Avant-Garde API (Figure 9) requires programmers to:
- Declare tensors with specific formats (`scaled mx9`)
- Call `flatten()` explicitly
- Manage flattened vs. non-flattened state

This is additional programmer burden. Compare to FP8, where NVIDIA's Transformer Engine handles scaling automatically. The paper doesn't discuss programmer usability or the potential for compiler automation.