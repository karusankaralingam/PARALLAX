# Paper Deconstruction: Telos

## Q1: Whiteboard Explanation

Alright, let me break this down for you as if we were at a whiteboard.

**The Problem Domain:** This paper is *not* about LLMs or Transformers—it's about solving Partial Differential Equations (PDEs), which are the bread and butter of scientific computing (physics simulations, weather prediction, structural analysis). When you discretize a PDE on a grid, you get a massive sparse linear system `Ax = b` where the matrix `A` has a very specific, predictable sparsity pattern determined by the "stencil"—essentially, how each grid point depends on its neighbors.

**The Computational Bottleneck:** The paper focuses on **SpTRSV** (Sparse Triangular Solve), which is a sequential nightmare. Imagine solving `Lx = b` where `L` is lower triangular: you solve for `x₀`, then use that to solve for `x₁`, then use both to solve for `x₂`, and so on. This loop-carried dependency is murder for GPUs—Figure 4 shows cuSPARSE achieving only **2.5% of peak throughput** (Section 3, page 4).

**Why This Matters:** In preconditioned iterative solvers like CG with Incomplete Cholesky (CG-IC), SpTRSV dominates—**72-81% of total runtime** according to Figure 2b. You can't just ignore it.

**The Core Idea (Whiteboard Sketch):**

1. **Exploit Structured Sparsity:** Unlike general sparse matrices, PDE-derived matrices have *predictable* patterns (Figure 1 shows Star-7P, Diamond-13P, Box-27P stencils). The authors use this predictability.

2. **Affine Transformation:** The trick is transforming the coordinate system so that "backward dependencies" (points that would violate the hyperplane execution order) are eliminated. Algorithm 2 (page 6) rotates the axes so all dependencies point "forward" in the transformed space.

3. **Plane-Parallel Pipelining:** After transformation, grid points are grouped into hyperplanes where all points *within* a plane are independent (Figure 6a). Each PE handles one column of the transformed grid, and they execute in lockstep, advancing one hyperplane per cycle.

4. **Communication Aggregation:** Instead of gathering all dependent values to one PE (expensive), they *scatter* partial products and aggregate them along fixed paths between neighboring PEs. Figure 7 shows how product terms are combined as they travel through the PE mesh—this is the "systolic" part.

**In Plain English:** They take the ugly, irregular dependencies of SpTRSV and, by exploiting the fact that PDEs produce *regular* patterns, transform them into a clean wavefront execution with nearest-neighbor communication—exactly what a spatial dataflow accelerator is good at.

---

## Q2: The Key Insight

**The Delta (Real Contribution):** The fundamental insight is that **stencil-derived sparsity patterns enable a coordinate transformation that converts the irregular, long-range dependencies of SpTRSV into regular, nearest-neighbor communication** amenable to systolic execution.

This is *not* just "another SpTRSV accelerator." Previous works like Alrescha [1] and Azul [13] (Table 1) handle general sparse matrices and must rely on software-based level-set methods to identify independent rows—requiring expensive synchronization and global memory communication. Telos sidesteps this by recognizing that **the sparsity pattern encodes geometric locality** that can be exploited architecturally.

**The Magic Trick (Mechanism):**

This is fundamentally a **dataflow trick** built on two algorithmic insights:

1. **Affine Transformation (Algorithm 2):** Given a stencil pattern, they compute a transition matrix `T` that maps grid coordinates `(i,j,k)` to new coordinates where all dependencies have positive components. For Diamond-13P, the transformation is `(i, i+j, i+j+k)`. This is elegant—it's a *compile-time* computation based only on the stencil type, not the problem size.

2. **Communication Path Assignment (Algorithm 3):** This is the clever part. They construct aggregation paths so that product terms traveling in the same direction to the same destination are combined along the way. The communication overhead drops from `O(sum of all dependency distances)` to `O(number of distinct 2D direction vectors)`. For Diamond-13P, this is a **2.3× reduction**; for Box-27P, **5.3×** (page 7, bottom).

**The Hardware Realization:**

- **PE Architecture (Figure 8):** Each PE has a scalar unit (computes individual variables), a vector unit (computes product terms using a FIFO of recent variables), and an aggregator (combines and routes partial sums).
  
- **Non-zero Value Packing (Section 5.2, Figure 9):** Coefficients from adjacent columns are interleaved by dependency distance, enabling the vector unit to process multiple "stages" in parallel.

- **Halo Exchange Units (HEUs):** Handle boundary conditions between tiles without expensive wrap-around FIFOs—they just write updated `b` values back to memory for the next tile.

**Why This Is Non-Trivial:** The challenge is that SpTRSV *by definition* has rigid sequential dependencies. The authors don't break this—they restructure the computation so that the *apparent* parallelism (points in a hyperplane) maps cleanly onto spatial PEs, while the *actual* dependencies are handled through deterministic, low-latency PE-to-PE communication.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Appropriate Baselines for SpTRSV:** They compare against cuSPARSE (vendor library), AG-SpTRSV [23] (state-of-the-art GPU algorithm from TACO 2024), and Alrescha [1] (prior HPCA accelerator). This is a reasonable competitive landscape for the SpTRSV kernel. The **8× speedup over AG-SpTRSV** (Figure 12) is meaningful because AG-SpTRSV already applies matrix-specific optimizations.

2. **Roofline Analysis (Figure 13):** They show Telos achieves **67-95% of peak throughput** versus GPU's 2-13%. This is the right metric—it demonstrates they're not just faster in absolute terms but actually utilizing their hardware efficiently.

3. **End-to-End Solver Evaluation (Section 6.3):** They don't just benchmark the kernel in isolation. Figure 15 shows full CG-IC solver performance against FDMAX and Spadix, accounting for convergence rate differences. The **3.7-7.9× speedups** over these domain-specific accelerators are compelling.

