# Study C — Multi-Persona Synthesis
**Paper:** 1029998 FACE  Fully Overlapped PD Scheduling and Multi Level Architecture Co Exploration on Wafer  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 07:30

---

# Q1: Whiteboard Explanation

FACE addresses a fundamental mismatch in LLM inference on wafer-scale chips: the prefill phase (processing input prompts) is compute-bound while the decode phase (generating tokens) is memory-bound. These phases have complementary resource demands, yet prior approaches either serialize them (causing interference) or physically separate them onto different hardware partitions (creating topology constraints and severe underutilization—decode instances show <9% compute utilization per Figure 4(b)).

**The Hardware Context (Figure 2):**
A wafer-scale chip consists of multiple compute dies (each containing NPU cores with PE arrays, vector units, SRAM, and DMA engines) integrated with HBM chiplets on a 12-inch wafer via an interposer. Dies connect through a 2D-mesh D2D network with bandwidth ranging from 1.65-8.13 TB/s depending on configuration.

**The Core Mechanism:**
FACE exploits wafer-scale chips' *fine-grained control*—the host can directly manage each core's controller and DMA engine via control I/O. This enables running prefill and decode attention operations *simultaneously* on the same instance by partitioning work at the tile level.

**The Three-Part Solution:**

1. **Configuration Space Exploration (CSE)**: Offline, FACE explores all valid combinations of (prefill chunk size, decode batch size, decode token count, tile sizes for both phases) and stores results in a Look-Up Table (LUT). The key constraint: SRAM must fit tiles for both phases. The algorithm (Figure 7) finds tile size pairs where `max(prefill_time, decode_time)` is minimized—achieving full overlap.

2. **Dynamic Adaptive Scheduling (DAS)**: At runtime, the prefill engine assigns requests to the least-busy instance using a sorted queue. The decode engine is smarter—it queries the LUT to find which instance would see the *minimum increase in per-iteration latency* (ΔT) if assigned this request.

3. **Optimized Memory Management (OMM)**: The critical insight is Equation (1): `Distance(ins_p, ins) ≤ D2D_BW / DRAM_BW`. Because D2D bandwidth exceeds DRAM bandwidth, decode requests can access KV cache from *remote* instances without additional latency—the bottleneck remains DRAM, not the interconnect. This expands the "schedulable instance range" for load balancing.

**Architecture Co-Exploration:**
FACE also searches the hardware design space at two levels: microarchitecture (SRAM/compute/NoC balance per core—finding 0.75MB SRAM optimal) and architecture (die size, HBM count, D2D bandwidth—finding large dies with maximum HBM chiplets perform best, case 10 in Table I).

---

# Q2: The Key Insight

The central insight is that **wafer-scale chips' fine-grained core-level control enables fully overlapped prefill-decode attention execution**, which eliminates the interference that plagues both unified scheduling (serialized attention) and disaggregated scheduling (topology constraints, <9% decode compute utilization).

This is articulated in Section III-C and Figure 5(c). Prior GPU-based systems like Sarathi could overlap *linear* operations between phases, but *attention* remained serialized because prefill attention (Q×K^T where Q is a matrix) and decode attention (Q×K^T where Q is a vector) have different input shapes and can't be batched together. FACE recognizes that wafer-scale NPUs support "dynamic tensor decomposition and PE-level execution control" (Section II-B2), allowing the host to orchestrate which cores work on which tiles of prefill versus decode attention simultaneously.

**The secondary architectural insight** is that D2D bandwidth exceeding DRAM bandwidth (Table I shows D2D up to 8.13 TB/s vs. HBM at ~3.28 TB/s per die) fundamentally changes the memory access model. Equation (1) formalizes this: decode requests can be scheduled to any instance within `D2D_BW/DRAM_BW` hops without incurring additional latency. This breaks the rigid coupling of disaggregated systems where prefill and decode must happen on adjacent instances.

**Why this only works on wafer-scale chips:** The paper emphasizes that GPU SMs are designed for SIMT execution of homogeneous warps, not simultaneous execution of differently-tiled attention kernels. The NPU architecture's ability to "directly manage the controller and DMA engine of each core to precisely regulate the sizes of prefill and decode tiles" (Section III-C) is the key enabler.

---

# Q3: Evaluation Critique

**Strengths:**

1. **Comprehensive Design Space Coverage:** Table I shows 14 microarchitecture configs × 10 architecture configs explored. Figures 13-14 present systematic sweeps producing non-obvious insights (e.g., 0.75MB SRAM with high compute/NoC is optimal because moderate SRAM maintains core count while allowing denser compute integration).

2. **Real-World Workloads:** Azure production traces (Section V-A3) with realistic distributions—code dataset (2.57 req/s, 3-7437 input tokens) and conversation dataset (5.53 req/s, 2-14050 input tokens)—avoid synthetic benchmarks.

3. **Hardware-Calibrated Models:** Section IV-F states the evaluator is "calibrated with actual data collected from a representative NPU device [85]" and DRAM access is "modeled based on real HBM hardware [2]."

