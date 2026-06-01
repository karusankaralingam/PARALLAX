## Q1: Whiteboard Explanation

Alright, let me break down what FACE is actually doing here, from a toolsmith's perspective.

**The Problem Setup:**
We have wafer-scale chips—think of a 12-inch wafer (215mm × 215mm) packed with multiple compute dies, each with its own HBM chiplets, all connected via a 2D-mesh Die-to-Die (D2D) network. The challenge is running LLM inference efficiently on this beast.

**The Core Insight:**
LLM inference has two phases with fundamentally different characteristics:
- **Prefill**: Compute-bound (processing the entire input sequence)
- **Decode**: Memory-bound (generating tokens one-by-one, accessing KV cache)

Previous approaches like WSC-LLM used *disaggregated scheduling*—separate prefill and decode instances. But this creates placement headaches on a 2D-mesh topology (tail latency from distant KV cache transfers, suboptimal resource ratios, etc.).

**FACE's Solution:**
Instead of segregating, they *overlap* prefill and decode execution within each instance. The key enabler is wafer-scale chips' **fine-grained control**—you can independently manage each core's controller and DMA engine to precisely partition compute tiles between phases.

**The Framework (Figure 6):**
1. **Configuration Space Exploration (CSE)**: Offline exploration to find viable tile sizes for concurrent prefill/decode attention that fit in SRAM. Produces a Look-Up Table (LUT).
2. **Dynamic Adaptive Scheduling (DAS)**: Runtime scheduler using the LUT to allocate incoming requests. Prefill engine uses chunk-based scheduling; decode engine finds the instance that minimizes incremental latency.
3. **Optimized Memory Management (OMM)**: Leverages the D2D bandwidth (often > DRAM bandwidth) to allow flexible KV cache placement across instances without congestion.

**The Architecture Co-Exploration:**
They also explore the microarchitecture (core SRAM, compute, NoC) and architecture (die size, HBM count, D2D bandwidth) design space, ultimately landing on large dies with maximum HBM chiplets (case 10 in Table I).

---

## Q2: The Key Insight

The fundamental insight is elegantly simple but non-obvious: **wafer-scale chips' fine-grained operational control enables fully overlapped prefill-decode execution, eliminating the prefill-decode interference that plagues both unified and disaggregated scheduling on GPUs.**

This is articulated in Section III-C and Figure 5. The authors recognize that:

1. **Unified scheduling** (vLLM-style) serializes prefill and decode phases, causing interference
2. **Disaggregated scheduling** (WSC-LLM-style) separates them spatially but creates topological constraints, tail latency, and leaves decode instances with <9% compute utilization (Figure 4(b))
3. **Wafer-scale chips are different**: The NPU-like architecture supports "dynamic tensor decomposition and PE-level execution control" (Section II-B2), meaning you can partition attention computation at the tile level and run both phases concurrently

The key technical enabler is that the host can "directly manage the controller and DMA engine of each core to precisely regulate the sizes of prefill and decode tiles" (Section III-C). This isn't possible on GPUs where the execution model is more coarse-grained.

What makes this insight valuable is that it turns a perceived limitation (having to choose between unified vs. disaggregated) into an opportunity by exploiting architectural features specific to wafer-scale designs.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Design Space Coverage:**
The evaluation explores a substantial parameter space (Table I): 7 SRAM sizes, 14 compute configurations, 5 NoC bandwidths, multiple core array sizes, and 10 architecture cases. Figure 13 and 14 present systematic sweeps across this space, which is rare and valuable.

**2. Real-World Workloads:**
They use Azure production traces (Section V-A3) with realistic distributions—code dataset (2.57 req/s, 3-7437 input tokens) and conversation dataset (5.53 req/s, 2-14050 input tokens). This is far better than synthetic benchmarks.

**3. Hardware-Calibrated Models:**
Section IV-F explicitly states the evaluator is "calibrated with actual data collected from a representative NPU device" and DRAM access is "modeled based on real HBM hardware [2]." This adds credibility to the analytical modeling.

