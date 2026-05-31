# Methodology Audit: LightML Evaluation

*adjusts glasses and pulls up the paper*

Let me be direct with you: this is an ISCA '25 paper, which means it passed peer review at a top venue. But that doesn't mean the evaluation is bulletproof. Let's dissect what they actually measured versus what they claim.

---

## 1. The Benchmark Selection Problem

**What they used:**
- CNNs: ResNet-18/50/101, VGG-11/16/19, MobileNet-V2/V3
- Datasets: CIFAR-10, CIFAR-100, ImageNet
- LLMs (preliminary): BERT, Llama 3.1-8B, ViT/B-16

**The "Cherry-Pick" Check:**

This is a *suspiciously CNN-heavy* benchmark suite. Notice what's **missing**:

1. **Sparse workloads**: No graph neural networks (GNNs), no sparse transformers, no recommendation models with embedding tables. Why does this matter? Their crossbar architecture assumes dense matrix operations. Sparse workloads would expose the utilization problem they briefly mention in Section 8.7.

2. **Irregular memory access patterns**: No pointer-chasing workloads, no attention mechanisms with variable sequence lengths in the main evaluation. They only show LLM results in Section 9 as "preliminary" and admit "our current implementations lack proper optimizations."

3. **Batch size sensitivity**: They fix batch size at 32 throughout. Look at Figure 14 - when they finally vary batch size for LLMs, the results are... not great. The GPU is 2.2x faster than LightML for Llama.

**My concern:** The workloads they chose are *exactly* the ones where dense matrix multiplication dominates. This is the photonic crossbar's sweet spot. The paper essentially says "we're great at what we're great at."

---

## 2. The Baseline Validity Problem

**Look at Table 2 carefully:**

| Platform | Technology | Precision |
|----------|------------|-----------|
| GPU (A100) | 7nm | FP16 |
| TPU | 12nm | FP16 |
| LightML | 28nm | Int5 |

*Record scratch.* 

They're comparing a **28nm photonic chip** against a **7nm GPU**. The technology node difference alone accounts for roughly 4x in power efficiency. They acknowledge this implicitly by saying "13.6x higher than a GPU" when including HBM, but the raw comparison is apples-to-oranges.

**The precision mismatch:**
- GPU/TPU: FP16 (16-bit floating point)
- LightML: Int5 (5-bit integer)

They claim "less than 3% inference accuracy loss" in the abstract, but look at Table 6:

| Dataset | GPU/TPU (FP16) | LightML (Nb=5) | Drop |
|---------|----------------|----------------|------|
| ImageNet | 69.8% | 66.1% | **3.7%** |
| CIFAR-10 | 92.4% | 90.6% | **1.8%** |

That 3.7% drop on ImageNet is *not* negligible for production systems. And they're using MobileNetV2 for ImageNet - a model specifically designed for efficiency. What happens with larger models?

---

## 3. The "Gotcha" Graphs

**Figure 12 - Look at the element-wise operations (f, g, h):**

The normalized latency bars show LightML is **8.2x to 9.7x slower** than the A100 for element-wise multiplication. They admit this in Section 8.5: "LightML is not optimal for element-wise operations due to poor crossbar utilization."

This is a *fundamental architectural limitation*, not a minor issue. Modern transformers are **full** of element-wise operations (LayerNorm, residual connections, attention scaling). Their LLM results in Figure 14 suffer precisely because of this.

**Figure 13 - Utilization Analysis:**

Look at the memory utilization for convolutions - it's between 40-60%. That means their memory system is **underutilized** for the workloads they claim to optimize for. The crossbar achieves >90% utilization, but the system is memory-bound.

---

## 4. The Missing Data

**What I would have loved to see:**

1. **Sensitivity to sequence length for transformers**: They show batch size sensitivity in Figure 14, but what about sequence length? Attention complexity is O(n²) - how does the crossbar handle this?

2. **Energy breakdown**: They give total power (2.97W on-chip, ~19W with HBM), but where does the energy actually go? The laser? The ADCs? The modulators? This matters for understanding scaling limits.

3. **Thermal analysis under sustained load**: Section 3.2 mentions "negligible temperature increases" because they consume <20W. But what about thermal crosstalk in the photonic waveguides during sustained inference? Silicon photonics is notoriously temperature-sensitive.

4. **Real fabrication data**: The error model in Section 3.2 is based on Monte Carlo simulation with assumed Gaussian noise. Their lab prototype (Section 3.1) is a 4×4 array. The 128×128 crossbar is *projected*, not demonstrated.

5. **Comparison against other photonic accelerators**: They cite Netcast [71], Hamerly et al. [26], and others in Section 10, but don't directly compare against them. Why not?

---

## 5. The "Zero-Event" Reality Check

**The key question:** Does the photonic MAC advantage actually matter for real workloads?

Their claim: 325 TOP/s at 3W = 109 TOP/s/W

But look at the utilization data:
- Crossbar utilization: >90% (good)
- Memory utilization: 40-60% (bad)
- Element-wise operations: 1/64 crossbar utilization (terrible)

**Real datacenter workloads** involve:
- Variable batch sizes (not always 32)
- Mixed precision requirements (not always Int5)
- Sparse activations (ReLU zeros out ~50% of activations)
- Irregular memory access patterns (attention, embeddings)

The paper optimizes for the *ideal case* of dense, regular matrix multiplication with fixed batch sizes. This is valid for some workloads (CNN inference on edge devices), but the "general purpose machine learning" claim in the title is... generous.

---

## Discussion Questions for You

1. **If we ran this on a real Google Search query trace** (variable sequence lengths, sparse attention patterns, mixed batch sizes), do you think the 109 TOP/s/W efficiency would hold? Why or why not?

2. **The paper claims "first system-level photonic crossbar architecture"** - but they don't have a fabricated 128×128 chip. Their lab prototype is 4×4. How should we interpret results from a simulated system?

3. **Look at the LLM results in Figure 14**: The GPU is 2.2x faster, but LightML achieves "6x higher energy efficiency per token." Is this a win or a loss? For what deployment scenarios?

4. **The precision limitation (Int5)** requires quantization-aware training or post-training quantization. The paper doesn't discuss the *training* cost of adapting models to Int5. Is this a hidden cost they're not accounting for?

---

## My Bottom Line

This is a **solid architecture paper** with a **carefully scoped evaluation**. The photonic MAC unit is genuinely impressive, and the system-level integration (memory hierarchy, non-linear functions via Fourier series) is novel.

But the evaluation is designed to make the system look good. They chose workloads where dense MMM dominates, fixed the batch size at the GPU's sweet spot, and compared against baselines at different technology nodes and precision levels.

The paper is honest about limitations (element-wise operations, LLM performance), but these limitations are buried in the results section rather than highlighted in the abstract or introduction.

**For your reading:** Focus on Sections 8.5-8.7 and Section 9. That's where the real performance story is, warts and all.