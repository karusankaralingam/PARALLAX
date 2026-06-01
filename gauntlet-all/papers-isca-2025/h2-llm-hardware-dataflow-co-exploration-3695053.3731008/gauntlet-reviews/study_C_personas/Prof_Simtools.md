## Q1: Whiteboard Explanation

Alright, let me walk you through H2-LLM like I'm sketching it on a whiteboard.

**The Problem Setup:**
Imagine you're running an LLM chatbot on an edge device—maybe a smart home hub or a private server. You've got batch sizes of 1-16 requests, not hundreds. LLM inference has two phases: *prefill* (compute-heavy, processes the prompt in parallel) and *decoding* (memory-heavy, generates tokens one-by-one). The challenge is that existing near-memory processing (NMP) designs put tiny compute engines *inside* DRAM dies, which gives you maybe 1 FLOP/Byte—pathetically underpowered when batch sizes increase beyond ~4.

**The Core Architecture Idea:**
Instead of cramming processing engines into DRAM technology (which is 10× worse logic density than CMOS, per Section 3.2), H2-LLM uses *hybrid bonding*—a 3D stacking technology where you vertically bond a DRAM die onto a *logic* die via Cu-Cu pillars. This lets you:
1. Customize real processing engines on the logic die (not DRAM-constrained)
2. Get massive internal bandwidth (110,000 I/Os per mm² at 3μm pitch)
3. Keep power reasonable (unlike HBM's 2.5D approach)

**The Architecture (Figure 5):**
- A centralized processor (TPU-like) handles compute-intensive prefill
- Multiple HB-NMP channels, each containing: DRAM banks on top, logic die below with multiple PEs (Processing Engines)
- Each PE pairs with one DRAM bank via HB I/O, contains FPUs, weight buffers, output buffers
- A shared input global buffer avoids duplicating inputs across PEs

**The Key Trade-off (Figure 4-b):**
More HB I/O pins = more bandwidth, but the controllers eat ~40% of logic die area at 1024 pins. So you're trading computation capacity for bandwidth. This is the architecture DSE space they explore.

**The Dataflow Abstraction (Section 5):**
Previous work either fixed which operators go to NMP (attention-only, FC-only) or used "compute-centric" mapping that constrains operators to one channel type. H2-LLM proposes *data-centric* mapping:
1. Partition operator graph into Memory Access Groups (MAGs)
2. Assign channel subsets to Memory Partition Groups (MPGs)
3. Fine-grain bind operators to channels
4. Enable operator *fission*—splitting one operator across both NMP and centralized processor

The genetic-algorithm-based DSE then searches architecture parameters + dataflow jointly.

---

## Q2: The Key Insight

**The fundamental insight is that the computation-bandwidth trade-off in hybrid bonding technology must be co-explored with dataflow mapping to unlock its potential for low-batch LLM inference.**

Prior NMP designs for LLMs made two independent assumptions: (1) a fixed architecture with ~1 FLOP/Byte ratio locked by DRAM technology, and (2) fixed operator mappings (e.g., "all attention to NMP" or "all FC to NMP"). H2-LLM recognizes that:

1. **Hybrid bonding creates a tunable knob** between bandwidth and compute that didn't exist in in-die NMP. At 1024 HB pins, you get 51.2 GB/s but lose area for FPUs; at 128 pins, you get 6.4 GB/s but can pack 8 FPUs per PE at 0.6 GHz (Table 6).

2. **The optimal trade-off point shifts with workload characteristics**—batch size, attention variant (MHA/GQA/MQA), and prompt/decode length ratio. Figure 18 shows this beautifully: at BS=1, bandwidth dominates; at BS=16, moderate computation-bandwidth ratio wins.

3. **Dataflow exploration must be prefill-aware**. The compute-centric approach (SpecPIM [47]) constrains operators to either normal or NMP channels, which *reduces external bandwidth to the centralized processor* (Section 3.3). Since edge processors have 500-1000 FLOP/Byte ratios, this can make prefill memory-bound, hurting end-to-end performance—especially in "prefill-heavy" scenarios like context understanding where prefill takes 36%-90% of latency (Figure 14).

**Why it matters**: This reframes NMP design from "maximize NMP bandwidth utilization" to "balance heterogeneous resources across inference phases and operator arithmetic intensities." The 2.72× speedup over ID-NMP+ (Figure 10) comes from this co-exploration, not just from hybrid bonding's raw specs.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive and systematic design space exploration:**
The evaluation spans 3 models (OPT 6.7B, LLaMA3 8B, PaLM 8B) with different attention mechanisms (MHA/GQA/MQA), 4 realistic datasets with varying prompt/decode ratios (Table 1), and batch sizes 1/4/16. This covers the claimed edge inference space well. The mixed-scenario results (Figure 11) demonstrate robustness.

**2. Fair baseline comparisons:**
They double the centralized processor compute for the CP-only baseline to account for NMP's added resources (Section 7.1). ID-NMP uses Samsung's LPDDR5-PIM specs; ID-NMP+ uses AiM's enhanced design—both grounded in real products.

**3. Strong ablation studies:**
The dataflow comparison (Figure 12-13) isolates the contribution of their data-centric abstraction versus Attn-NMP, FC-NMP, and CC-NMP alternatives. Figure 14 directly shows prefill latency reduction (1.27× geomean) versus CC-NMP.

**4. Detailed architecture DSE analysis (Section 7.4):**
Figures 18-21 provide actionable takeaways. The computation-bandwidth trade-off analysis (Figure 18) shows non-obvious results—e.g., at BS=4, going from 25.6 to 51.2 GB/s HB I/O doesn't help because controller area eats compute capacity.

**5. Reasonable overhead accounting:**
Figure 16 reports synchronization and data transfer overhead at 1.6%-15.7%. Figure 17 validates their tiling factor selection against worst-case factors.

### Weaknesses

**1. No silicon validation; simulation only:**
The entire evaluation rests on extending Ramulator2 [52] and injecting Tileflow [89] performance estimates. Per Section 7.1: "We extend Ramulator2 to simulate NMP PE's computation." Ramulator2 is a DRAM *timing* simulator—it doesn't model the logic die's PE microarchitecture. There's no mention of cycle-accurate PE simulation or RTL validation.

**2. Hybrid bonding parameters from "real-chip tape-out" [55] but limited detail:**
Section 7.1 states "HB-related area numbers are obtained from our real-chip tape-out [55]"—but reference [55] is a 2022 ISSCC paper on recommendation systems. It's unclear whether H2-LLM's specific controller/PE area breakdown (Figure 4-b: ~40% area at 1024 pins) was actually measured or extrapolated from that different design.

**3. Energy model is coarse:**
They report 0.88 pJ/bit for HB I/O (from [55]) and synthesized FPU energy at 40nm, but there's no breakdown of dynamic vs. leakage power, no thermal modeling despite 3D stacking concerns, and the AE artifact explicitly states: "we cannot directly provide the simulator due to the data privacy issue... skip the evaluation of energy-related results during the AE process" (Section A.6).

**4. Centralized processor modeled as TPU-like systolic arrays:**
The centralized processor configuration (8 × 128×128 arrays, 128MB SRAM, Section 7.1) is idealized. Real edge accelerators have more complex memory hierarchies. Tileflow's performance model assumes perfect tiling and fusion, which may overestimate centralized processor efficiency.

**5. Limited dataflow design space exploration for attention fusion:**
Modern LLM inference uses FlashAttention-style fused kernels. Section 7.1 mentions "Tileflow supports the evaluation of attention operator fusion," but there's no explicit analysis of how FlashAttention-2's memory access patterns interact with NMP offloading decisions.

**6. Context length limited to 2048:**
Table 1 caps sequences at 2048 tokens "considering edge-side platform's confined resource provision." However, longer contexts (4K-8K) are increasingly common even on edge devices. The KV cache pressure at longer contexts could significantly change the optimal architecture/dataflow.

---

## Q4: What the Authors Didn't Tell You

**1. The DSE search cost is substantial and potentially impractical:**
Section 7.2 mentions "100 rounds" with "5k individuals per iteration"—that's 500,000 evaluations. For Figure 15's "full exploration," they "enlarge the population size to 50k." With 9 hours total runtime (Artifact Appendix A.2), this implies the performance model is *very* approximate. The AE uses a "rough performance model" (Section A.6) instead of the actual simulator, which raises questions about evaluation fidelity.

**2. The mode-switching overhead between normal and NMP modes is glossed over:**
Section 4.3 mentions "Mode Change" commands serve as "memory barriers" but doesn't quantify this latency. DRAM row buffer conflicts are mentioned but the actual stall cycles are never reported. This matters because frequent mode switching per operator tier could accumulate significant overhead.

**3. Memory capacity constraints aren't fully explored:**
They target 8B models on 8 channels × 16 banks × 256MB = 32GB capacity. But FP16 8B models require ~16GB for weights alone; KV cache at batch=16, context=2048, hidden=4096 adds another ~2GB. There's no analysis of what happens when capacity forces model partitioning or KV eviction policies.

**4. The input global buffer sharing across PEs creates a potential bottleneck:**
Figure 5-c shows one input global buffer shared among all PEs. Section 4.2 describes sequential input tile loading (step ❶) before PE computation (step ❷-❸). But with 16 PEs per channel, the input broadcast could serialize PE startup. They claim "output-stationary" execution avoids row buffer conflicts, but the input buffer access pattern isn't deeply analyzed.

**5. Thermal implications of hybrid bonding are ignored:**
Reference [83] in their bibliography explicitly addresses "Thermal Problem of 3D Stacked Memory." Yet H2-LLM's 3D-stacked design—with active compute on the logic die under DRAM—has no thermal analysis. At 0.6-1.0 GHz with 8 FPUs per PE across 16 PEs, thermal throttling is a real concern.

**6. The "data-centric" vs. "compute-centric" distinction may be overstated:**
Their critique of SpecPIM [47] (Section 3.3) is that it "constrains operator placement to either normal or NMP channels," reducing bandwidth for prefill. But their solution—binding operators to channel *subsets*—still partitions channels between operators. The improvement (1.11× over CC-NMP in Figure 12) is modest and mostly comes from operator fission, which isn't unique to "data-centric" framing.

**7. No comparison with software-only optimizations:**
Techniques like FlashAttention, continuous batching, or speculative decoding could narrow the gap between baseline and H2-LLM without new hardware. The baseline centralized processor uses Tileflow for fusion but doesn't represent state-of-the-art serving systems like vLLM or TensorRT-LLM.

**8. The artifact is incomplete for key claims:**
Per Section A.6: "we adopt a rough performance model to estimate NMP/NPU operators' latency, and skip the evaluation of energy-related results during the AE process." The energy efficiency claims (1.48× improvement, Figure 10) are not reproducible from the public artifact.