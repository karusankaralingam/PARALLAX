## Q1: Whiteboard Explanation

Let me sketch this out for you.

**The Problem:** Hyperdimensional Computing (HDC) is a brain-inspired ML paradigm that encodes data into very long vectors (thousands of elements, called "hypervectors") and performs lightweight operations on them—think XOR, addition, Hamming distance—instead of heavy matrix multiplications like DNNs. The catch? HDC researchers have been writing their algorithms in Python/NumPy for prototyping, then painfully rewriting everything in CUDA for GPUs, then *again* in completely different C++ with hardware-specific primitives for FPGAs or custom accelerators. Each target is a separate codebase.

**The Solution (What They Built):**

```
HDC++ Source Code (Single Program)
         │
         ▼
    ┌─────────────┐
    │  HPVM-HDC   │  ← Compiler with HDC-specific IR
    │  Compiler   │
    └─────────────┘
         │
    ┌────┴────┬────────┬────────────┐
    ▼         ▼        ▼            ▼
   CPU       GPU    Digital     ReRAM
  (LLVM)   (CUDA/   ASIC      Accelerator
           cuBLAS)            (Simulator)
```

**The Key Abstraction:** HDC++ provides ~24 domain-specific primitives like `hypervector`, `hypermatrix`, `hamming_distance`, `matmul`, `wrap_shift`, etc. (Table 1, page 7). You write your algorithm once using these. The compiler then:

1. Lowers these to an intermediate representation (HPVM IR extended with HDC intrinsics)
2. For CPUs: expands HDC ops into parallel LLVM code
3. For GPUs: maps HDC ops directly to cuBLAS/Thrust calls or CUDA kernels
4. For accelerators: maps coarse-grained "stage primitives" (`encoding_loop`, `training_loop`, `inference_loop`) to the accelerator's functional interface

**The Approximation Trick:** HDC is inherently noise-tolerant (Section 4.2). They exploit this with:
- **Automatic Binarization:** Propagates 1-bit precision through the dataflow—if you use `hdc_sign()` anywhere, it taints downstream operations to use bit-packed representations and Hamming distance instead of cosine similarity.
- **Reduction Perforation:** Skip elements when computing reductions (e.g., only look at every other element during Hamming distance). This is loop perforation applied to HDC-specific patterns.

---

## Q2: The Key Insight

**The Real Contribution:** The core insight is recognizing that HDC accelerators expose *coarse-grained instructions* (like "run inference on this entire dataset") that are fundamentally incompatible with how people write HDC algorithms in research code (fine-grained element-wise operations). The paper bridges this gap with a **two-level IR design**:

1. Fine-grained HDC primitives (like `hamming_distance`, `wrap_shift`) for CPUs/GPUs
2. Coarse-grained "stage primitives" (`inference_loop`, `training_loop`) for accelerators

The clever bit (Section 3.1, page 6): The stage primitives take an "implementation function" as a parameter. When targeting CPUs/GPUs, that function is executed. When targeting accelerators, it's ignored and replaced with a call to the accelerator's monolithic instruction. Quote from Section 4.3 (page 9):

> "This is because while HDC accelerators implement specific encoding, training, and inference algorithms, CPUs and GPUs can be programmed to implement a variety of algorithms—it is up to the application developer to choose concrete versions of these algorithms for these targets."

**Why This Matters:** This is *not* just a DSL paper. The insight is that HDC's algorithmic structure (encode → train/infer → search) happens to align well with what these emerging accelerators can do, and you can exploit that alignment if your IR is designed right. Prior work (HDCC [61]) only targeted CPUs and couldn't express this.

**The Approximation Optimizations** are secondary but clever: binarization is essentially a type-system taint analysis (Algorithm 1, page 8) that propagates reduced precision. This is the right abstraction level—you annotate *where* you want binarization (`hdc_sign`), not *how* to implement it for each target.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: They actually ran on taped-out silicon (partially).** The Digital ASIC is real hardware (40nm, Section 2.2, page 4). This is rare and commendable—most "accelerator compiler" papers use simulators end-to-end. The ReRAM accelerator is simulated, but with "extracted timing and energy parameters from commercial SRAM and ReRAM macros" (page 5).

**S2: Honest about the 1.17x GPU speedup.** Figure 5 (page 10) shows HPVM-HDC achieves a *geomean* 1.17x speedup over hand-written CUDA baselines. They explicitly acknowledge: "This speedup is primarily the result of different tuning choices in HPVM-HDC generated code compared to the baseline codes" (page 10). This is refreshingly honest—they're not claiming compiler magic, just competitive parity with less code.

**S3: HyperOMS is slower and they admit it.** Section 5.2 (page 10) explicitly states HyperOMS is 5% *slower* than baseline because its bottleneck (level ID encoding) uses generic OpenCL from HPVM's backend instead of hand-optimized CUDA with warp-level primitives. This transparency builds credibility.