**4. Multiple Baselines:**
They compare against W-Sch (WSC-LLM's disaggregated), U-Sch (unified), and vLLM on GPU clusters. The 3.68× average improvement over WSC-LLM and 7.23× over vLLM (Figures 11, 12) across 6 model-dataset combinations is substantial.

### Weaknesses

**1. Simulation Infrastructure Opacity:**
The paper states the evaluator is "modified based on WSC-LLM" and uses ASTRA-sim for D2D communication (Section IV-F). However, there's no clarity on:
- Whether this is cycle-accurate or analytical modeling
- What the warm-up period is for trace-driven simulation
- How context switches or OS overhead are handled
- Whether DRAM refresh is modeled

This is a significant gap. Saying models are "calibrated" with real hardware isn't the same as validating the full simulation infrastructure against RTL or silicon.

**2. No Artifact or Reproducibility:**
There's no mention of open-sourced code, Docker containers, or public artifacts. The parameter exploration (Table I) involves complex co-design, but without artifacts, this is effectively "paperware."

**3. Idealized Assumptions:**
- **Equation 1** assumes D2D links have no contention if distance ≤ D2D_BW/DRAM_BW. This is a best-case analysis under time-division multiplexing—real contention patterns are workload-dependent.
- The LUT-based scheduling (Section IV-C2) uses "minimum Euclidean distance" for inexact matches. No analysis of how often exact matches occur or the error introduced by approximation.

**4. Limited Sensitivity Analysis:**
The architecture exploration (Figure 14) shows case 10 winning "nearly all scenarios," but there's no analysis of:
- Thermal constraints (more HBMs = more heat)
- Yield implications of larger dies
- Cost-performance tradeoffs

**5. GPU Comparison Fairness:**
The 48 A100 GPUs (6 nodes × 8 GPUs) comparison uses the same 7nm process, but the total silicon area, power envelope, and cost are not discussed. Wafer-scale chips have fundamentally different economics.

---

## Q4: What the Authors Didn't Tell You

### 1. The Simulation is Analytical, Not Cycle-Accurate
The paper never claims cycle-accurate simulation. Section IV-F describes "performance analysis models" calibrated with hardware data, plus ASTRA-sim for communication. This is trace-driven analytical modeling—useful for design space exploration but prone to optimistic assumptions. The claim that computational and NoC models incorporate "system overheads (e.g., control costs)" is hand-wavy without quantifying what overheads are included.

### 2. The LUT Complexity is Understated
Section IV-B2 describes building the LUT by exploring "decode batch size, decode token count, and attention operator tile sizes." The paper claims the exploration is "offline" with "no additional system overhead," but doesn't quantify:
- How large is the LUT? (MB? GB?)
- How long does offline exploration take?
- What happens when the LUT miss rate is high under distribution shift?

### 3. The Microarchitecture Configuration is Suspiciously Specific
Figure 13 shows 0.75MB SRAM with "Large Compute, Large NoC" winning in most scenarios. But the "Small Compute, Small NoC" vs. "Large Compute, Large NoC" comparison (Section VI-A1) conflates multiple variables. Which matters more—compute or NoC? The area models for PE arrays, vector units, and controllers (referenced to [10], [20], [47], [48], [54], [55], [71]) span dramatically different designs (AMD EPYC, RISC-V vectors, etc.). The modeling assumptions aren't disclosed.

### 4. Thermal and Power Analysis is Absent
Wafer-scale chips have notoriously challenging thermal constraints (Cerebras WSE requires custom cooling). The paper never mentions:
- Power consumption of the explored architectures
- Thermal implications of packing 8 HBM chiplets per die (case 10)
- Whether the D2D links' power scales with utilization

### 5. The "Optimal" Architecture Has Tradeoffs
Case 10 (800mm² die, 8 HBMs, 5.69 TB/s D2D, 6×5 die array) is declared optimal, but this configuration:
- Has the **lowest D2D bandwidth** among large die options (Table I)
- Has only **30 dies** vs. 108 in case 1
- May have **yield issues** with 800mm² dies approaching the reticle limit

Section VI-A2's second guideline about "coordinated provisioning" is essentially admitting that case 6 (which maximizes HBMs) actually loses to case 5 because D2D bandwidth becomes the bottleneck. The "optimal" choice is workload-dependent, not universal.

### 6. Multi-Wafer Scalability is Handwaved
Section VI-B1 claims FACE "can naturally extend to multi-wafer systems" by confining scheduling within wafers when inter-wafer bandwidth is limited. This is a significant limitation—it means the prefill-decode overlap benefit disappears at the multi-wafer scale, and the system degrades to something like disaggregated scheduling between wafers.