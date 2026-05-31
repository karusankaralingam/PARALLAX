# Methodology Audit: Avant-Garde Evaluation

*adjusts glasses and pulls up the paper*

Alright, let's dissect what they actually measured versus what they claim.

## 1. Benchmark Selection Analysis

**What they used:**
- One microbenchmark (1M parameters, matrix multiplication)
- Four DNN models: ViT-Base (86M), ViT-Large (307M), BERT (110M), GPT-2 Small (124M)
- Datasets: ImageNet for ViT, English Wikipedia for BERT/GPT-2

**The Cherry-Pick Check:**

This benchmark suite is... *acceptable but narrow*. Here's what concerns me:

1. **Model Size Homogeneity**: All models are in the 86M-307M parameter range. Where's GPT-3 (175B)? Where's LLaMA-70B? They mention GPT-3's computational demands in the introduction to motivate the problem, but then evaluate on GPT-2 Small (124M parameters). That's a **1,400x smaller model**.

2. **Missing Workload Diversity**: 
   - No CNNs (ResNet, EfficientNet) - these have different memory access patterns
   - No sparse models or mixture-of-experts
   - No recommendation models (DLRM) which have irregular memory access
   - No diffusion models

3. **The Microbenchmark Red Flag**: A 1M parameter matrix multiplication microbenchmark is essentially a best-case scenario. It's compute-bound, perfectly regular, and maximizes Tensor Core utilization. Of course it shows the highest speedup (up to 67% execution time reduction).

## 2. The Baseline Validity

**What they compare against:**
- NVIDIA H100 GPU with software-based scaled numeric format support
- FP8 as the "native" baseline

**The Strawman Question:**

This is where it gets interesting. Their baseline is *legitimate* in the sense that current GPUs genuinely don't have native MX9/HBFP support. However:

1. **The FP8 Comparison is Missing**: They claim H100 supports FP8, but Figure 10 shows MXFP8 results, not a direct FP8 vs. their approach comparison. What's the speedup over *native* FP8 inference? This is the real competition.

2. **No Comparison to Existing Accelerators**: Section 6 mentions DBPS, FAST, and Bucket Getter - all accelerators for scaled numeric formats. Where's the head-to-head comparison? They cite these works but don't benchmark against them.

3. **Software Baseline Implementation**: They implemented the software baseline themselves. Did they optimize it? Did they use NVIDIA's cuBLAS with FP8? Or did they write naive CUDA code? The instruction stream in Figure 3 looks suspiciously unoptimized.

## 3. The "Gotcha" Graphs

**Look at Figure 10 carefully:**

Notice how the speedup *decreases* as model size increases:
- ViT-Base: ~1.75x throughput
- ViT-Large: ~1.65x throughput
- GPT-2: ~1.55x throughput

They acknowledge this in Section 5.1: *"The throughput improvement of Avant-Garde slightly decreases as model size increases."* They attribute it to memory access overhead, but this is a **critical trend**. If we extrapolate to GPT-3 scale, what happens? The gains might vanish entirely.

**Figure 4's Y-axis:**
The register file usage comparison (Figure 4a) is normalized, but they don't tell us the absolute numbers. A 1.38x increase sounds bad, but if the baseline uses 20 registers and they use 28, that's still well within the 256KB register file budget per SM.

## 4. The Missing Data

**What I would have loved to see:**

1. **Batch Size Sensitivity**: All results appear to be single-batch inference. What happens at batch size 32, 64, 128? Memory bandwidth becomes the bottleneck, and their operand transformation overhead might become visible.

2. **Training Results**: They claim Avant-Garde supports training (Section 3.2 mentions "unflattening" for weight updates), but **all evaluation is inference-only**. Where's the training throughput? Training has different memory access patterns and requires gradient accumulation.

3. **Real Hardware Validation**: This is a simulation study using Accel-Sim. They modified the simulator to model FP8 (Section 4 admits: "As Accel-Sim does not support FP8, we modify the simulator..."). How validated is this model? What's the simulation error margin?

4. **End-to-End Latency**: They show throughput and execution time, but what about tail latency? In production, P99 latency matters more than average throughput.

5. **Memory Bandwidth Utilization**: They claim memory efficiency improvements but don't show memory bandwidth utilization graphs. Is the system compute-bound or memory-bound?

## 5. The "Zero-Event" Reality Check

**Does the problem they solve actually matter?**

Their core claim: Software-based scaling factor application causes 2.14x instruction overhead (Figure 4b).

**Reality check questions:**
1. In production inference (with batching, KV-cache, speculative decoding), how much time is actually spent on MMA operations vs. attention, softmax, layer norm?
2. If MMA is only 60% of total execution time, a 74% MMA speedup translates to ~44% end-to-end speedup. Their 44% execution time reduction (Figure 11) suggests MMA dominates, but they don't break this down.

## 6. Accuracy Claims

**Table 4 is suspiciously clean:**

| Model | FP32 | MX9 (Flattened) | MX9 (Non-Flattened) |
|-------|------|-----------------|---------------------|
| ViT-Base | 80.3% | 80.3% | 80.3% |
| BERT | 10.09 | 10.11 | 10.10 |
| GPT-2 | 17.48 | 17.49 | 17.49 |

The accuracy is *identical* to two decimal places? This is either remarkable engineering or they're using pre-trained models that were already quantization-aware. They mention using "Microsoft's MX emulator" - did they fine-tune the models for MX9, or is this zero-shot quantization?

---

## Discussion Question

*leans forward*

Here's what I want you to think about:

**If we ran Avant-Garde on a real datacenter workload - say, serving GPT-4 with continuous batching, KV-cache, and speculative decoding - do you think the 74% throughput improvement would hold?**

Consider:
1. KV-cache operations are memory-bound, not compute-bound
2. Speculative decoding has irregular control flow
3. Continuous batching means variable batch sizes
4. Real inference has attention mechanisms that aren't pure GEMM

The paper optimizes the *easy* part (dense matrix multiplication). The hard parts of modern LLM inference might not benefit at all.

---

**My verdict:** The methodology is *sound but limited*. They prove their hardware works on small-to-medium models with regular workloads. The extrapolation to "this solves the arithmetic density crisis for GPT-3 scale" is... optimistic. I'd want to see training results, larger models, and real hardware validation before believing the headline numbers.