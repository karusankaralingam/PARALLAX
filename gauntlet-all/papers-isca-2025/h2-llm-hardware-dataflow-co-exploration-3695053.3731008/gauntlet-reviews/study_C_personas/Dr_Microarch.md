## Q1: Whiteboard Explanation

Let me walk you through what H²-LLM actually is at the hardware level.

**The Problem Setup:**
Edge-side LLM inference (batch sizes 1-16) faces a fundamental mismatch. During decoding, you're reading ~8GB of weights to generate a single token—classic memory-bound behavior. Existing near-memory processing (NMP) solutions embed tiny processing engines (PEs) directly into DRAM dies, giving you maybe 1-2 FLOPs/Byte (see Table 2, page 5). That's fine for batch=1, but as Figure 3 shows, once batch size hits 8 or you use GQA with fewer KV heads, in-die NMP becomes compute-limited while the centralized processor is still bandwidth-limited. Both units are simultaneously sub-optimal.

**The Structural Solution:**
H²-LLM uses **hybrid bonding (HB)** instead of in-die NMP. Looking at Figure 4-(a), the DRAM die sits on top of a custom logic die, connected via Cu-Cu direct fusion bonding at ~3μm pitch (110,000 I/Os per mm²). This lets you:
1. Put real compute logic on the logic die (not cramped DRAM-process transistors)
2. Get high bandwidth to each DRAM bank through dedicated HB I/O channels

**The Actual Wiring (Figure 5):**
- A centralized processor (TPU-like, 8 systolic arrays @ 1GHz) connects to 8 memory channels
- Some channels are normal DRAM; some are HB-NMP channels
- Each HB-NMP channel has multiple DRAM banks, each paired with its own PE via HB controller
- Critical: A shared **input global buffer** across PEs avoids duplicating input tensors to every bank

**PE Architecture (Figure 5-(c)):**
Each PE contains multiple FPUs (configurable 1-8 units, each with 16 MACs), plus weight/output buffers. The design space trade-off is explicit in Table 6: at 51.2 GB/s HB I/O, controller area eats into compute, leaving room for only 2 FPUs at 1GHz. At 6.4 GB/s, you can fit 8 FPUs.

**The Execution Flow (Section 4.2):**
For GEMM operators mapped to NMP:
1. Centralized processor scatters inputs to HB-NMP channels
2. Input tiles load to global buffer (❶), PEs load weight tiles and compute (❷-❸), outputs accumulate and write back (❹)
3. Centralized processor collects and merges partial results

The key insight on tiling: they solve Equation 1 analytically to minimize transfer overhead when splitting across K and N dimensions. For 8 channels doing (M,K)×(K,N), optimal tiling is T_K = sqrt(C × K×B_l / N×B_s).

---

## Q2: The Key Insight

**The "Magic Trick":**
The core architectural insight is that **hybrid bonding creates a computation-bandwidth trade-off that doesn't exist in traditional in-die NMP**. The paper's Figure 4-(b) quantifies this: HB controllers eat 10-40% of logic die area depending on I/O count. This is the first work to systematically explore this trade-off for LLM inference.

But the deeper insight is in the **data-centric dataflow abstraction** (Section 5). Previous work like SpecPIM [47] uses "compute-centric" mapping—assign operator to computation engine first, then derive channel allocation. H²-LLM inverts this: **bind operators to memory channels first, then derive computation engine**.

Why does this matter? In a heterogeneous system where some channels have NMP PEs and others don't, compute-centric mapping forces operators onto either-or: all NMP channels or all normal channels. This fragments external bandwidth. Data-centric mapping lets you bind one operator to *both* channel types and **fission** the operator—split the workload without duplicating weights (Section 5.2).

Figure 7 shows this concretely for a parallel transformer: operators Q, K, F1, F2 are bound to both normal and NMP channels, while V, F3 go to NMP-only. The paper's Table 3 calls out that only H²-LLM supports flexible fission, while AttAcc [60] has fixed FFN-only fission.

**The practical consequence (Figure 14):** After accelerating decoding with NMP, prefill still takes 12-90% of end-to-end latency. The data-centric approach achieves 1.27× geomean prefill speedup over compute-centric because it doesn't starve the centralized processor of bandwidth during prefill.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Honest roofline analysis (Figure 3):** The authors don't hide the limitations. They show exactly where in-die NMP falls short—batch size ≥8 or KV heads ≤4—and why the centralized processor remains bandwidth-bound while NMP is compute-bound.

2. **Real silicon numbers for HB:** The area models in Section 7.1 come from "our real-chip tape-out [55]" and in-house 40nm synthesis. They report specific numbers: FPU at 1GHz = 0.77mm², SRAM = 2.72mm²/MB, PE total = 6.76mm². This isn't hand-waving.

