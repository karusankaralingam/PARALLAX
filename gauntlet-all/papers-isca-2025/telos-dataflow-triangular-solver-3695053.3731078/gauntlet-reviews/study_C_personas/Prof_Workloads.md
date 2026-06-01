## Q1: Whiteboard Explanation

**Imagine you're solving a massive Sudoku puzzle, but each cell's value depends on cells you've already filled in.**

Telos solves a very specific computational bottleneck: Sparse Triangular Solve (SpTRSV) for Partial Differential Equations (PDEs).

**The Problem Chain:**
1. PDEs (like heat diffusion, fluid flow) are discretized into a grid
2. Each grid point relates to neighbors via a **stencil pattern** (e.g., 7-point star in 3D)
3. This creates a sparse matrix system Ax = b
4. Iterative solvers (like CG with preconditioning) need SpTRSV repeatedly
5. SpTRSV has **brutal sequential dependencies** — you can't solve row 7 until rows it depends on are done

**Why GPUs Fail (Figure 4):**
- Only 2.5% of peak throughput achieved
- 50-63% of warp time spent at synchronization barriers
- Warp CPI of 27-37 cycles (memory-bound hell)

**The Telos Insight:**
Instead of treating the matrix as "arbitrary sparse," exploit that PDE stencils create **predictable dependency patterns**. A 7-point stencil always connects the same relative neighbors.

**The Dataflow Design:**
1. **Affine Transformation (Algorithm 2):** Rotate coordinates so all dependencies point "forward" — no backward dependencies within a hyperplane
2. **Hyperplanes:** Group independent grid points that can execute in parallel
3. **PE Mapping:** Each PE handles a column of points along the z-axis, processing them in a pipeline
4. **Communication Aggregation (Algorithm 3):** Instead of scattering product terms everywhere, aggregate them along fixed paths between neighboring PEs only

**Hardware:** 8×8 PE array + Halo Exchange Units. Each PE has a scalar unit (division for solving), vector unit (parallel multiply-accumulates), and aggregator (combine & route partial sums).

---

## Q2: The Key Insight

**The Foundational Insight:**

The paper's core contribution is recognizing that **SpTRSV's "general sparse" treatment throws away exploitable structure**. When matrices come from PDE discretization, the sparsity pattern isn't random — it's a tiled, repeating stencil.

**Why This Matters:**

Table 1 (Section 3) crystallizes this: prior works like Alrescha, Azul, and LevelST support spatial dependencies but achieve **neither data reuse nor pipeline parallelism**. FDMAX achieves data reuse but **cannot handle spatial dependencies** (footnote 1 explicitly states this limitation).

**The Technical Enabler:**

The affine transformation (Section 4.1.1, Figure 5) is the linchpin. By rotating the coordinate system such that dependencies like (0,1,-1) become (0,1,0), they convert an irregular dependency DAG into a **wavefront** where entire hyperplanes can execute in parallel. This is mathematically elegant: transform the problem geometry rather than fight the dependencies.

**The Communication Innovation:**

Equation in Section 4.2 shows communication reduction: D_scatter = |{(x,y)|(x,y,z) ∈ S_tr}| versus D_gather = Σ(|x|+|y|). For Diamond-13P: 3 vs 7 (2.3× reduction). For Box-27P: 4 vs 21 (5.3× reduction). This converts irregular all-to-all communication into **systolic nearest-neighbor transfers**.

**Why Existing Approaches Miss This:**

- Level-set methods (GPU standard) identify independent rows but scatter them randomly across memory — terrible locality
- General sparse accelerators use caches hoping for hits — unpredictable, wastes area
- PDE-specific accelerators like FDMAX only handle Jacobi-style updates without intra-iteration dependencies

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Legitimate Baselines with State-of-the-Art Comparisons**

They compare against cuSPARSE (standard library), AG-SpTRSV [23] (explicitly called "state-of-the-art GPU implementation" published 2024), and Alrescha [1] (recent HPCA accelerator). This isn't a strawman setup. Figure 12 shows AG-SpTRSV sometimes beats cuSPARSE by 2-3×, proving it's a real competitor.

**2. Roofline Analysis Validates the Problem (Figure 4a)**

They show SpTRSV achieves only 2.5% of roofline-predicted throughput on GPU. This isn't cherry-picked — it's a fundamental characterization showing the kernel is neither compute-bound nor memory-bound in the traditional sense; it's **synchronization-bound**.

**3. End-to-End Solver Evaluation (Section 6.3, Figure 15)**

They don't just show kernel speedups. They run complete CG-IC solvers accounting for convergence rates. FDMAX uses Jacobi (slow convergence) while Telos uses CG-IC (18.9× faster convergence per their measurements). The 3.7× end-to-end speedup over FDMAX is honest about this tradeoff.

