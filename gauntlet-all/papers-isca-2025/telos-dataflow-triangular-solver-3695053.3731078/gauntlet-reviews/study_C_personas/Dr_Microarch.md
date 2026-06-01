## Q1: Whiteboard Explanation

Let me walk you through the actual hardware mechanism of Telos by reverse-engineering Figure 8 and the dataflow design in Sections 4-5.

**The Problem Being Solved:**
SpTRSV (Sparse Triangular Solve) computes `x = L⁻¹b` where L is a lower triangular sparse matrix derived from PDE discretization. The core loop (Algorithm 1) is: for each row i, compute a partial sum of `a_ij * x_j` for all j < i where `a_ij ≠ 0`, then solve `x_i = (b_i - sum) / a_ii`.

**The Structural Insight:**
PDEs discretized on structured grids produce sparse matrices with *predictable* non-zero patterns (Figure 1). A 7-point stencil creates exactly 7 non-zeros per row at fixed offsets. The authors exploit this regularity to transform a sparse matrix problem into a geometric wavefront problem.

**The Dataflow Pipeline (Figure 6-7):**

1. **Affine Transformation (Algorithm 2):** Grid points are reindexed so that dependencies always point "forward" in the transformed coordinate system. For Diamond-13P, point (i,j,k) becomes (i, i+j, i+j+k). This eliminates backward dependencies within hyperplanes.

2. **Hyperplane Scheduling:** Points satisfying `x' + y' + z' = n` form the n-th wavefront. All points in a wavefront are independent and map to different PEs.

3. **PE Assignment:** A 2D PE array processes the x-y plane. Each PE handles one column along the z-axis over time. PE(i,j) processes all points (i,j,k) for k=0,1,2,...

**Inside Each PE (Figure 9c):**

- **Scalar Unit:** Computes one `x_ijk = (b_ijk - sum) / a_diag` per cycle using accumulated partial sums
- **Vector Unit:** Multiplies the newly-solved variable against a *packed vector* of coefficients for multiple dependency distances simultaneously
- **Aggregator:** Combines vector lane outputs and routes partial products to neighboring PEs via configurable output ports

**The Packing Trick (Figure 9b):**
Non-zeros from *different* columns are interleaved into one "record." Record 0 contains: distance-1 elements from column 002, distance-2 from column 001, distance-3 from column 000. This enables the vector unit to process multiple pipeline stages in parallel while variables stream through a FIFO.

**Cross-PE Communication (Algorithm 3, Figure 7):**
Product terms are *scattered* (not gathered) and aggregated along fixed paths. A term `n^{000}_{(1,1,0)}` combines with `n^{100}_{(0,1,0)}` at PE(1,0) before reaching its destination. This converts O(|stencil| × distance) communication into O(distinct_directions) = 3-4 paths.

---

## Q2: The Key Insight

**The "Magic Trick":**

The fundamental insight is that **structured sparsity from PDE stencils enables predictable communication patterns that can be compiled into fixed systolic datapaths, eliminating runtime dependency tracking entirely.**

Specifically:
1. **Geometric Transformation Enables Static Scheduling:** The affine transformation (Section 4.1.1) rotates the coordinate system so that *all* stencil dependencies point to subsequent hyperplanes. This is computed once per stencil type, not per matrix. The insight is that backward dependencies in SpTRSV always follow the stencil geometry—so a coordinate rotation can make them all "forward."

2. **Communication Aggregation Replaces Routing:** Instead of point-to-point communication for each dependency (which would require O(stencil_points × grid_size) transfers), the authors observe that partial products traveling in the same direction can be *accumulated* along the path. This reduces communication from `D_gather = Σ(|x|+|y|)` to `D_scatter = |distinct xy-projections|`—a 2.3× reduction for Diamond-13P and 5.3× for Box-27P (Section 4.2).

3. **Value Packing Exploits FIFO Structure:** Variables processed sequentially within a PE can have their coefficient vectors interleaved (Figure 9b), allowing a single vector multiply to service multiple pipeline stages simultaneously.

**Why This Matters Architecturally:**

The GPU baseline achieves only 2.5% of roofline (Figure 4a) because:
- Level-set methods require global synchronization between levels
- Warp occupancy is ~25% due to dependency-limited parallelism
- 50-63% of warp cycles are stalled at barriers (Figure 4b)

Telos eliminates all three: no explicit synchronization (dataflow), full pipeline occupancy (wavefronts provide continuous work), and nearest-neighbor communication only (no memory system involvement for dependencies).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive Baseline Comparison:** The authors compare against CPU (PETSc), GPU (cuSPARSE, AG-SpTRSV), and ASIC (Alrescha). The 11× speedup over Alrescha (Figure 12) is meaningful because Alrescha is a recent HPCA'20 SpTRSV accelerator.

