# Evaluation Methodology Audit: Neo Paper

*adjusts glasses and pulls up the experimental section*

Let me be direct with you: this paper has some solid experimental work, but there are several methodological choices that warrant scrutiny. Let's dissect this systematically.

---

## 1. Methodology Audit

### Benchmark Selection

They evaluate on three workloads:
- **PackBootstrap** - A microbenchmark for the core FHE operation
- **HELR** - Logistic regression on MNIST (binary classification, 3 vs 8)
- **ResNet-20/32/56** - CNN inference on CIFAR-10

**My Assessment:** This is a *reasonable* but *narrow* benchmark suite. Here's what concerns me:

1. **HELR is a toy workload.** Training on 14×14 MNIST images with 196 weights? That's not representative of real privacy-preserving ML. Where's BERT inference? Where's a recommendation system with sparse embeddings? These would stress the memory hierarchy very differently.

2. **ResNet is compute-bound and regular.** CNNs have beautiful, predictable access patterns. FHE on transformers with attention mechanisms would expose whether their data layout optimizations generalize.

3. **No datacenter-scale evaluation.** They batch 128 ciphertexts, but real deployments might need to handle thousands of concurrent queries with different rotation indices. Does their evaluation key management scale?

---

## 2. The "Gotcha" Graphs

### Figure 12 - The Valid Proportion Problem

*This is the most honest graph in the paper, and it reveals a fundamental limitation.*

Look at how the "valid proportion" for IP drops as level `l` decreases:
- At l=35: ~75% valid
- At l=15: ~25% valid  
- At l=5: Essentially unusable on TCU

They acknowledge this by saying "when valid proportion exceeds 80%, map to TCU; otherwise, map to CUDA Cores." But here's the problem: **during Bootstrapping, you spend most of your time at low levels.** The paper doesn't break down what percentage of total execution time is spent at each level.

**Question for you:** If 60% of KeySwitch operations happen at l < 20, how much of their claimed TCU benefit actually materializes in practice?

### Figure 16 - The WordSize Trade-off

Notice how WordSize_T = 48 is optimal, but the difference between 48 and 64 is substantial at high levels. They chose 48 as the "default," but:

- At l=35, WordSize_T=64 would be faster for NTT
- At l=15, WordSize_T=48 wins

**This suggests their "optimal" parameter is actually a compromise that isn't optimal for any specific operating point.** A truly adaptive system would switch WordSize_T based on current level.

---

## 3. The Missing Data

### What I Would Have Loved to See:

1. **Roofline Analysis:** They claim to improve TCU utilization, but where's the roofline model showing how close they are to peak? The A100 has 19.5 TFLOPS FP64 on TCU - what percentage are they achieving?

2. **Memory Bandwidth Utilization:** Figure 2 shows they reduced memory transfer requirements, but did this translate to reduced *time* waiting on memory? What's their achieved bandwidth vs. the A100's 1.5 TB/s?

3. **Energy Consumption:** For privacy-preserving computation in datacenters, energy efficiency matters. They never mention power.

4. **Latency vs. Throughput Trade-off:** All results are throughput-oriented (batch size 128). What happens when you need single-ciphertext latency for interactive applications?

5. **Comparison at Equal Security Levels:** Table 4 shows Set-H has λ≥98 while others have λ≥128. The CPU baseline from Craterlake uses Set-H. **This is not an apples-to-apples comparison.**

---

## 4. Baseline Validity Check

### Is TensorFHE a Fair Baseline?

TensorFHE is from HPCA 2023 - that's recent and reasonable. However:

1. **They had to reimplement TensorFHE with Double Scaling (DS)** because the original "leads to precision loss." This is fair, but it means they're comparing against their own reimplementation, not the published artifact.

2. **HEonGPU comparison is more concerning.** They only beat HEonGPU by 19.9% on average (Table 5), and HEonGPU doesn't use TCU at all. This suggests their TCU optimizations provide diminishing returns compared to good CUDA Core implementations.

### The 3.28× Claim

The abstract says "Neo outperforms TensorFHE by 3.28×." But look at Table 5:
- PackBootstrap: 0.74s → 0.24s = 3.08×
- HELR: 0.78s → 0.22s = 3.54×
- ResNet-20: 38.77s → 12.03s = 3.22×

The 3.28× is cherry-picked from somewhere. More importantly, **against HEonGPU, they only achieve 1.2-1.5× speedup**, which is a much more modest improvement.

---

## 5. Discussion Questions

1. **The Generalization Problem:** Their BConv and IP optimizations rely on transforming element-wise operations into matrix multiplications. This works because FHE has specific algebraic structure. But if FHE algorithms evolve (as they acknowledge in Section 3.1), will these transformations still apply? They claim GPGPU flexibility is an advantage, but their optimizations are highly FHE-specific.

2. **The Real-World Workload Question:** If we ran Neo on a real Google Search query trace with:
   - Variable ciphertext levels (not always starting at l=35)
   - Mixed operation types (not just HMULT/HROTATE heavy)
   - Heterogeneous batch sizes
   
   Would the gains hold? Their sensitivity study (Figure 17) shows performance degrades significantly at BatchSize=8. Real workloads often can't wait to accumulate 128 ciphertexts.

3. **The Opportunity Cost:** They use FP64 TCU components, which means they're competing with scientific computing workloads for the same hardware. In a shared datacenter, would it be better to run FHE on CUDA Cores and let ML workloads use the TCUs?

---

## Summary Verdict

**Strengths:**
- Honest about limitations (Figure 12, the 80% threshold)
- Multiple parameter configurations tested
- Reasonable baseline selection

**Weaknesses:**
- Narrow benchmark suite (no transformers, no sparse workloads)
- Missing roofline/bandwidth analysis
- The headline 3.28× number obscures the more modest 1.2× improvement over HEonGPU
- No energy or single-query latency analysis

**The Bottom Line:** This is solid systems work, but the evaluation is optimized to show the technique in its best light. The real question isn't "does Neo beat TensorFHE?" - it's "when would I choose Neo over HEonGPU, and is the 20% improvement worth the implementation complexity?"

What aspects would you like to dig deeper into?