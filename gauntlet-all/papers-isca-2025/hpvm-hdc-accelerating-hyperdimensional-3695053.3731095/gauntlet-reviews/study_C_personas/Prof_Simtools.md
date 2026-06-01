## Q1: Whiteboard Explanation

Let me walk you through what HPVM-HDC is actually doing under the hood.

**The Problem:** Hyperdimensional Computing (HDC) is a brain-inspired ML paradigm that operates on very high-dimensional vectors (thousands of elements). The challenge is that HDC programs today are written separately for each target—Python for prototyping, CUDA for GPUs, custom C++ for FPGAs, and nobody knows how to program the new HDC accelerators (ASICs, ReRAM devices). You essentially rewrite your application N times for N targets.

**The Solution Architecture:**

```
HDC++ Source Code (single version)
        ↓
   [Clang Frontend]
        ↓
   HPVM-HDC IR  ←── HDC intrinsics + HPVM parallel DAG
        ↓
   [Optional: Binarization, Reduction Perforation]
        ↓
   Target-Specific Backends
   ├── CPU: HPVM → LLVM → x86 machine code
   ├── GPU: HDC primitives → cuBLAS/Thrust/CUDA kernels
   ├── Digital ASIC: training_loop → device API calls
   └── ReRAM Simulator: inference_loop → simulator API
```

**Key Design Choices:**

1. **HDC++ Language (Section 3):** A C++ dialect with 24 HDC-specific primitives (Table 1). You write `__hetero_hdc_hamming_distance(data, classes)` instead of hand-rolling the loop. Crucially, they added three "stage primitives" (`encoding_loop`, `training_loop`, `inference_loop`) that map directly to accelerator coarse-grain instructions.

2. **HPVM-HDC IR (Section 4.1):** HDC primitives become LLVM intrinsics embedded in HPVM's hierarchical dataflow graph. This captures both the parallelism *within* an HDC operation (element-wise ops are embarrassingly parallel) and *across* operations (task-level parallelism).

3. **Two Lowering Strategies:** For CPUs, HDC intrinsics are lowered to HPVM IR subgraphs → LLVM IR → machine code. For GPUs, they *skip* HPVM and directly emit cuBLAS/Thrust calls because vendor libraries are already optimized. For accelerators, the high-level stage primitives map to the device's functional API (Listing 6).

**The Approximation Story:** HDC is noise-tolerant by design, so they exploit this with (a) automatic binarization—propagate 1-bit representations through the dataflow graph after `hdc_sign` operations, and (b) reduction perforation—skip elements when computing Hamming distances (since relative ranking is what matters, not absolute values).

---

## Q2: The Key Insight

The key insight is **separating the abstraction level at which you program from the abstraction level at which hardware executes**.

HDC accelerators expose *coarse-grained instructions* (e.g., "run inference on this entire dataset"), while CPUs/GPUs expose *fine-grained parallelism* (e.g., "compute this element-wise XOR"). Prior HDC codes were written at one level or the other, making them non-portable.

HPVM-HDC's solution is to require programmers to write **both**: the high-level stage primitives (`inference_loop`) for accelerators, *and* an "implementation function" using granular primitives for CPUs/GPUs. The compiler picks which to use based on the target.

**Why this matters:** This is the correct architectural insight. HDC accelerators are *not* programmable in the general sense—they implement specific algorithms (cyclic random projection, Hamming distance search). You cannot express arbitrary HDC algorithms on them. The authors acknowledge this limitation implicitly by requiring the implementation function as a fallback. The abstraction isn't truly unified; it's a clever two-track design masquerading as one.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Diverse Hardware Coverage with Real Silicon (Section 5.2, Figure 6)**
They run on four hardware classes: Intel Xeon CPU, RTX 2080 Ti GPU, a *taped-out* 40nm HDC ASIC, and a ReRAM simulator. Running on actual fabricated silicon (the ASIC) is rare and commendable. The ASIC achieves 2.71x and 4.68x speedup over Jetson Orin for HD-Classification and HD-Clustering respectively (Figure 6).

**S2: Apples-to-Apples GPU Comparison (Section 5.2)**
For the four benchmarks with CUDA C++ baselines (HD-Classification, HD-Clustering, HyperOMS, RelHD), they achieve 0.95x–1.5x performance with a geomean of 1.17x. Notably, HyperOMS is *slower* (0.95x) because their OpenCL codegen can't match hand-written CUDA with warp-level primitives—they're honest about this.

**S3: Approximation Space Exploration (Section 5.3, Figure 7, Table 3)**
The accuracy-vs-speedup Pareto plot (Figure 7) is valuable. It shows that binarization + Hamming distance (Config III) *improves* accuracy to 0.89 (from 0.82 baseline) while achieving 1.6x speedup. Perforating the similarity computation (Configs VII, VIII) maintains accuracy while perforating encoding (Configs V, VI, IX) craters it to 0.25–0.35. This is actionable empirical guidance.