3. **Comprehensive dataflow comparison (Figure 12-13):** They compare against four prior schemes (Attn-NMP, Attn-NMP-Split, FC-NMP, CC-NMP) on the same H²-LLM hardware, isolating the dataflow contribution. Result: dataflow exploration alone provides 1.37×/1.11× over FC-NMP/CC-NMP.

4. **Architecture design space case studies (Section 7.4):** Figures 18-21 systematically vary one dimension while fixing others. The takeaways are actionable—e.g., "with batch size increase, performance becomes more sensitive to input/output buffer size, while sensitivity to weight buffer diminishes" (Takeaway 4).

**Weaknesses:**

1. **Simulation methodology gaps:** The energy model uses different technology nodes for centralized processor (10nm) vs. HB-NMP (40nm). While they cite synthesis results, comparing pJ/MAC across 3× node difference (10nm vs 40nm) is apples-to-oranges. The 0.682 vs 1.365 pJ/MAC comparison (Section 7.1) is thus inflated against NMP.

2. **Baseline selection bias:** ID-NMP+ uses "AiM's PE design" at 1GHz, but AiM operates on GDDR6, not LPDDR5. The fair comparison would be Samsung's actual LPDDR5-PIM at 200MHz (ID-NMP baseline). H²-LLM achieves 2.72× over ID-NMP+, but ID-NMP+ itself isn't a real product—it's a hypothetical enhancement.

3. **No end-to-end system validation:** The evaluation uses extended Ramulator2 [52] with Tileflow [89] for centralized processor. They acknowledge in Section A.6 that "we cannot directly provide the simulator due to data privacy issue" and use "a rough performance model" for artifact evaluation. Cycle-accurate RTL simulation is absent.

4. **Prefill is still centralized-processor-only:** Section 5.2 states "Operators in the prefill stage are assigned to the centralized processor." This means during prefill, all HB-NMP compute capacity sits idle. For prefill-heavy workloads (LongBench/LooGLE), this is 36-90% of runtime where NMP PEs contribute nothing.

5. **Context length fixed at 2048:** Table 1 truncates to 2048 tokens for "edge-side platform's confined resource provision." Modern edge LLM use cases often need 8K+ context. The KV cache scaling implications aren't explored.

---

## Q4: What the Authors Didn't Tell You

**1. The Controller Area Problem is Worse Than Stated:**
Figure 4-(b) shows 40% area for 1024 HB I/O pins driving a "single DRAM bank." But each channel has 16 banks. The paper never clarifies whether the 40% is per-bank or per-channel. If per-bank, and you want bank-level parallelism, controller overhead could dominate.

**2. Thermal Constraints Are Absent:**
Hybrid bonding stacks logic directly under DRAM. DRAM is thermally sensitive (refresh rates increase with temperature). The paper cites [83] (Parana) which discusses thermal problems of 3D stacked memory, but H²-LLM provides zero thermal analysis. At 1GHz with 8 FPUs per PE, the logic die will generate significant heat directly beneath the DRAM.

**3. Manufacturing Reality Check:**
The paper assumes "40nm technology" for NMP logic die (Section 7.1). Hybrid bonding at production scale currently exists at SK Hynix (HBM3E) and TSMC (for Apple/AMD). But these are >$2000/chip solutions for datacenter. The cost model for edge devices with HB is entirely missing. The area numbers are given, but no cost analysis.

**4. The Input Global Buffer is a Potential Bottleneck:**
Figure 6 shows all PEs sharing one input global buffer. Section 4.1 states "A input global buffer is shared among all PEs to avoid duplicating the input tensor to each DRAM bank." But what's the read bandwidth? If 8 PEs each need input tiles simultaneously for attention operators, this buffer becomes a serialization point. The paper shows 32KB default size but not multi-port access or banking.

**5. Synchronization Overhead Scaling:**
Figure 16 shows 1.6-15.7% overhead for synchronization and data transfer. But this is for batch sizes 1-16 on 8 channels. The dataflow abstraction (Section 5.3) lists four synchronization cases. As models scale or batch sizes increase, synchronization case (4)—"when VPU number is not enough for distinct assignment"—will hit more often. The genetic algorithm DSE (Section 6.2) hides this complexity.

**6. The "Edge" Claim is Questionable:**
The centralized processor is configured with "8 128×128 systolic arrays together with 8 SIMD-128 vector processing units running at 1GHz" and "128MB" SRAM (Section 7.1). That's 128 TFLOPS INT8 equivalent. This isn't Jetson-class edge; it's more like a server chip. The comparison against "NVIDIA Jetson Modules" [56] in Table 5 is never actually done.

**7. Weight Duplication is Hidden in Operator Fission:**
Section 5.2 claims "operator fission, which splits one operator to both normal and NMP channels without duplicating the weights." But look at Figure 6: weights W₀...W_{P-1} are distributed across PE banks. For fission between NMP and centralized processor, the centralized processor needs its weight portion in its own DRAM. The paper glosses over this—there's no explicit weight placement analysis for fissioned operators.