2. **Roofline-Aware Analysis:** Figure 13 shows Telos achieves 67-95% of roofline while GPUs achieve 2-13%. This demonstrates the dataflow design genuinely addresses the memory/compute imbalance.

3. **End-to-End Solver Evaluation:** Section 6.3 evaluates full CG-IC solver performance, accounting for convergence rates. The 3.7-7.9× speedup over FDMAX/Spadix validates that SpTRSV acceleration translates to solver-level gains.

4. **Scalability Analysis:** Figure 18a shows linear scaling with bandwidth until compute-bound, and weak scaling analysis confirms constant time with proportional PE/grid scaling.

5. **Reuse Ratio Quantification:** Figure 16 and Equation 5 provide analytical backing for data reuse claims (>90% for tile sizes ≥9).

**Weaknesses:**

1. **Limited Stencil Diversity:** All benchmarks use regular grids with 5-27 point stencils (Table 2). Real PDE applications often involve:
   - Variable-coefficient problems (different stencil weights per point)
   - Adaptive mesh refinement (irregular tile boundaries)
   - Higher-order schemes (e.g., 125-point stencils)
   The paper does not evaluate these cases.

2. **No Memory System Modeling Detail:** The authors "assume 460 GB/s" bandwidth (Section 6.1) matching HBM2. However:
   - No analysis of bank conflicts in the highly-banked DomainSpMat buffer
   - Halo exchange requires read-modify-write to DRAM (Figure 10) whose latency is not characterized
   - Double-buffering overhead not quantified

3. **Tile Pipelining Gains Unclear:** Figure 17a shows only 1.16× speedup from tile pipelining for 3D problems. For the target 256³ grids, the technique provides marginal benefit, yet the paper emphasizes it as a key contribution.

4. **Comparison Fairness Concerns:**
   - Alrescha is modeled, not measured (Section 6.1: "we model their behaviors")
   - FDMAX/Spadix comparison uses FP32 for baselines vs. FP64 default for Telos (Section 6.1)
   - AG-SpTRSV is evaluated on RTX3090 while profiling (Figure 4) uses RTX2080Ti

5. **Area/Power Context Missing:** Table 3 reports 7.95mm² at 28nm, but no comparison to GPU die area or power (350W TDP for RTX3090 vs. 2.89W for Telos). The efficiency comparison should be energy-per-solve, not raw speedup.

---

## Q4: What the Authors Didn't Tell You

**Hidden Hardware Costs:**

1. **The Divider in Every PE:** Each PE contains a scalar division unit (Section 5.2, Figure 9c). FP64 dividers are expensive—typically 10-15× the area of a multiplier and multi-cycle latency. With 64 PEs (8×8 array), this represents significant silicon. The paper states "the critical path is determined by the division operation" (Section 6.6) but doesn't quantify divider latency or area.

2. **Aggregator Complexity:** The aggregator must support configurable `aggregate(lane_i:j, port_in, port_out)` primitives (Section 5.2). For Box-27P, "13 product terms are combined using four primitives, with results forwarded through all four output ports." This implies a reconfigurable reduction tree with 4 input ports, 4 output ports, and lane selection logic—non-trivial combinational logic.

3. **Buffer Banking Requirements:** The DomainSpMat buffer (512KB) must support "simultaneous reading of short vectors for parallel PEs" (Section 5.1). With 64 PEs each needing ~6-13 elements per cycle (stencil size), this requires 64-way banking or multi-ported SRAM. The CACTI area estimate may undercount this.

**Algorithmic Limitations:**

4. **Affine Transformation Constraints:** Algorithm 2 assumes all backward dependencies can be eliminated by two rotations. This works for symmetric stencils but may fail for:
   - Upwind schemes (asymmetric stencils)
   - Multi-physics coupling (non-local dependencies)
   - The algorithm has no fallback if transformation fails

5. **Fixed PE-Grid Mapping:** The one-to-one mapping of grid points to PEs (Section 6.2) means Star-7P/13P "achieves suboptimal vector unit utilization" because "each level lacks sufficient computations for full utilization." This is a fundamental limitation for sparser stencils.

**Missing System Integration Details:**

6. **Host Interface Overhead:** No discussion of how tiles are scheduled from CPU/host, how configuration is loaded, or PCIe/interconnect latency for streaming matrices to the accelerator.

7. **Multi-Solve Amortization:** PDE solvers invoke SpTRSV thousands of times (one per CG iteration). The paper doesn't discuss whether the non-zero packing (Figure 9b) is done once or per-solve, though it should be amortized.

8. **Precision Sensitivity:** Section 6.3 mentions "Telos-GS incurs an additional 20% energy overhead compared to baselines in a single iteration due to halo region updates." This suggests the halo update mechanism (write-back to DRAM) may become a bottleneck for methods requiring frequent boundary exchange.