### Weaknesses

**W1: Simulator-Based ReRAM Results Without Validation (Section 2.2, Section 5.2)**
The ReRAM accelerator numbers come from a *simulator* using "extracted timing and energy parameters from commercial SRAM and ReRAM macros in the 40nm technology node provided by a foundry." There's no RTL validation, no comparison to measured silicon, and no discussion of what the simulator does or doesn't model. Does it model write endurance degradation? Analog compute noise? Peripheral circuit delays? The 2.98x speedup for HD-Clustering (Figure 6) could be optimistic or pessimistic—we simply don't know.

**W2: Accelerator Communication Bottleneck Swept Under the Rug (Section 5.2)**
The paper states: "Due to fabrication cost constraints, the digital ASIC and its ARM host CPU only communicate at approximately **10 kbps**." They measure "device-only" performance to work around this, but 10 kbps is comically slow—transferring a single 2048-element float32 hypervector would take ~6.5 seconds. This isn't a minor caveat; it means the ASIC results are entirely divorced from any realistic system deployment. The paper doesn't discuss what a production-quality interconnect would look like or what system-level performance would be.

**W3: CPU Baselines Are Python, Making Speedups Meaningless (Section 5.2)**
All CPU baselines are NumPy/Python. HPVM-HDC generates compiled C++. The 2.35x–15.6x CPU speedups (Figure 5) are comparing apples to interpreted oranges. The authors acknowledge this ("We do not draw conclusions with regards to performance improvements on the CPU") but still include the numbers in the geomean. A fair comparison would be against a compiled C++ baseline with OpenMP or similar.

**W4: No Energy or Power Measurements (Entire Paper)**
For HDC accelerators targeting edge deployment, energy is often *the* metric. The ASIC claims 0.78 TOPS/W (Section 2.2, citing [67]), but they report zero power or energy data from their own experiments. The ReRAM simulator reportedly has energy parameters but they're never shown.

**W5: Limited Accelerator Application Coverage (Section 5.2, Table 2)**
Only HD-Classification and HD-Clustering run on accelerators. HyperOMS, RelHD, and HD-Hashtable cannot because "the other three applications do not map to these particular coarse-grained operations." This exposes a fundamental limitation: the accelerators support a narrow slice of HDC algorithms, and HPVM-HDC can't magically broaden that.

---

## Q4: What the Authors Didn't Tell You

**1. The GPU Backend is Mostly Library Calls, Not Compilation (Section 4.3)**
When targeting NVIDIA GPUs, HPVM-HDC "lowers HDC primitives directly to cuBLAS calls, Thrust calls, or CUDA kernels instead of HPVM IR." This means for GPUs, the "compiler" is largely a library wrapper. The actual code generation happens in NVIDIA's proprietary toolchain. HyperOMS's OpenCL path (the one case where they use HPVM's generic GPU backend) is 5% slower than the baseline. This suggests their actual GPU codegen is weaker than hand-tuned CUDA—the speedups come from cuBLAS/Thrust, not novel compilation.

**2. The ASIC Interface Bandwidth Would Dominate Real Workloads**
At 10 kbps, transferring the Isolet dataset (617 features × thousands of samples × 4 bytes) would take hours. Even with a fast PCIe-like interface, the "execute_retrain" and "execute_inference" calls in Listing 6 move data per-sample, suggesting they couldn't batch effectively. A production system would need fundamentally different data movement patterns.

**3. The Approximation Optimizations Only Work on CPU/GPU (Section 4.2)**
"They are applicable on the CPU and GPU... However, they are not applicable on the HDC accelerators, since these devices do not support these approximations." So binarization and perforation—key selling points—are irrelevant for the accelerators. The accelerators execute fixed algorithms, and if you want approximate HDC, you need flexible hardware.

**4. Lines-of-Code Comparison Cherry-Picks Targets (Table 4, Section 5.4)**
The 1.6x geomean LoC reduction "combines the lines of codes across all baseline target implementations." But HDC++ requires *one* implementation while baselines require *two* (CPU + GPU). If you compare against the GPU baseline alone (the production-relevant target), HD-Classification is 410 vs 608 (1.48x), HyperOMS is 560 vs 1188 (2.12x), but RelHD is 642 vs 457 (0.71x—HDC++ is *more* code). The story is mixed.

**5. No Artifact Availability Statement**
The paper doesn't mention a GitHub repository, Dockerized artifact, or any form of reproducibility package. For a compiler paper claiming to be "the first retargetable compilation framework for HDC," this is a significant omission. We cannot independently verify their claims or build on their work without reimplementing from the paper.