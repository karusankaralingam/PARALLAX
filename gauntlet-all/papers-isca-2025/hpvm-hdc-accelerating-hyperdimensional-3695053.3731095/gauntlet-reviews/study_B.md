# Study B — Rich Directive
**Paper:** 3695053.3731095  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 08:41

---

Q1: Whiteboard Explanation

Imagine I'm explaining this to a colleague at a whiteboard:

"So the problem we're tackling is that Hyperdimensional Computing—this brain-inspired ML paradigm that uses very high-dimensional vectors (like 10,000 dimensions)—has a serious programmability problem. People write HDC code in Python for prototyping, then rewrite it in CUDA for GPUs, then rewrite it again for FPGAs or custom accelerators. Each version is completely different, and you can't easily port between them.

[Drawing a stack diagram]

Our solution has two parts. First, HDC++—a domain-specific language embedded in C++ that provides high-level primitives. Instead of manually implementing Hamming distance with nested loops, you just write `__hetero_hdc_hamming_distance(data, classes)`. The language has hypervector and hypermatrix as first-class types, plus 24 primitives covering encoding, similarity measures, and arithmetic.

[Drawing the key abstraction]

The clever part is we have two levels of primitives. Fine-grained ones like `hamming_distance`, `matmul`, `wrap_shift` work everywhere. But we also have coarse-grained stage primitives: `encoding_loop`, `training_loop`, `inference_loop`. These take an implementation function as a parameter—that function runs on CPUs/GPUs, but on HDC accelerators, we can map the whole loop directly to hardware instructions.

[Drawing the compilation flow]

HDC++ compiles down to HPVM-HDC IR, which extends HPVM's hierarchical dataflow representation with HDC-specific intrinsics. From there, different backends kick in:
- CPU: HDC ops become HPVM subgraphs → sequential code
- GPU: HDC ops become cuBLAS/Thrust/CUDA kernel calls
- Digital ASIC: stage primitives become accelerator API calls
- ReRAM simulator: same, different API

[Drawing the approximation knobs]

We also implemented two automatic optimizations that exploit HDC's error resilience. Automatic binarization propagates 1-bit representations through the dataflow. Reduction perforation lets you skip elements during similarity computations—you can do strided or segmented access. Both are specified with minimal code changes."

Q2: The Key Insight

The central insight is that HDC applications exhibit a natural two-level structure that can be exploited for portable compilation across radically different hardware targets: fine-grained element-wise and reduction operations that map well to SIMD/GPU parallelism, and coarse-grained algorithmic stages (encoding, training, inference) that map directly to custom accelerator instructions.

The key architectural implication is that HDC accelerators don't expose fine-grained ISAs—they expose monolithic operations like "run inference on this batch." Traditional compilation approaches that break everything into primitive operations cannot target such hardware. By providing both granularities in the programming model and IR, HPVM-HDC can lower the same source code to either fine-grained parallel backends (CPU/GPU) using the implementation function, or coarse-grained accelerator calls using the stage primitives.

This differs from prior work like HDCC (which only targets multi-core CPUs) and HDC libraries like TorchHD (which cannot extend to accelerators) because neither captures the hierarchical structure needed for heterogeneous compilation. The insight enables "write once, run on four different hardware classes" for the first time in the HDC domain.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive target coverage**: Demonstrating code generation for CPU, GPU, digital ASIC, and ReRAM simulator from a single source is impressive and validates the core portability claim.

2. **Honest GPU comparison**: The 0.95x-1.5x range against hand-tuned CUDA baselines with a 1.17x geomean is credible. They don't hide that HyperOMS is 5% slower and explain why (level ID encoding uses warp primitives HPVM can't generate).

3. **First accelerator application execution**: The claim that no prior work has run complete HDC applications on these accelerators is significant—even if the comparison is against Jetson Orin rather than baseline accelerator code, it demonstrates the system works.

4. **Approximation evaluation is nuanced**: Figure 7 shows the accuracy-performance tradeoff honestly, including configurations that severely degrade accuracy (points in red). The finding that encoding perforation hurts more than inference perforation is a useful insight.

**Weaknesses:**

1. **CPU comparisons are misleading**: Comparing compiled C++ against interpreted Python+NumPy is not a fair comparison. The authors acknowledge this ("we do not draw conclusions") but still include bars showing 2-15x speedups in Figure 5, which could mislead readers.

2. **Accelerator evaluation methodology is incomplete**: The ASIC-to-host bandwidth is 10 kbps due to "fabrication cost constraints"—this makes end-to-end comparison impossible. Measuring "device-only" time excludes the dominant cost in real deployment. The ReRAM numbers are from a simulator, not real hardware.

3. **Limited application diversity for accelerators**: Only 2 of 5 applications run on accelerators. The paper claims accelerators "only support a proper subset of core HDC operations" but doesn't quantify what fraction of real HDC workloads could be accelerated.

4. **No energy measurements**: HDC's primary value proposition is energy efficiency on edge devices. The paper cites 0.78 TOPS/W for the ASIC but doesn't measure actual energy for their workloads.

5. **Baseline selection concerns**: HD-Hashtable's "baseline" is Python/CuPy interpreted code, making the 15.6x GPU speedup meaningless. The paper should have excluded this from aggregate statistics.

Q4: What the Authors Didn't Tell You

**Implementation realities:**

The GPU backend doesn't use HPVM's GPU code generation for HDC primitives—it directly emits cuBLAS/Thrust/CUDA calls. This means the "retargetable IR" story is partially a facade for the GPU target. Porting to AMD GPUs would require rewriting the entire GPU backend, not just adding a new code generator.

**Accelerator limitations:**

The stage primitives (`encoding_loop`, `training_loop`, `inference_loop`) that enable accelerator targeting are actually quite restrictive. They hardcode specific algorithms: random projection encoding, summation-based training, Hamming distance inference. Applications using different algorithms (like RelHD's graph neighbor encoding or HyperOMS's level ID encoding) cannot use accelerators at all. The paper frames this as "applications don't map," but it's really "the abstraction doesn't generalize."

**Approximation automation is manual:**

Despite calling binarization "automatic," users must manually add the `hdc_sign` primitive to trigger it. The reduction perforation requires explicit `red_perf` annotations with hardcoded parameters. There's no automatic search for good approximation configurations—the 10 configurations in Table 3 were manually explored.

**Performance variance unexplained:**

The 1.5x speedup on HD-Classification GPU versus baseline is attributed to "different tuning choices" but never explained. What tuning choices? This matters because if HPVM-HDC is making better algorithmic decisions, that's a contribution; if the baseline was poorly optimized, the comparison is unfair.

**Scalability questions:**

All experiments use fixed hypervector dimensions (2048-10240). HDC research is exploring much larger dimensions for better accuracy. The paper doesn't discuss how performance scales with dimension, which matters for understanding whether the system will remain competitive as HDC algorithms evolve.

**Missing comparison with TorchHD/OpenHD:**

These existing HDC libraries target CPUs and GPUs with optimized implementations. The paper only compares against application-specific baselines, not against these libraries. This omission likely hides cases where mature libraries would outperform HPVM-HDC.