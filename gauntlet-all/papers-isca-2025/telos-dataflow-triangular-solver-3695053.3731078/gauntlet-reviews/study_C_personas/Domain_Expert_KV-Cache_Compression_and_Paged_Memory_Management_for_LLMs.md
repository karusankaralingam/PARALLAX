# Paper Deconstruction: Telos

## Q1: Whiteboard Explanation

Let me sketch this out for you like we're at a whiteboard.

**The Problem:**
Imagine you're solving a physics simulation—heat flow, fluid dynamics, structural analysis. These are all Partial Differential Equations (PDEs). The standard approach discretizes your continuous world into a grid (say, a 256×256×256 cube), and at each grid point, you need to solve for a value that depends on its neighbors. This creates a *massive* sparse linear system: **Ax = b**.

The bottleneck? **Sparse Triangular Solve (SpTRSV)**. It's the "substitution" step where you solve one variable at a time, but each variable depends on previously computed ones. Think of it like a chain of dominoes—you can't topple domino #100 until dominoes #1-99 have fallen. This creates a brutal loop-carried dependency that GPUs *hate*.

**Why GPUs Struggle:**
Section 3 and Figure 4 lay this out clearly. On an RTX 2080Ti with cuSPARSE:
- Only **2.5% of peak throughput** achieved (Figure 4a)
- **50-63% of warps are stalled at barriers** (Figure 4b) waiting for dependencies
- Warp CPI (Cycles Per Instruction) is **27-37 cycles** (Figure 4c)—catastrophic latency hiding failure
- Only 25% warp occupancy

The fundamental issue: GPUs need *independent* work to hide memory latency. SpTRSV's dependencies murder this parallelism.

**Telos's Key Trick:**
Here's the napkin sketch. Take your 3D grid of points. Each point depends on neighbors in a *stencil pattern* (Figure 1 shows 7-point, 13-point, 27-point patterns). Telos exploits the **geometric regularity** of this structure.

1. **Hyperplane Decomposition**: Slice your 3D grid into 2D planes (hyperplanes) where x+y+z = constant. All points within one plane are independent of each other—they only depend on *previous* planes.

2. **Affine Transformation**: Some dependencies point "backwards" relative to these planes (Definition 2, Section 4.1.1). Algorithm 2 rotates the coordinate system so ALL dependencies point forward. For Diamond-13P stencil, (i,j,k) → (i, i+j, i+j+k).

3. **PE Array Mapping**: Map each hyperplane to a 2D mesh of Processing Elements (Figure 6b). Each PE handles points along the z-axis over time, resolving one variable per cycle and forwarding results to neighbors.

4. **Communication Aggregation**: Instead of each PE gathering data from distant sources, they **scatter** product terms to neighbors, aggregating partial sums along fixed paths (Figure 7, Algorithm 3). This transforms irregular long-range communication into nearest-neighbor systolic flow.

**The Result:**
A dataflow accelerator where:
- Synchronization is implicit (data-driven, not control-driven)
- Dependencies become pipeline stages
- Data reuse is maximized within and across PEs
- Memory access patterns are predictable

---

## Q2: The Key Insight

**The Real Innovation** is the recognition that **structured sparsity patterns from PDE discretization can be systematically transformed into systolic-style dataflow execution** through coordinate-space affine transformations.

This is a **mechanism innovation**, not just a new scheduling policy. Let me be precise:

**What's New vs. What's Known:**

*Known:* Level-set methods for SpTRSV parallelism (Section 2.3, Figure 3c). You analyze the dependency graph, find independent rows, and execute them in parallel batches with global synchronization between levels. This is what cuSPARSE and prior accelerators like Alrescha do.

*Known:* Dataflow accelerators for stencil computations exist (FDMAX [27], Spadix [26]). But they handle **matrix-free** iterative methods (Jacobi, Equation 3) where each iteration is embarrassingly parallel within a plane.

*The Gap:* SpTRSV and Gauss-Seidel (Equation 4) have **spatial dependencies within each iteration**—you need value x_{i-1,j,k} to compute x_{i,j,k}. Prior stencil accelerators "fail to address spatial dependencies across PEs" (Footnote 1, page 4).