**4. Utilization Metrics (Figure 13)**

Showing 67-95% of peak throughput vs GPU's 2-13% is powerful evidence that the dataflow design eliminates bottlenecks.

### Weaknesses

**1. The Benchmark Selection is Narrow**

Table 2 shows only 6 PDEs, all on **structured grids**. Real-world CFD, FEM, and geophysics use unstructured meshes, adaptive mesh refinement, or multi-block structured grids with complex interfaces. The paper explicitly scopes to "structured sparsity patterns" (Table 1) but doesn't quantify what fraction of HPC workloads this covers.

**2. The "256^3 is Large" Assumption is Questionable**

Figure 12's largest 3D test is 256³ (~16M unknowns). Production PDE solvers in climate modeling or reservoir simulation routinely use 1000³+ grids (1B+ unknowns). The speedup diminishes from 12× at size 64 to 2.6× at size 256 over GPU. What happens at 512³? The scalability analysis (Section 6.6) only goes to 10×10 PEs — does this architecture hit a wall?

**3. Memory Bandwidth Assumption May Be Generous**

They assume 460 GB/s HBM2. But Figure 18a shows performance scales linearly with bandwidth until compute-bound. At their configuration, they're likely memory-bound for smaller stencils. The 8×8 PE array with 4 vector lanes might be **overprovisioned** for the memory system they model.

**4. Missing Comparison: What About Multi-GPU?**

A single RTX 3090 is compared against their accelerator. But SpTRSV at scale uses multi-GPU with domain decomposition. Their halo exchange mechanism (Section 5.2, Figure 10) handles tile boundaries but doesn't address distributed-memory scaling.

**5. The Alrescha Comparison Has Caveats**

Section 6.1 admits: "None of them has been open-sourced. Therefore, we model their behaviors and scale them to the same configuration as ours." This introduces potential bias. Alrescha might perform differently at its native design point.

**6. Figure 13's Y-Axis Framing**

Starting at 0% is correct, but notice the GPU bars (2-13%) are nearly invisible while Telos bars (67-95%) dominate visually. The 8× average speedup is real, but the visual exaggerates perceived difference.

---

## Q4: What the Authors Didn't Tell You

**1. The Affine Transformation Has Edge Cases**

Algorithm 2 works for convex stencils but what about asymmetric stencils from upwind schemes in advection-dominated problems? The paper tests "Star-13P" for advection (Table 2) but doesn't discuss whether the transformation handles all physically-motivated stencil asymmetries. Line 6 of Algorithm 2 requires `M(d, s_x+s_y+s_z) = ∅` — what happens when this conflicts?

**2. Preconditioning Quality Isn't Discussed**

They use IC(0) and IC(1) preconditioners, but IC factorization quality degrades on parallel machines due to ordering effects. Their plane-parallel execution **changes the effective ordering** of the triangular solve. Do they get the same convergence rate as sequential IC? Figure 2a shows convergence, but that's the baseline solver, not their reordered execution.

**3. The Halo Exchange Creates Hidden Memory Traffic**

Section 5.2 states: "neighboring tiles in memory can be updated with the halo value." This means **every tile boundary requires memory writes**. For small tiles (needed to fit in the PE array), the halo-to-interior ratio is poor. Figure 16 shows reuse ratio drops to 80-85% at tile size 4-5. They chose tile size 9+ in evaluation — what's the actual tile size used for 256³ problems?

**4. FP64 Precision Impact**

Table 3 shows their default is FP64, but the area/power is synthesized for FP64 multiply-accumulate in each PE. FP64 dividers are expensive — the paper doesn't break down what fraction of PE area is the scalar division unit. If division is the bottleneck (Section 6.6 mentions "critical path is determined by division"), why not pipeline it more aggressively?

**5. The "61× over CPU" Number is Misleading**

The abstract claims "61× over CPU" but this compares an 8×8 PE ASIC against a general-purpose CPU running PETSc. A fairer comparison would be against a CPU solver with the same stencil-awareness (like [5] Mat2Stencil cited in Table 4). The GPU comparison (8× over AG-SpTRSV) is the more meaningful metric.

**6. What About Numerical Stability?**

Aggregating partial products in different orders (systolic accumulation vs. direct summation) can affect floating-point round-off. For iterative solvers this usually doesn't matter, but for ill-conditioned problems it might. No discussion of numerical reproducibility.

**7. Power Comparison Omission**

Table 3 shows 2.89W for Telos. An RTX 3090 is ~350W TDP. Even at 8× speedup, Telos would be ~100× more energy-efficient, yet Section 6.2 doesn't highlight this. The energy comparison in Figure 14 only compares against Alrescha (another accelerator), not against GPUs.