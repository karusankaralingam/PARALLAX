## Q1: Whiteboard Explanation

**The Problem Telos Solves:**

Imagine you're solving a 3D Poisson equation (heat distribution, fluid flow, etc.) on a grid. You discretize it using finite differences, creating a sparse linear system Ax = b. The matrix A has a *structured sparsity pattern* determined by the stencil (e.g., 7-point star, 13-point diamond, 27-point box — see Figure 1).

To solve this efficiently, you use preconditioned iterative solvers (like CG with Incomplete Cholesky). The bottleneck? **SpTRSV** — sparse triangular solve — which consumes 72-81% of execution time (Figure 2b). SpTRSV has nasty loop-carried dependencies: you can't compute x_i until you've computed all x_j where a_ij ≠ 0.

**Why GPUs Struggle:**

GPUs achieve only ~2.5% of peak throughput on SpTRSV (Figure 4a). Why?
1. **Synchronization hell**: 50-63% of warp time is stalled at barriers (Figure 4b)
2. **Memory latency**: 27-37 cycles per instruction due to dependency-induced serialization (Figure 4c)
3. **Low occupancy**: Only 25% warp occupancy because you can't find enough independent work

**The Telos Approach:**

The key insight is that PDE stencils create *predictable* dependency patterns. Telos exploits this structure through:

1. **Affine Transformation** (Algorithm 2): Rotate the coordinate system so all dependencies point "forward" — eliminating backward dependencies within hyperplanes. For Diamond-13P, this transforms point (i,j,k) → (i, i+j, i+j+k).

2. **Plane-Parallel Pipelining**: Points within a hyperplane (x+y+z = n) are independent and can execute in parallel across PEs. Adjacent hyperplanes execute in pipelined stages (Figure 6).

3. **Communication Aggregation** (Algorithm 3): Instead of gathering values from scattered locations, scatter product terms and aggregate them along systolic paths. This reduces communication overhead from 7 to 3 transfers for Diamond-13P (Section 4.2).

4. **Non-zero Value Packing** (Figure 9): Interleave coefficients from adjacent columns by distance groups, enabling vector units to process multiple stages simultaneously.

**Architecture:** 8×8 PE array with 4 vector lanes per PE, two Halo Exchange Units (HEUs) for boundary handling, and banked buffers for parallel access.

---

## Q2: The Key Insight

**The Distilled Contribution:**

The paper's core insight is that **structured sparsity from PDE stencils can be systematically transformed into systolic-style dataflow execution**, avoiding the synchronization and communication overhead that plagues general-purpose SpTRSV implementations.

Specifically, the authors recognize that:
1. Stencil patterns create geometric dependency structures in 3D space
2. An affine coordinate transformation can align all dependencies with hyperplane wavefronts
3. Product terms sharing destinations can be aggregated along fixed communication paths, reducing inter-PE traffic from O(|stencil points| × distances) to O(distinct directions)

**Why This Matters:**

Prior work faced a fundamental trade-off: extracting parallelism required scattering variables across processors (killing locality), while preserving locality forced sequential execution. The level-set method (Figure 3c) exemplifies this — it finds independent rows but they're spatially scattered.

Telos breaks this trade-off by exploiting *geometric locality within stencils*. The affine transformation is elegant: for Diamond-13P, backward dependencies like (0,1,-1), (1,-1,0), (1,0,-1) become forward-pointing after rotation (Figure 5). This isn't generic graph analysis — it's domain-specific exploitation of PDE structure.

**Comparison to Alternatives:**
- **FDMAX/Spadix** [26,27]: Exploit stencil patterns but only for matrix-free methods (Jacobi iteration). They can't handle the spatial dependencies in Gauss-Seidel or SpTRSV.
- **Alrescha** [1]: Handles SpTRSV but treats matrices as unstructured, requiring general dependency analysis and losing locality.
- **Telos**: First to combine structured sparsity awareness with SpTRSV support (Table 1).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Comprehensive Baseline Coverage**
The evaluation compares against CPU (PETSc), GPU (cuSPARSE v12.1, AG-SpTRSV [23]), and ASIC (Alrescha [1], FDMAX [27], Spadix [26]). Figure 12 shows consistent wins: 61× over CPU, 11× over cuSPARSE, 8× over AG-SpTRSV, 11× over Alrescha on average.

**S2: Roofline Analysis Validates Efficiency**
Figure 13 is compelling: Telos achieves 67-95% of peak throughput across stencil types, versus 2-13% for GPU. This directly validates the dataflow design addresses the memory/synchronization bottleneck.

**S3: End-to-End Solver Evaluation**
Section 6.3 evaluates complete PDE solving, not just kernel speedup. The CG-IC solver achieves 18.9× faster convergence than FDMAX's Jacobi (Figure 15a), demonstrating the practical value of supporting advanced preconditioners.