**The New Mechanism:**

Telos introduces **plane-parallel pipelining** combined with **cross-plane communication aggregation**:

1. The **affine transformation** (Algorithm 2) is the mathematical trick that converts "backward" dependencies into "forward" ones. This isn't just reordering—it's a coordinate system rotation that preserves the stencil structure while enabling wavefront execution. For Box-27P: (i,j,k) → (i, i+j, 2i+j+k).

2. The **communication aggregation** (Algorithm 3, Section 4.2) reduces data movement from O(|stencil_points| × distance) to O(distinct_directions). For 27-point stencil, this is **5.3× reduction** in communication (page 7). The key insight: product terms traveling to the same destination can be accumulated along their shared path segments.

3. The **non-zero value packing** (Section 5.2, Figure 9b) interleaves coefficient groups from adjacent columns, enabling vector units to process multiple pipeline stages in parallel—a custom data layout for the transformed coordinate system.

**The "So What" Factor:**

Table 1 summarizes the delta: Telos is the **only** design that achieves data reuse (DR), spatial dependency support (SD), AND pipeline parallelism (PP) simultaneously. Prior work achieves at most two of three.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### **Strengths:**

**1. Appropriate Baselines for SpTRSV:**
The authors compare against **cuSPARSE** (vendor library), **AG-SpTRSV [23]** (TACO 2024, explicitly called "state-of-the-art GPU implementation"), and **Alrescha [1]** (HPCA 2020, "state-of-the-art SpTRSV accelerator"). This is a solid baseline selection—they didn't take the easy path of comparing against naive Hugging Face-style implementations.

**2. Roofline Analysis:**
Figure 13 shows **67-95% of roofline peak** for Telos vs. 2-13% for GPU. This is the right metric—it separates architectural efficiency from raw platform capability. Claiming "11× over cuSPARSE" means nothing if you're comparing a 400MHz ASIC to a 1.7GHz GPU; showing you're hitting your own roofline ceiling proves the design is sound.

**3. Energy Breakdown:**
Figure 14 decomposes energy into DRAM/SRAM/PE contributions for both Telos and Alrescha. The 16% average energy reduction (22% for 3D) is substantiated, not just asserted.

**4. Scalability Study:**
Figure 18 systematically varies PE array size (6×6 to 10×10), DRAM bandwidth (64-960 GB/s), and vector lanes (1-5). The weak scaling analysis (constant time with proportional grid/PE growth) validates the design doesn't have hidden bottlenecks.

**5. Open Source:**
Code available at https://github.com/pku-liang/telos. This is increasingly table stakes for ISCA but still noteworthy.

### **Weaknesses:**

**1. Simulated ASIC vs. Real Silicon GPU:**
Table 3 shows Telos is synthesized at **TSMC 28nm, 400MHz, 2.89W**. The RTX 3090 baseline is 350W at 8nm. The authors report **speedup** numbers without normalizing for area, power, or process node. The 11× over cuSPARSE is comparing:
- Custom ASIC designed specifically for this workload
- 28nm vs. 8nm (Telos has ~4× transistor density disadvantage)
- 2.89W vs. 350W

A fairer comparison would be GFLOPS/Watt or throughput per mm². The paper buries this by reporting only latency speedups.

**2. HBM2 Bandwidth Assumption Without Silicon:**
Section 6.1 states "We assume a memory bandwidth of 460 GB/s, matching that of HBM Gen2 memory." But Table 3 shows 7.95mm² chip area. Integrating HBM2 into a 7.95mm² die is... optimistic. The actual memory subsystem overhead isn't accounted for.

**3. Limited Problem Diversity:**
Table 2 shows 6 PDE benchmarks, but they're all textbook elliptic/parabolic/hyperbolic equations (Poisson, Heat, Laplace, Helmholtz, Advection, Diffusion). Real-world HPC applications often have:
- Adaptive mesh refinement (breaks structured assumption)
- Mixed boundary conditions
- Multi-physics coupling

The paper acknowledges targeting "structured problems" (Table 4), but doesn't discuss what percentage of real PDE workloads fit this constraint.