**S4: The approximation study (Figure 7, Table 3) is well-designed.** They show 10 configurations and plot speedup vs. accuracy (page 11-12). The key finding—that perforating *inference* is safe but perforating *encoding* destroys accuracy—is a useful insight for HDC practitioners.

### Weaknesses

**W1: The CPU baseline is Python.** Table 4 (page 12) reveals the CPU baselines are Python/NumPy. Comparing compiled C++ (via LLVM) against interpreted Python and claiming "speedup" is meaningless. They acknowledge this: "We do not draw conclusions with regards to performance improvements on the CPU" (page 10). But then why include those bars in Figure 5?

**W2: Accelerator evaluation is device-only, hiding system overhead.** Section 5.2 (page 11) admits: "We measure the 'device-only' performance for the accelerators." The Digital ASIC communicates with its host at "approximately 10 kbps" due to "fabrication cost constraints." This means the end-to-end system performance would be dominated by data transfer, which they don't report. For the ReRAM accelerator, they only simulate the accelerator itself, not the system. The comparison against Jetson Orin is thus apples-to-oranges.

**W3: Only 2 of 5 applications run on accelerators.** HD-Classification and HD-Clustering map to the accelerators; HyperOMS, RelHD, and HD-Hashtable do not (Section 5.2, page 10). The paper's title promises "Accelerating Hyperdimensional Computing" but the accelerator story is limited to classification kernels—the simplest HDC workloads.

**W4: Approximation is only evaluated on HD-Classification inference.** Section 5.3's entire analysis (Figure 7, Table 3) uses a single benchmark. They don't show whether reduction perforation or binarization generalizes to HyperOMS, RelHD, or other applications. The encoding dimension was fixed at 10240 for this study—no sensitivity analysis.

**W5: No energy/power numbers for accelerators.** Section 2.2 mentions the ASIC achieves "0.78 TOPS/W" but Section 5 never reports energy consumption for their compiled applications. For edge-targeted accelerators, this is a critical omission.

---

## Q4: What the Authors Didn't Tell You

**1. The Accelerators Only Support One Algorithm Each.** Buried in Section 2.2 (page 4-5): The Digital ASIC does "cyclic random projecting" encoding and "Hamming distance" inference. The ReRAM does "tensorized encoding" and Hamming distance. If your HDC application needs a different encoding scheme (level ID, graph neighbor, k-mer based—see Table 2), you cannot use these accelerators. The paper doesn't quantify how much of the HDC application space this actually covers.

**2. The "Implementation Function" Abstraction is Leaky.** The paper claims you write one program for all targets, but Section 3.1 (page 6) reveals: if you use `encoding_loop`/`inference_loop`, you must provide an implementation function for CPUs/GPUs that is *ignored* when targeting accelerators. This means the programmer must understand what algorithms the accelerators support and ensure their implementation function is semantically compatible. What happens if they differ? The paper is silent.

**3. No Discussion of Memory Capacity or Batch Size Limits.** The Digital ASIC has a "256kb On-chip Buffer" (Figure 1, page 5). The ReRAM has a "1024×1024 ReRAM Macro." Neither Section 4.3 nor Section 5 discusses what happens when hypervector dimensions or dataset sizes exceed these limits. For real HDC applications with D=10000+ dimensions, does the compiler tile? Does it fall back to the host? This is completely unaddressed.

**4. The Jetson Comparison is Misleading.** Figure 6 (page 11) compares accelerators against Jetson Orin, calling it "representative of GPU-based compute available in edge environments." But the Jetson AGX Orin 64GB is a $1999 board with 64 tensor cores—it's the *top-end* of NVIDIA's edge lineup, not a representative edge device. A fairer comparison would be against Jetson Nano or a mobile GPU.

**5. The 10 kbps Communication Bottleneck is Catastrophic.** They mention this once (Section 5.2, page 11) then measure "device-only" time. Back-of-envelope: transferring a 10K-dimension float32 hypervector at 10 kbps takes ~32 seconds. Even with int8 elements, you're at ~8 seconds per vector. This means the ASIC is unusable for any real workload with the current interface—the accelerator speedups in Figure 6 are fictional for end-to-end applications.

**6. Automatic Binarization Only Works with `hdc_sign`.** Algorithm 1 (page 8) is triggered by finding `hdc_sign` operations. If a programmer writes code that *should* be binarizable but doesn't use `hdc_sign`, the optimization doesn't fire. The paper doesn't discuss how to handle incremental binarization or partial precision hierarchies (e.g., 4-bit, 8-bit).

**7. The "1.6x LOC Reduction" Counts Against Multiple Baselines.** Table 4's methodology is suspect. They sum LOC across *all* baseline implementations (CPU + GPU) and compare against one HDC++ program. A fairer comparison would be per-target. When you look at individual entries, HD-Classification's HDC++ code (410 LOC) is *larger* than the Python baseline (193 LOC) and smaller than CUDA (608 LOC). The "reduction" is an artifact of counting twice.