**S4: Energy Breakdown Analysis**
Figure 14 shows 16-22% energy reduction versus Alrescha. The breakdown reveals DRAM energy dominates for both, but Telos's predictable access patterns improve efficiency.

**S5: Scalability Studies**
Figure 18 demonstrates linear scaling with memory bandwidth until compute-bound, and weak scaling analysis shows <1% variation in processing time when scaling PE array and grid proportionally.

### Weaknesses

**W1: Simulation-Only Evaluation**
The entire evaluation uses a "cycle-accurate simulator" (Section 6.1). There's no RTL implementation running on an FPGA, no silicon, and no tape-out. The RTL was synthesized for area/power estimates, but timing closure at 400MHz in TSMC 28nm is assumed without validation. This is classic "simulation is doomed to succeed" territory.

**W2: Memory System Modeling Questions**
They "assume a memory bandwidth of 460 GB/s, matching that of HBM Gen2 memory" (Section 6.1). But:
- No mention of HBM controller complexity or its area/power
- DRAM refresh overhead isn't discussed
- DRAMSim3 energy estimation assumes idealized access patterns

**W3: Limited Stencil Diversity**
Table 2 shows only 6 stencil patterns, all regular (structured grids). Real PDE applications often use:
- Adaptive mesh refinement (AMR) with varying resolution
- Unstructured meshes near boundaries
- Higher-order stencils with more points
The claim of "general numerical schemes" (Table 1) is oversold.

**W4: Missing Comparison Points**
- No comparison against FPGA implementations like LevelST [20] for SpTRSV
- Alrescha comparison uses a "modeled" implementation scaled to same config (Section 6.1) — not original hardware
- The 28nm technology node is outdated; comparisons to modern GPUs (RTX3090 at 8nm) aren't iso-technology

**W5: Halo Region Overhead Underexplored**
Section 5.3 mentions halo regions add "negligible memory overhead," but Figure 15b shows Telos-GS has 20% higher energy than baselines for single iterations due to halo updates. For large-scale problems with many tiles, this could accumulate.

**W6: No Warm-up or Variability Analysis**
The simulator results show single data points with no confidence intervals, no discussion of warm-up periods, and no analysis of performance variability across different matrix instances of the same stencil type.

---

## Q4: What the Authors Didn't Tell You

**The Simulation Gap:**

The entire performance story rests on a cycle-accurate simulator they developed. Key missing details:
- **Simulator validation**: Was it validated against any RTL simulation? Section 6.1 only says it "captures micro-architectural behaviors" — that's an assertion, not validation.
- **Memory timing**: They use DRAMSim3 for energy but unclear if it's integrated into cycle-accurate timing. HBM has complex scheduling with bank groups, refresh, and thermal throttling.
- **Interconnect modeling**: The 8×8 PE mesh communication is assumed to have fixed latency, but physical routing in 28nm for a 7.95mm² die matters.

**The "ASIC" Baseline is Paper-Only:**

Section 6.1 states: "None of them has been open-sourced. Therefore, we model their behaviors and scale them to the same configuration as ours." This means:
- Alrescha [1] speedup is modeled Telos vs. modeled Alrescha
- FDMAX/Spadix comparisons are similarly synthetic
- No actual measurements from published hardware

**Hidden Assumptions in the Dataflow:**

1. **Affine transformation overhead**: Algorithm 2 runs at compile time, but the transformed coordinates change memory layout. How is the sparse matrix re-indexed? This preprocessing cost isn't evaluated.

2. **Fixed stencil assumption**: The communication path assignment (Algorithm 3) is precomputed per stencil type. Changing stencils requires reconfiguration — the "flexibility" claim needs caveats.

3. **Perfect double buffering**: Section 5.1 claims buffers operate in "double buffering manner" allowing streaming "without repetitive accesses." This assumes tile sizes fit in 672KB total buffer (128KB + 512KB + 32KB from Table 3) with perfect overlap timing.

**What About Real Systems?**

The paper doesn't address:
- **Integration with host CPU**: How does data get to/from the accelerator? PCIe latency for small problems?
- **Multi-accelerator scaling**: What about problems larger than a single chip's capacity?
- **Precision beyond FP64**: They mention FP32 "to match" FDMAX/Spadix, but many scientific applications need mixed precision or extended precision.

**The 400MHz Question:**

Table 3 claims 400MHz on TSMC 28nm. This is conservative for arithmetic units but the question is whether the complex aggregator datapath and interconnect close timing. Synthesis results for target frequency vs. achieved frequency would be informative.

**Artifact Availability:**

The authors claim code is at `github.com/pku-liang/telos`. This is good for reproducibility, but the critical artifact is the cycle-accurate simulator, not just the RTL. Without the simulator, the performance claims can't be independently verified.