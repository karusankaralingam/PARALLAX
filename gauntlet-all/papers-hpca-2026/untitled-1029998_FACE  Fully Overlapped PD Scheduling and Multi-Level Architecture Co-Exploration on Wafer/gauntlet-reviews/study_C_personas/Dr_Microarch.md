## Q1: Whiteboard Explanation

Let me walk you through the wiring diagram here, starting from the physical reality of what FACE is actually doing.

**The Hardware Context (Figure 2):**
We're looking at a wafer-scale chip built from chiplets—multiple compute dies (each containing NPU cores) integrated with HBM DRAM on a 12-inch wafer via an interposer. The key architectural primitives are:
- **Wafer level:** 2D-mesh topology connecting dies via D2D (die-to-die) links
- **Die level:** A compute die array + attached HBM chiplets + mesh NoC + D2D interfaces
- **Core level:** PE arrays (for GEMM), vector units (for softmax/element-wise), SRAM, DMA, and a control unit

**The Fundamental Problem:**
LLM inference has two phases with opposite bottlenecks: prefill (compute-bound, matrix-matrix ops) and decode (memory-bound, matrix-vector ops). Existing approaches either serialize them (unified scheduling—wastes compute during decode) or separate them onto different hardware partitions (disaggregated scheduling—introduces KV cache transfer overhead and underutilizes decode instance compute, as shown in Figure 4(b) where decode compute utilization is <9%).

**The "Magic Trick" (Figure 5(c)):**
FACE exploits the *fine-grained control* of NPU-style architectures to run prefill and decode attention operations *simultaneously* on the same instance. Here's the actual mechanism:

1. **Tile-level co-scheduling:** The attention operator for prefill (Q×K^T where Q is a matrix) and decode (Q×K^T where Q is a vector/small batch) are partitioned into tiles. The host-side controller programs each core's DMA and control unit to process prefill tiles and decode tiles concurrently.

2. **Core-group mapping (Figure 10):** Within a die, cores are organized into groups. Prefill attention tiles and decode attention tiles are mapped to the same core groups but interleaved at the nano-computation level. The dual-head pipeline (Section IV-E2) schedules PE array work (matrix multiply for one head) in parallel with vector unit work (softmax/data movement for another head).

3. **The LUT-based runtime (Figures 7-8):** Offline, FACE precomputes a Look-Up Table (LUT) that stores, for each workload configuration (prefill chunk size, decode batch size, decode token count), the optimal tile sizes for both phases that achieve balanced overlap. At runtime, the Dynamic Adaptive Scheduler (DAS) queries this LUT in O(n) time to make scheduling decisions.

**The Memory Trick (Figure 9):**
The key insight enabling flexible decode request placement is Equation (1): `Distance(ins_p, ins) ≤ D2D_BW / DRAM_BW`. Because D2D bandwidth exceeds DRAM bandwidth, a decode request can access KV cache from a *remote* instance without incurring additional latency—the bottleneck remains DRAM access, not D2D transfer. This expands the "schedulable instance range" for workload balancing.

---

## Q2: The Key Insight

**The singular architectural insight is this:** On wafer-scale chips, the D2D interconnect bandwidth exceeds DRAM bandwidth (Table I shows D2D up to 8.13 TB/s vs. HBM at 410 GB/s × 8 = 3.28 TB/s max per die). This ratio (D2D_BW/DRAM_BW > 1) fundamentally changes the memory access model.

Concretely:
- **For KV cache placement:** A decode request doesn't need its KV cache co-located on the same instance. Remote DRAM access via D2D is "free" up to the distance threshold in Equation (1)—the latency is still dominated by DRAM, not the interconnect.
- **For prefill-decode overlap:** Because wafer-scale NPUs provide PE-level control (unlike GPUs with kernel-level granularity), you can tile prefill and decode attention at arbitrary granularity and co-schedule them without kernel launch overhead.

