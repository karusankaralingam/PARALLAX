# Neo: Towards Efficient FHE Acceleration using Tensor Core — A Toolsmith's Analysis

## Q1: Whiteboard Explanation

Let me walk you through what Neo is actually doing on real NVIDIA hardware.

**The Problem Setup:**
Fully Homomorphic Encryption (FHE) lets you compute on encrypted data, but it's computationally brutal. The CKKS scheme encrypts data as polynomials of degree N=2^16 with coefficients stored in a Residue Number System across multiple "limbs." The killer operation is **KeySwitch**, which dominates runtime in HMULT and HROTATE operations.

**The Baseline Pain:**
Prior GPU work (TensorFHE) used the INT8 components of Tensor Cores for NTT acceleration. But here's the catch: CKKS with practical precision needs 36-bit WordSize (per SHARP[25]), which means you can't fit coefficients in 32-bit integers. When you go to 64-bit computation on CUDA cores, you lose half your throughput. Meanwhile, INT8 TCU acceleration requires Booth decomposition—splitting 36-bit values into five 8-bit chunks, computing 25 partial products, then merging. That's expensive.

**Neo's Core Moves:**

1. **Adopt KLSS KeySwitch method**: Instead of the Hybrid method that works in ring R_PQ, KLSS moves computation to an auxiliary ring R_T with selectable WordSize_T. This reduces algorithmic complexity (see Table 2) but introduces new kernels.

2. **Transform BConv and IP into matrix multiplication**: The original BConv and IP kernels perform element-wise multiplications with poor data reuse—each coefficient gets fetched from global memory α' times (BConv) or β̃ times (IP). Neo reorganizes data layouts to batch these into GEMM operations where data stays resident in local memory during accumulation.

3. **Map to FP64 TCU components**: The A100's TCU has both INT8 and FP64 datapaths (Figure 1). For 36-bit computation, FP64 needs only 3 partial GEMMs (since 53-bit mantissa can hold intermediate products up to 2^52), while INT8 needs 25. Figure 3 shows FP64 is 1.65× faster at WS=36.

4. **Radix-16 NTT**: Reduces matrix multiplication complexity from 2^25 to 2^22 by decomposing into four smaller transforms rather than two.

**The Data Flow (Figure 4):**
- BConv/NTT/IP: Split & Reorder → Matrix Mult (on TCU FP64) → Reorder & Merge
- ModMUL/ModADD/AUTO: CUDA Cores only
- IP has conditional mapping: TCU if valid computation proportion >80%, else CUDA Cores

---

## Q2: The Key Insight

The central insight is **inverting the optimization target for Tensor Core utilization in FHE workloads**.

Prior work assumed "more TOPS = better" and chased the INT8 components (624 TFLOPS on A100) over FP64 (19.5 TFLOPS). But this ignores the **Booth complexity tax**: when your data width exceeds the native precision, you pay in decomposition overhead, not just FLOPS. For 36-bit FHE coefficients, INT8 requires O(n²) partial products while FP64 requires O(1) effective operations per multiplication.

The deeper insight is that **memory transfer dominates FHE kernels**, not compute. Figure 2 shows BConv and IP together consume 85% of KeySwitch's global memory traffic. By transforming element-wise operations into matrix multiplications, Neo exploits the TCU's implicit data reuse—fragments stay in register files across the accumulation dimension K. This trades compute overhead (preprocessing/postprocessing) for memory bandwidth savings.

Section 3.4 quantifies this: at WordSize=36, FP64 GEMM (including split/merge) runs 1.65× faster than INT8 GEMM. At WordSize=48, the gap widens to 1.74×. The fragment shape mismatch compounds this—INT8 fragments (16×16×16) require padding for the small K dimensions in BConv (α=4-8), wasting 75% of computation (Figure 11).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Real Hardware Execution (Table 3):**
Neo runs on actual NVIDIA A100 hardware with CUDA 11.3, not a simulator. This is critical—TCU behavior is notoriously difficult to model accurately due to undocumented fragment scheduling and memory coalescing effects. The authors use nvprof-accessible metrics (execution time) rather than simulated cycle counts.

**2. End-to-End Application Benchmarks (Table 5):**
They evaluate three meaningful FHE applications: PackBootstrap, HELR (logistic regression training), and ResNet-20/32/56 inference. These represent real workloads with varied operation mixes. The 3.28× speedup over TensorFHE's best configuration (not just apples-to-apples parameters) is honest reporting.

**3. Memory Transfer Analysis (Figure 2, Figure 15):**
The authors actually measure global memory requirements at different ciphertext levels and show how their optimizations reduce I/O. Figure 15(b) shows application-level reductions—PackBootstrap drops from 144GB to ~50GB after optimization. This is more meaningful than theoretical complexity analysis.

**4. Ablation Study (Figure 14):**
The incremental breakdown (KLSS → dataflow optimization → Radix-16 NTT → FP64 TCU) lets readers attribute speedups to specific techniques. Each contribution is isolated.