4. **Fair Baseline Selection:** Comparing against WSC-LLM (wafer-scale SOTA) using identical hardware configuration and vLLM on 6-node A100 clusters (same 7nm process, 48 GPUs) provides meaningful context. The 3.68× E2E improvement over WSC-LLM and 7.23× over vLLM are substantial.

5. **Honest Ablation Structure:** Figure 11 systematically tests all 6 combinations of {W-Sch, U-Sch, F-Sch} × {W-Arch, F-Arch}, demonstrating both scheduling and architecture improvements are necessary.

**Weaknesses:**

1. **Simulation-Only Evaluation:** Despite calibration claims, all results come from modified WSC-LLM evaluator and ASTRA-sim. No silicon, no FPGA prototype, no RTL. The paper never claims cycle-accurate simulation—this is trace-driven analytical modeling.

2. **Missing Thermal/Power Analysis:** A wafer-scale chip with 30+ dies has severe thermal constraints. The paper assumes uniform compute capability, but edge dies run cooler than center dies. No power budgeting, no thermal throttling analysis.

3. **GPU Comparison Limitations:** The A100 comparison uses 2020 hardware. Modern vLLM has chunked prefill and speculative decoding. More critically, the paper compares against unified scheduling only—not disaggregated GPU systems like DistServe or Splitwise, which it cites but doesn't benchmark.

4. **LUT Complexity Understated:** The paper claims O(n) lookup but doesn't quantify LUT size. With multiple decode batch sizes, token counts, and tile size pairs, this could be millions of entries. No sensitivity analysis for LUT miss rates under distribution shift.

5. **Throughput Gains Modest vs. Latency:** While E2E latency improves 3.68× vs. WSC-LLM, throughput only improves 1.70×. This asymmetry suggests the system reduces per-request latency but doesn't scale request-level parallelism as dramatically—unexplained in the paper.

6. **Missing Latency Distribution Analysis:** Only average E2E latency is reported. For SLO-driven serving, P99/P999 latency matters more. The OMM strategy with remote KV cache access could create tail latencies hidden in averages.

7. **Yield and Cost Ignored:** The "optimal" architecture (case 10) uses 800mm² dies approaching the reticle limit. No yield modeling or cost analysis despite claiming chiplet integration offers "improved manufacturing yield."

---

# Q4: What the Authors Didn't Tell You

**1. The "Fully Overlapped" Claim Has Fine Print:**
Section IV-B2 states prefill uses "chunked prefill" with chunk size set to "average input token length from the test dataset." A request with 7000 input tokens (present in Azure code dataset) requires many chunked iterations, ballooning TTFT. The paper reports only aggregate E2E latency—not TTFT or P99, which are critical SLO metrics.

**2. Control Path Latency is Assumed Away:**
The "fine-grained control" enabling tile-level co-scheduling requires the host to program each core's controller and DMA engine per iteration (~millisecond scale). No control latency numbers are provided. Real NPUs may batch instructions, making per-iteration reconfiguration expensive.

**3. SRAM Pressure from Dual-Head Pipeline:**
The dual-head pipeline (Section IV-E2) schedules two attention heads simultaneously, requiring SRAM to hold Q, K, V, intermediate S, and output O tiles for *two* heads concurrently. This implicitly doubles SRAM footprint requirements—explaining why 0.75MB (not larger) is optimal.

**4. The Schedulable Instance Range is Limited:**
With typical parameters (D2D ~6-8 TB/s, DRAM ~3.3 TB/s per die), Equation (1) gives Distance ≤ ~2 hops. On an 8×8 die array, decode requests can only migrate 2 hops—severely limiting load balancing for hot spots at wafer corners.

**5. D2D Contention Not Rigorously Modeled:**
Equation (1) assumes no D2D congestion if distance ≤ BW ratio. But Figure 9 shows multiple instances may share D2D links. The "link weight update" mechanism (Section IV-D2) is mentioned but not analyzed for pathological cases.

**6. Multi-Wafer Scalability is Handwaved:**
Section VI-B1 claims FACE "can naturally extend to multi-wafer systems" but only mentions that "when wafer-to-wafer bandwidth is limited... FACE automatically confines inter-instance workload balancing within each individual wafer." This means prefill-decode overlap benefits disappear at multi-wafer scale.

**7. Fault Tolerance Completely Absent:**
For a 12-inch wafer, neither the architecture template nor evaluation mentions defect handling, redundancy, or routing around dead dies/cores. Real wafer-scale systems (Cerebras, Dojo) dedicate significant design complexity to this.

**8. The Architectural Exploration is Narrowly Constrained:**
Table I shows 10 architecture cases with fixed (die_size, HBM_count) combinations. The paper doesn't explore heterogeneous die sizes, asymmetric HBM placement, non-square die arrays, or different memory technologies. The "optimal" architecture is optimal within a narrow search space.