4. **Scalability Analysis (Figure 18):** They sweep PE array sizes (6×6 to 10×10), memory bandwidth (64-960 GB/s), and vector lanes. Performance scales linearly with bandwidth until compute-bound, which is the ideal behavior.

5. **Energy Breakdown (Figure 14):** They provide DRAM/SRAM/PE decomposition and show **16-22% energy reduction** over Alrescha, driven by predictable data movement.

### Weaknesses

1. **Synthetic/Controlled Benchmarks Only:** All benchmarks (Table 2) are clean PDEs on regular structured grids. Real scientific applications often have:
   - Adaptive mesh refinement (non-uniform grids)
   - Irregular boundaries
   - Multi-physics coupling
   
   The authors acknowledge structured sparsity is required (Table 1), but they don't discuss degradation on quasi-structured problems.

2. **Baseline Accelerator Modeling:** Section 6.1 admits "None of them has been open-sourced. Therefore, we model their behaviors and scale them to the same configuration as ours." This introduces uncertainty—are the models accurate? What assumptions were made about Alrescha's memory hierarchy or FDMAX's compute utilization?

3. **Problem Sizes Are Modest:** The largest 3D problem is 256³ = 16.7M points (Figure 12). Real HPC applications often involve billions of unknowns. The paper doesn't discuss:
   - Multi-chip scaling
   - What happens when the problem doesn't fit in on-chip buffers
   - Communication overhead at scale

4. **Single-Precision Comparison for PDE Solving:** Section 6.1 notes "we evaluate our design in both FP32 (to match these works) and FP64 (our default)." But Figure 15 compares against FDMAX/Spadix which are FP32-only. For many scientific applications, FP64 is mandatory. The comparison conflates precision effects with architectural improvements.

5. **Missing Latency vs. Throughput Discussion:** They report speedup but not absolute latency. For real-time applications (e.g., control systems), latency bounds matter more than throughput.

6. **No Comparison Against Modern GPU Sparse Libraries:** They use cuSPARSE v12.1, but don't compare against cuDSS (NVIDIA's new direct solver library) or cuSparse-generic APIs optimized for structured patterns.

---

## Q4: What the Authors Didn't Tell You

### The Elephant in the Room: This Is a Niche Problem

The paper positions SpTRSV as universally important ("backbone of numerous scientific problems"), but let me be real: **the target workload is extremely specific.** You need:
1. A PDE on a **structured grid** (no unstructured meshes, no finite elements on irregular domains)
2. A **stencil-based discretization** (not spectral methods, not boundary element methods)
3. An **IC-preconditioned Krylov solver** (not multigrid, which often outperforms IC for these problems)

Modern HPC increasingly uses multigrid methods that have *better* asymptotic complexity than Krylov methods and are naturally more parallel. The authors never mention multigrid—probably because it doesn't require SpTRSV.

### The Memory Bandwidth Assumption

From Table 3: they assume **460 GB/s HBM2 bandwidth**. The accelerator is 7.95mm² at 28nm—this is tiny. Where is the HBM? Real HBM integration requires an interposer and significantly more area. The paper describes the *compute die* but hand-waves the memory system. For a memory-bound kernel (Figure 4a shows 0.2 FLOP/byte arithmetic intensity), this is a critical omission.

### The "11× over Alrescha" Claim Needs Context

From Section 3/Table 1: Alrescha doesn't exploit structured sparsity—it's designed for *general* sparse matrices. Comparing Telos (which *requires* stencil patterns) against Alrescha (which handles arbitrary sparsity) is like comparing a domain-specific language to Python and claiming a 10× speedup. Yes, it's faster for this workload, but it's not a fair apples-to-apples comparison of architectural ideas.

### What Happens When Stencils Get Irregular?

Real PDE applications often have:
- **Variable coefficients** (the paper assumes constant stencil shapes)
- **High-order methods** that produce larger stencils (27-point is their max; some methods use 125-point stencils)
- **Boundary conditions** that break stencil regularity

The paper's HEU design (Section 5.2, Figure 10) handles tile boundaries, but what about internal boundaries (e.g., an object embedded in the domain)? These would create irregular dependencies that break the hyperplane assumption.

### The Division Bottleneck

From Figure 9c and Section 5.2: each PE has a **scalar division unit** to compute `x_i = (b_i - sum) / a_ii`. Division is expensive (typically 10-20× more cycles than multiply). Figure 18c shows most stencils achieve maximum speedup with just 2 vector lanes because "the critical path is determined by the division operation." This means the multiplier-heavy vector unit is underutilized for simpler stencils—they're paying for compute they can't use.

### The Convergence Rate Gambit

Figure 15a shows Telos-CG achieving 3.7-7.9× speedup over FDMAX/Spadix, but the text admits: "These gains stem from the faster convergence of the advanced CG-IC solver, which achieves 18.9× and 41.1× convergence rates." So most of the "speedup" is from using a better *algorithm* (IC-preconditioned CG vs. Jacobi/Gauss-Seidel), not better *hardware*. The Gauss-Seidel comparison (Telos-GS) shows only **1.0-1.2× speedup** in single-iteration performance against these baselines.

### Technology Node Implications

Synthesized at **TSMC 28nm** (Table 3). Modern accelerators target 7nm or 5nm. At a more advanced node:
- Area would shrink ~4-10× 
- Power efficiency would improve ~2-3×
- But memory bandwidth requirements remain constant

The comparison against GPUs (RTX 3090 at Samsung 8nm) involves a significant process disadvantage. A fair comparison would normalize for technology node or project performance to equivalent nodes.