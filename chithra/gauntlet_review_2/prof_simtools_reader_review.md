# Dr. Sim's Tooling Analysis: Neo Paper

*adjusts glasses and pulls up the methodology section*

Alright, let's talk about what's actually being measured here, because simulation is doomed to succeed—and this paper has some interesting choices in its experimental setup.

---

## 1. Tooling Breakdown

**Platform:** Real hardware—NVIDIA A100 GPU (40GB), Hygon C86 7285 CPU, 512GB system memory (Table 3).

This is **not a simulation study**. They're running on actual silicon, which is good for validity. However, let me be precise about what they're *actually* measuring:

- **Software stack:** GCC 8.4, CUDA 11.3, PyTorch 1.12, CuPy 11.5
- **Measurement methodology:** Execution time comparisons against TensorFHE and HEonGPU
- **Workloads:** PackBootstrap, HELR (logistic regression), ResNet-20/32/56 inference

**The Good:** Real hardware eliminates the "simulation gap" problem. When they say 3.28× speedup over TensorFHE, that's measured wall-clock time on identical hardware.

**The Concerning:** They don't describe their timing methodology in detail. Are they measuring:
- Kernel launch overhead?
- Memory transfer time to/from host?
- Warm-up iterations before measurement?
- Statistical variance across runs?

---

## 2. The Modeling Risk: What's Missing

### 2.1 Microbenchmarking Methodology

Look at Table 6—they report operation times in microseconds (μs) with precision to one decimal place. For HMULT at 3472.5 μs, that's suspiciously precise. Where are the:
- **Error bars?** Standard deviation across runs?
- **Warm-up periods?** GPU frequency scaling can affect early measurements by 10-20%
- **Memory state?** Was the cache cold or warm between measurements?

### 2.2 The BatchSize Dependency

Figure 17 shows performance varies significantly with BatchSize (from 8 to 128). They default to 128, which maximizes parallelism. But:

> "Due to the limitations of GPGPU memory capacity, BatchSize cannot be increased indefinitely"

This is a **capacity-bound** optimization. They're measuring peak throughput, not latency-sensitive scenarios. For real-world FHE deployment (e.g., single-query privacy-preserving inference), BatchSize=1 performance matters—and they don't report it.

### 2.3 The Comparison Baseline Problem

They compare against TensorFHE and HEonGPU, but:

1. **TensorFHE reimplementation:** "We reimplemented TensorFHE with DS integrated since the absence of DS in TensorFHE leads to precision loss" (Table 5 footnote). They modified their baseline. Did they optimize it fairly?

2. **HEonGPU:** Uses different parameters (Set-E vs Set-C/D). The 19.9% improvement claim mixes parameter configurations.

3. **CPU baseline:** "The application performance data of the CPU was obtained from Craterlake[40]"—they're citing another paper's CPU numbers, not measuring themselves. Different CPU, different memory subsystem, different compiler optimizations.

---

## 3. The "Impossible Physics" Check

### 3.1 TCU Utilization Claims

They claim FP64 TCU components outperform INT8 for their workload (Figure 3). Let me verify the math:

- A100 INT8 TCU: 624 TFLOPS
- A100 FP64 TCU: 19.5 TFLOPS
- Ratio: ~32×

Yet they show FP64 is 1.65× faster for 36-bit operations. This is plausible because:
- Booth decomposition overhead for INT8 (5×5 = 25 partial products)
- FP64 only needs 3 partial products for 36-bit
- Fragment shape mismatch (16×16×16 for INT8 vs 8×8×4 for FP64)

**This checks out mathematically**, but they should have shown roofline analysis to prove they're compute-bound, not memory-bound.

### 3.2 Memory Bandwidth Reality

Figure 2 shows memory transfer requirements. For Set-B at l=35:
- Hybrid method: 9.43 GB
- KLSS method: 7.80 GB

A100 has 1.6 TB/s HBM2e bandwidth. For a KeySwitch operation taking ~3.2ms (Table 8), that's:
- 7.80 GB / 0.0032s = 2.4 TB/s required

**This exceeds A100's bandwidth.** Either:
1. Their memory numbers are per-batch (128 ciphertexts), making per-operation ~61 GB/s (reasonable)
2. They're achieving significant cache reuse (their claimed optimization)
3. The numbers are theoretical, not measured

They don't clarify this. **This is a red flag for reproducibility.**

---

## 4. Artifact Availability: The Paperware Problem

**Critical omission:** There is no GitHub link. No artifact appendix. No Docker container.

For a systems paper claiming 3.28× speedup through software optimization, this is problematic. Without code:
- We can't verify the TensorFHE "reimplementation"
- We can't reproduce the parameter sensitivity studies
- We can't validate the KLSS implementation correctness

The acknowledgments mention funding sources but no artifact evaluation badge. At ISCA '25, artifact evaluation is standard practice.

---

## 5. Discussion Questions for the Student

1. **Validation question:** They claim their BConv transformation to matrix multiplication reduces memory transfers (Figure 15). How would you design a microbenchmark using NVIDIA Nsight Compute to verify that global memory transactions actually decreased, rather than just measuring end-to-end time?

2. **Roofline question:** Given their claim that FP64 TCU is superior to INT8 TCU for this workload, what would the operational intensity (FLOP/byte) need to be for this to be true? Can you derive this from their Figure 3 data?

3. **Reproducibility question:** If you wanted to reproduce Table 6's HMULT timing of 3472.5 μs, what experimental controls would you need that aren't specified in the paper?

4. **Generalization question:** Their optimization relies on WordSize_T = 48 being optimal (Figure 16). For a different GPU architecture (say, H100 with different TCU fragment sizes), would this conclusion hold? What architectural parameters would you need to re-evaluate?

---

## Summary Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| **Hardware validity** | ✓ Good | Real A100, not simulation |
| **Baseline fairness** | ⚠ Questionable | Modified TensorFHE, cited CPU numbers |
| **Statistical rigor** | ✗ Missing | No error bars, no variance reported |
| **Memory modeling** | ⚠ Unclear | Transfer numbers don't reconcile with bandwidth |
| **Artifacts** | ✗ Missing | No code release mentioned |

**Bottom line:** The core ideas are sound—transforming element-wise operations to matrix multiplication for TCU acceleration is legitimate. But the experimental methodology has gaps that would make me nervous about reproducing these exact numbers. The 3.28× claim is plausible but not rigorously validated.