**5. Sensitivity Studies (Table 8, Figure 16):**
They explore the d_num × α̃ parameter space and WordSize_T tradeoff, showing the optimization surface rather than just cherry-picking best points.

### Weaknesses

**1. No Profiler Breakdowns:**
The paper lacks Nsight Compute or nvprof analysis showing TCU utilization rates, memory bandwidth achieved, or SM occupancy. Figure 3 shows wall-clock time for split/GEMM/merge but doesn't report achieved TFLOPS vs. peak. *How close to the 19.5 TFLOPS FP64 ceiling are they actually running?* Without this, we can't distinguish algorithmic wins from implementation quality.

**2. HEonGPU Comparison is Incomplete:**
Table 5 shows Neo beats HEonGPU by 19.9% average, but HEonGPU uses parameter Set-E while Neo uses Set-C/D. The footnote admits Set-D matches HEonGPU's parameters, but then Neo is *slower* than its own Set-C configuration (13.39s vs 12.03s for ResNet-20). This suggests Neo's advantage over HEonGPU may be parameter-dependent rather than fundamental.

**3. No Correctness Validation Details:**
FHE with approximate arithmetic (CKKS) is precision-sensitive. Section 2.1 mentions "controlled noise growth" and Section 3.2 references SHARP's precision requirements, but there's no validation that Neo's outputs match bit-for-bit (or within acceptable error bounds) with reference implementations. The DS (Double Rescale) usage is mentioned but its overhead isn't isolated.

**4. BatchSize=128 is Suspiciously Convenient:**
Figure 17 shows execution time drops dramatically from BS=8 to BS=128, but the paper doesn't explain *why* 128 is the maximum. Is it A100 VRAM (40GB)? At what point does batching saturate the TCU? They state "limitations of GPGPU memory capacity" but don't quantify the memory footprint per ciphertext at different parameter sets.

**5. No Multi-GPU or PCIe Transfer Analysis:**
Table 5 claims practical relevance, but real deployments involve data movement to/from GPUs. The total data per application (Table 4 shows L=35-44 levels) exceeds VRAM, yet there's no discussion of paging or multi-GPU partitioning.

**6. Warmup and Variance Not Reported:**
Execution times are given without error bars, confidence intervals, or information about measurement methodology. How many trials? Was warmup performed? CUDA kernel timing can vary significantly due to frequency scaling and memory residency.

---

## Q4: What the Authors Didn't Tell You

**1. The FP64 TCU is Underutilized by Design:**
Figure 1 shows the TCU architecture, but here's what's hidden: the FP64 datapath shares silicon with the FP32/TF32 path in Ampere, and running FP64 GEMM on TCU actually *disables* FP32 throughput. The paper doesn't discuss whether mixing FP64 TCU with FP32 CUDA cores (for preprocessing) creates pipeline bubbles or SM scheduling conflicts.

**2. The KLSS Method Isn't Free:**
Table 2 shows KLSS reduces some operations but *increases* IP complexity from 2β(l+α) to β̃βα'. The paper claims "judicious parameter selection" makes KLSS better (Section 2.2), but Table 8's sensitivity study suggests the optimal point is narrow—performance varies by 1.7× across the parameter space. They found good parameters; whether these generalize to other applications or security requirements is unclear.

**3. Fragment Padding Affects More Than Just Compute:**
Figure 11 discusses "valid proportion" of computation, but padding also affects register pressure and shared memory layout. The 8×8×4 FP64 fragments are awkward for the 16-element α dimension—they need two fragments per K-slice. This data marshaling overhead isn't quantified.

**4. The Evaluation Keys Dominate Memory:**
Section 2.3 mentions IP requires "ββ̃α' polynomial keys" per KeySwitch variant. For HROTATE with multiple rotation indices, this means pre-computing and storing massive key sets. At N=2^16 with 64-bit coefficients, one polynomial is 512KB. With β̃βα' potentially reaching hundreds, evaluation keys alone can consume gigabytes. The BatchSize=128 limit likely reflects this, not ciphertext storage.

**5. No Comparison Against Dedicated Accelerators:**
Table 5 compares against TensorFHE and HEonGPU (both GPU-based) and mentions CPU baselines from Craterlake[40]. But Craterlake, BTS[30], Taiyi[11], and SHARP[25] are ASIC designs claiming 1000×+ speedups over CPU. The paper argues GPUs are "practical" (Section 3.1), but doesn't quantify the gap to custom silicon. This is a positioning paper, not a claim of SOTA absolute performance.

**6. The Radix-16 NTT is Borrowed:**
Section 4.4 states "We have employed a Radix-16 NTT method from SHARP[25]". The algorithmic contribution is the GPU mapping (butterfly-to-GEMM transformation), not the NTT structure itself. This reduces the novelty claim but is appropriately cited.

**7. Artifact Availability is Absent:**
Despite the extensive evaluation, there's no link to source code, Dockerfiles, or reproducibility artifacts. "Paperware" is risky for GPU work where CUDA version, driver version, and compile flags significantly affect performance.