**4. FDMAX/Spadix Comparison is Indirect:**
Section 6.1 states "None of them has been open-sourced. Therefore, we model their behaviors and scale them to the same configuration as ours to offer a fair comparison." This is reasonable given constraints, but the 3.7×-7.9× speedups over FDMAX/Spadix (Figure 15a) partially come from algorithmic differences (CG-IC vs. Gauss-Seidel convergence), not just hardware efficiency. The Telos-GS comparison is fairer—and shows only ~1.2× speedup with 20% energy overhead for single iterations.

**5. No Accuracy/Convergence Quality Metrics:**
Unlike LLM papers where we'd check perplexity, numerical methods have condition number sensitivity, residual norms, and iteration counts. Figure 2a shows convergence curves, but only for the *algorithm* (CG-IC vs. Jacobi), not validating that Telos's hardware produces bit-identical results. For FP64 scientific computing, any precision shortcuts would be catastrophic.

---

## Q4: What the Authors Didn't Tell You

**1. The Preprocessing Overhead is Hidden:**
Algorithm 2 (Backward Dependency Elimination) and Algorithm 3 (Communication Path Assignment) generate the transformation matrix and routing tables. The paper claims this "can be automated with negligible overhead compared to levelset method" (page 6) but provides **no quantification**. For applications that solve different PDEs or change grids frequently, this compile-time cost matters.

**2. The 8×8 PE Array Choice is Suspiciously Convenient:**
Section 6.4 states "We configure an 8x8 PE array with 4 lanes in each PE to balance efficiency and resource usage." But Figure 18a shows performance scales linearly with PE count until compute-bound... so why stop at 8×8? The answer is likely in the bandwidth analysis (Figure 18b): at 460 GB/s, larger arrays would be memory-starved. The "design choice" is actually a constraint.

**3. Halo Exchange is an Ongoing Tax:**
Section 5.2-5.3 discuss Halo Exchange Units (HEUs) that handle boundary dependencies between tiles. Figure 16 shows reuse ratio drops to 80-85% for small tiles. What's not emphasized: **every tile boundary requires DRAM reads and writes** for halo updates. This is why "Telos-GS incurs an additional 20% energy overhead compared to the baselines in a single iteration" (Section 6.3). The structured sparsity wins inside tiles but pays at boundaries.

**4. The Sparse Matrix Storage Isn't Actually That Sparse:**
Section 5.2's "non-zero value packing" (Figure 9b) interleaves coefficient groups for efficient vector access. But this means the actual storage format is dense within the packed records. The paper doesn't report storage overhead compared to standard CSR/CSC formats. For very large grids, this could matter.

**5. 2D Problems Show Diminishing Returns:**
Figure 12 shows 24× average speedup for 2D vs. 5× for 3D over GPU. But look closer: for 3D Diamond-13P at size 256, Telos is only **~2.5× faster** than AG-SpTRSV (GPU). As problems scale, the GPU's parallel resources become better utilized (Figure 13's GPU utilization increases with problem size). Telos's advantage is most pronounced for smaller problems or 2D cases where GPU occupancy is poor.

**6. The Real Competitor Isn't Here:**
The paper positions against Alrescha, FDMAX, and GPUs. But the emerging competition is:
- **Wafer-scale processors** (Cerebras, cited as [41]) that throw massive parallelism at stencils
- **PIM architectures** (Table 4 lists pSyncPIM, ReFloat) that eliminate the memory bottleneck entirely
- **Tensor cores** for structured linear algebra (not discussed)

The related work section (Table 4) acknowledges these exist but doesn't benchmark against them.

**7. The CG-IC Solver's Iteration Count Isn't Reported:**
Figure 15a shows Telos-CG achieves "18.9× and 41.1× convergence rates" over FDMAX/Spadix. But this is conflating algorithm choice (CG-IC vs. Gauss-Seidel/Jacobi) with hardware efficiency. The fair hardware comparison is Telos-GS vs. FDMAX/Spadix, which shows only ~1.0-1.2× energy advantage. The paper's main claim of 7.9× speedup over Spadix is mostly algorithmic, not architectural.