The "delta vs. baseline" (WSC-LLM's disaggregated approach) is structural: FACE eliminates the P-instance/D-instance partition entirely. Every instance runs *both* phases concurrently. The scheduling problem transforms from "which partition gets this request" to "what prefill/decode tile ratio achieves balanced pipeline utilization on this instance."

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Realistic hardware modeling:** The evaluator (Section IV-F) is calibrated against real NPU measurements and actual HBM2E specifications (Micron, 410 GB/s per device, 16GB capacity). The area models reference AMD EPYC and commercial NPU designs (references [10], [47], [48], [54], [55], [71]).

2. **Comprehensive design space coverage:** Table I shows 14 microarchitecture configs × 10 architecture configs explored. The two-stage exploration (fix die area → optimize core config → then vary die/HBM counts) is methodologically sound.

3. **Fair baseline selection:** Comparing against both WSC-LLM (wafer-scale SOTA) and vLLM on 6-node A100 clusters (same 7nm process, 48 GPUs total) provides meaningful cross-platform context. The 3.68× E2E improvement over WSC-LLM and 7.23× over vLLM (Section V-B, V-C) are substantial.

4. **Workload realism:** Using Azure production traces (code/conversation datasets) with actual arrival rates (2.57/s and 5.53/s) and length distributions avoids cherry-picked synthetic benchmarks.

**Weaknesses:**

1. **Simulation-only evaluation:** Despite calibration claims, all results come from the modified WSC-LLM evaluator. No silicon, no FPGA prototype, no RTL. The "real hardware measurement data" (Section IV-F) calibrates the *model* but doesn't validate the full system.

2. **Missing thermal/power analysis:** A wafer-scale chip packing 30+ dies has severe thermal constraints. The paper assumes uniform compute capability across the wafer, but in reality, edge dies run cooler than center dies. No power budgeting is discussed.

3. **LUT storage and lookup overhead hand-waved:** The paper claims LUT lookup is O(n) with "one-dimensional Euclidean-distance computation" (Section IV-C2), but doesn't quantify LUT size. With 3 workload parameters and multiple tile size combinations, the table could be substantial.

4. **No contention modeling for D2D links:** Equation (1) assumes no D2D congestion if distance ≤ BW ratio. But Figure 9 shows multiple instances may share D2D links. The "link weight update" mechanism (Section IV-D2) is mentioned but not rigorously analyzed for pathological cases.

5. **Limited model diversity:** Only LLaMA family (7B, 13B, 70B). No MoE models (which have different memory/compute patterns), no multi-modal models, no context lengths beyond the dataset distributions.

---

## Q4: What the Authors Didn't Tell You

**The Hidden Hardware Tax:**

1. **Control path latency:** The "fine-grained control" enabling tile-level co-scheduling requires the host to program each core's controller and DMA engine (Section III-C). The paper assumes this control latency is negligible, but for real-time scheduling at iteration granularity (milliseconds), the PCIe/control-I/O roundtrip could become significant. No latency numbers are provided.

2. **SRAM pressure from dual-head pipeline:** The dual-head pipeline (Section IV-E2) schedules two attention heads simultaneously, meaning the SRAM must hold Q, K, V, intermediate S, and output O tiles for *two* heads concurrently. The "CheckSRAM" constraint (Figure 7, line 6) implicitly doubles the SRAM footprint requirement, which explains why 0.75MB SRAM (not larger) is optimal in Figure 13—larger tiles for single-head would be better, but dual-head needs the split.

3. **The reticle-limit assumption:** The optimal architecture (case 10) uses 800mm² compute dies—approaching the reticle limit (~850mm²). The paper handwaves this as "compute dies should be as large as possible" (Section VI-A2), but larger dies have exponentially worse yield. No yield modeling or cost analysis is provided.

4. **Chunked prefill introduces latency:** The paper uses chunked prefill with chunk size = average input length (Section IV-B2). For requests with input length >> average, this means multiple iterations to complete prefill, increasing Time-To-First-Token (TTFT). The E2E latency metric hides this by averaging.

5. **The "schedulable instance range" limitation:** Equation (1) with typical parameters (D2D ~6-8 TB/s, DRAM ~3.3 TB/s per die) gives Distance ≤ 2. On an 8×8 die array (case 5-6), this means decode requests can only migrate 2 hops—severely limiting load balancing for hot spots at wafer corners.

6. **No preemption or priority:** The FCFS decode queue (Section IV-C1) and ΔT-minimizing decode placement (Section IV-C2) assume all requests are equal. Real serving systems need SLO-aware scheduling with preemption for latency-sensitive requests.