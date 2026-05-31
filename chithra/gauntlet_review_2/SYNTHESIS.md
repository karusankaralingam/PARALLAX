# Master Class Reading Guide: Neo (ISCA '25)

## 1. The "Real" Abstract (No-Hype Summary)

**What they actually built:** A software library that accelerates CKKS homomorphic encryption on NVIDIA A100 GPUs by (1) reformulating two memory-inefficient kernels (Base Conversion and Inner Product) as matrix multiplications, and (2) using the FP64 Tensor Core units instead of INT8 units for these matrix operations.

**The core claim stripped bare:** For 36-48 bit integer arithmetic—which is what CKKS needs—FP64 Tensor Cores beat INT8 Tensor Cores despite INT8 having 32× higher peak throughput. This happens because splitting wide integers into 8-bit chunks for INT8 requires 25 partial products (Booth's algorithm), while FP64's 53-bit mantissa lets you get away with only 3 partial products.

**What the speedup actually means:** 3.28× over TensorFHE (the previous GPU implementation), but only ~20% over HEonGPU (a non-Tensor-Core baseline). The headline number compares against an approach that was leaving significant performance on the table.

---

## 2. The "Rashomon" Synthesis (Conflicting Expert Perspectives)

The experts viewed this paper through fundamentally different lenses, revealing the paper's tensions:

**The Microarchitecture View (Dr. Microarch):** Loves the FP64 insight—it's a genuine "obvious in hindsight" discovery that the raw TOPS numbers are misleading for wide-integer workloads. But flags that the data rearrangement overhead (transposing tensors before/after matrix multiplication) is "not negligible" and the paper doesn't isolate this cost. The 80% threshold for switching IP between TCU and CUDA Cores is called out as a "magic constant" without rigorous justification.

**The Workloads View (Prof. Workloads):** Concerned about benchmark narrowness—MNIST logistic regression and ResNet on CIFAR-10 are toy workloads that don't stress the system the way transformers or sparse embeddings would. More damning: the BatchSize=128 assumption is doing heavy lifting. Figure 17 shows performance degrades 2× at BatchSize=8, but real interactive applications can't always batch 128 ciphertexts. The paper optimizes for throughput, not latency.

**The Simulation/Tooling View (Prof. SimTools):** Notes the absence of error bars, roofline analysis, or memory bandwidth utilization metrics. The memory transfer numbers in Figure 2 don't reconcile cleanly with A100's bandwidth—either there's significant cache reuse they're not quantifying, or the numbers are theoretical rather than measured. No code artifact is released, making reproduction impossible.

**The Industry View (Chief Architect):** Sees the ROI as positive because the "cost" is engineering time, not silicon. But raises hard questions about numerical correctness (is the FP64-to-integer emulation bit-exact across all inputs?), security (timing side channels in multi-tenant environments?), and forward compatibility (does this work on H100?). Would not ship without formal verification of the floating-point arithmetic.

**The FHE Domain View:** Appreciates that they adopted KLSS (a 2023 CRYPTO algorithm) and are first to implement it on GPU. But notes the paper doesn't address key storage overhead—KLSS requires differently-structured evaluation keys—and the security parameter Set-H used for CPU comparison has λ≥98, below the standard 128-bit threshold.

**The Tension:** The microarchitecture experts see a clean hardware utilization story; the workload experts see a narrow evaluation that may not generalize; the tooling experts see missing rigor; the industry perspective sees deployment risks. The paper is strongest as a *proof of concept* that FP64 Tensor Cores are viable for FHE, weaker as a *production-ready system*.

---

## 3. The "Magic Trick" (The Core Mechanism)

**The one insight that makes everything work:** *Element-wise operations with poor data reuse can be restructured as matrix multiplications with excellent data reuse, if you're willing to pay the data layout transformation cost.*

Here's the whiteboard version:

**Original BConv (Base Conversion):**
```
For each of α' output limbs:
    For each of α input limbs:
        For each of N coefficients:
            output[j][n] += input[i][n] × conversion_factor[i][j]
```
Each input coefficient is read α' times from global memory. Terrible.

**Neo's BConv:**
```
Reshape input from [α × BatchSize × N] to [N × BatchSize × α]
Matrix multiply: [N×BatchSize × α] × [α × α'] = [N×BatchSize × α']
Reshape output back
```
Each coefficient read once. The conversion factors form a small matrix that lives in registers. The Tensor Core handles the accumulation internally.

**Why FP64 beats INT8:** A 36-bit integer split into INT8 chunks requires 5 chunks → 5×5=25 partial products. The same integer stored as FP64 (which has 53 mantissa bits) can be split into 3 chunks of 12 bits → only 3 partial products. The 32× raw throughput advantage of INT8 is overwhelmed by the 8× reduction in partial products.

**The KLSS connection:** The KLSS KeySwitch method lets you *choose* the bit-width of intermediate computations (WordSize_T). Pick 48 bits, and you hit the sweet spot where FP64 Tensor Cores are maximally efficient. This is algorithm-hardware co-design: the algorithm provides a knob, the hardware analysis tells you where to set it.

---

## 4. The "Skeleton in the Closet" (What They Didn't Tell You)

**Skeleton #1: The 80% Threshold is Arbitrary**
Section 4.5.3 admits that IP kernel execution switches between Tensor Cores and CUDA Cores based on whether "valid proportion exceeds 80%." This threshold is empirically determined and never justified. Figure 12 shows the valid proportion drops below 50% at low ciphertext levels—meaning for much of a bootstrapping operation, they're *not* using Tensor Cores for IP at all.

**Skeleton #2: The BatchSize Dependency**
Figure 17 shows performance at BatchSize=8 is 2× worse than BatchSize=128. But they default to 128 throughout the evaluation. Real-world FHE services may need to handle single queries with low latency. The paper never reports single-ciphertext latency.

**Skeleton #3: The Baseline Modifications**
Footnote ‡ in Table 5: "We reimplemented TensorFHE with DS integrated since the absence of DS in TensorFHE leads to precision loss." They modified their baseline. The 3.28× speedup is against their own reimplementation, not the published TensorFHE artifact.

**Skeleton #4: The Security Parameter Mismatch**
Table 4 shows Set-H (used for CPU baseline from Craterlake) has λ≥98, while their GPU configurations have λ≥128. The CPU comparison is not apples-to-apples.

**Skeleton #5: No Roofline Analysis**
They claim improved TCU utilization but never show a roofline model. What percentage of peak FP64 Tensor Core throughput are they achieving? What's the operational intensity? Are they compute-bound or memory-bound after their optimizations? We don't know.

**Skeleton #6: Memory Numbers Don't Add Up**
Figure 2 shows ~7.8GB memory transfer for KeySwitch at l=35. At 3.2ms execution time (Table 8), that's 2.4 TB/s—exceeding A100's 1.6 TB/s bandwidth. Either the numbers are per-batch (making per-operation ~61 GB/s), or there's cache reuse they're not quantifying, or the numbers are theoretical. The paper doesn't clarify.

---

## 5. The Verdict (Why This Matters)

**Why we're reading this:** This paper demonstrates a non-obvious result—that for FHE's specific computational patterns, underutilized FP64 Tensor Cores beat the "obvious" INT8 path. This is the kind of insight that changes how people think about hardware utilization.

**What it teaches about research methodology:**
- *Question the obvious:* Everyone assumed INT8 Tensor Cores were the path forward because of raw throughput. The paper shows you need to analyze the full computation, including decomposition overhead.
- *Algorithm-hardware co-design matters:* The KLSS algorithm provides a tunable parameter (WordSize_T); the hardware analysis reveals where to set it.
- *Data layout is half the battle:* The transformation from element-wise to matrix multiplication is useless without the corresponding memory layout changes.

**What it teaches about reading papers critically:**
- *Beware headline numbers:* 3.28× over TensorFHE sounds great, but 20% over HEonGPU is more honest about what the Tensor Core contribution actually provides.
- *Check the baselines:* Modified baselines, mismatched security parameters, and cited (not measured) CPU numbers all weaken the comparison.
- *Look for the missing experiments:* No roofline, no power analysis, no single-query latency, no artifact release.

**The takeaway:** This is a solid systems paper with a genuine insight about FP64 Tensor Core utilization for wide-integer arithmetic. It's not a revolution—FHE is still 4-6 orders of magnitude slower than plaintext computation—but it's a meaningful step that will influence future GPU-based FHE implementations. Read it to understand the FP64 trick; be skeptical of the absolute performance claims.