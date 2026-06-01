## Q1: Whiteboard Explanation

Alright, picture this on the whiteboard. You've got LLMs running on edge devices—think personal assistants, smart home bots, private servers. These edge scenarios have a critical property: **low batch sizes** (1-20 requests), not hundreds like cloud inference.

**The Problem:** LLM inference has two phases with completely opposite computational profiles:
- **Prefill** (processing the prompt): Compute-intensive, processes 100s-1000s of tokens in parallel
- **Decoding** (generating output): Memory-bandwidth-bound, processes one token at a time per request

Existing Near-Memory Processing (NMP) solutions embed tiny processing engines directly inside DRAM dies. Look at Table 2—Samsung's HBM-PIM gives you about 1 FLOP/Byte computation-to-bandwidth ratio. That's pathetically low. Figure 3 shows the roofline: when batch size ≥8 or you use GQA/MQA (which reduces KV heads), in-die NMP loses its advantage entirely because it simply can't compute fast enough.

**The H²-LLM Solution:** Stack a logic die underneath the DRAM die using hybrid bonding technology. This gives you:
1. **Custom processing engines** on the logic die (not constrained by DRAM process)
2. **High bandwidth** through dense Cu-Cu pillar connections (~110,000 pins/mm² at 3μm pitch)

But here's the catch shown in Figure 4(b)—those hybrid bonding controllers eat 10-40% of your logic die area depending on bandwidth. More bandwidth = less room for compute. This is the central **computation-bandwidth trade-off** the paper explores.

**The Dataflow Innovation:** They propose a "data-centric" abstraction (Section 5) that decides: Which operators go to the centralized processor? Which go to NMP? Can we split ("fission") an operator across both? This is solved via a genetic algorithm DSE framework.

---

## Q2: The Key Insight

The key insight is actually **two-fold and tightly coupled**:

**Insight #1 (Architecture):** Hybrid bonding creates a fundamental trade-off between computation capacity and memory bandwidth that doesn't exist in conventional in-die NMP. Every additional HB I/O pin for bandwidth costs you logic die area that could house more FPUs. The paper quantifies this in Section 3.2 and Table 6: at 51.2 GB/s HB bandwidth, you can only fit 2 FPUs at 1GHz, but at 6.4 GB/s, you can fit 8 FPUs. **Finding the optimal point on this Pareto frontier is workload-dependent.**

**Insight #2 (Dataflow):** Previous dataflow strategies are either fixed (offload all attention to NMP, or all FCs to NMP) or "compute-centric" (SpecPIM [47]), which constrains operators to either NMP channels OR normal channels exclusively. The problem with compute-centric mapping: it reduces the external bandwidth available to the centralized processor during prefill, which can shift compute-bound prefill operators to memory-bound (Section 3.3). 

**The synthesis:** By exploring architecture and dataflow jointly—what the paper calls "data-centric" abstraction with operator-channel binding (not compute-engine binding)—you can:
1. Assign the same operator to **both** NMP and normal channels via "operator fission" (Table 3 shows only H²-LLM does flexible fission)
2. Maintain full external bandwidth for prefill while still accelerating decoding
3. Co-optimize the hardware design point with the dataflow strategy

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**S1: Diverse Workload Characterization**
Table 1 is excellent. They actually characterize real edge use cases (code completion, chatbot, context understanding, Q&A) and show the prompt/decoding length distributions vary significantly. The HumanEval average is 157/67 (decoding-heavy) versus LooGLE at 1971/17 (prefill-heavy). This justifies why fixed dataflows fail.

**S2: Reasonable Baseline Construction**
They compare against three baselines (Section 7.1): CP-only (centralized processor with doubled compute), ID-NMP (Samsung LPDDR5-PIM at 200MHz), and ID-NMP+ (enhanced with AiM's 1GHz PEs). The ID-NMP configurations come from published commodity designs (Table 2 cites [37,39,41,43,44]), not strawmen.

**S3: Honest Overhead Reporting**
Figure 16 reports synchronization and data transfer overhead at 1.6%-15.7%. They don't hide it. Figure 17 shows different tiling factors can incur 1.5×-5.4× overhead—they're transparent about sensitivity.

**S4: Design Space Analysis is Informative**
Section 7.4's takeaways (computation-bandwidth trade-off, buffer sizing, NMP channel distribution) provide actionable insights. Figure 18 shows the ranking changes with batch size—the "best" design at BS=1 differs from BS=16.

### Weaknesses

**W1: The "Cherry-Pick" Check — Missing Quantized Models**
The entire evaluation uses FP16 (Section 7.1). Real edge deployment heavily uses INT8/INT4 quantization. Quantization changes the arithmetic intensity curve entirely—4-bit weights mean 4× lower memory traffic per operation. The roofline in Figure 3 would shift dramatically. **They never address this.**

**W2: The Baseline Validity — No GPU Comparison**
They compare against in-die NMP designs, but not against actual edge GPUs like Jetson Orin (which they cite as a reference in Section 7.1 for compute-bandwidth ratio). The closest is Figure 21's "GPU" label at 312 TFLOPS (A100), but A100 isn't an edge device. Where's Jetson AGX Orin comparison?

**W3: The "Zero-Event" Reality — Hybrid Bonding Maturity**
The paper assumes hybrid bonding is ready for edge deployment. Their HB parameters (Section 7.1) come from "our real-chip tape-out [55]"—which is a neural recommendation accelerator, not LLM inference. The 40nm logic technology they use for synthesis is ancient. What's the thermal envelope? Power budget? They mention HB is "lower power than HBM" (Section 3.2) but never give absolute power numbers for the complete system.

**W4: Batch Size Ceiling**
They test BS=1/4/16. But what happens at BS=32 or 64? Figure 18 suggests the architecture preference is shifting toward higher compute—does H²-LLM still win when the workload is no longer memory-bound? The design space exploration may have found local optima that don't generalize.

**W5: Prefill Speedup Decomposition is Thin**
Figure 14 shows prefill takes 12%-90% of end-to-end latency, and their data-centric abstraction achieves 1.27× prefill speedup. But they don't explain *how*. What operator mappings improve prefill? The claim is about maintaining external bandwidth, but there's no bandwidth utilization breakdown.

**W6: Single-Chip Assumption**
The architecture assumes one centralized processor + one memory system. Multi-chip or multi-device edge deployments aren't considered.

---

## Q4: What the Authors Didn't Tell You

**1. The Simulation Fidelity is Questionable**
Section 7.1 states: "We extend Ramulator2 [52] to simulate NMP PE's computation" and "adopt Tileflow's performance model [89] to evaluate centralized processor operators." These are analytical models, not cycle-accurate RTL simulation. The DSE framework's evaluator (Section 6) is essentially a roofline model with some correction factors. The 2.72× speedup is a **simulated number**, not measured silicon.

**2. The Artifact Appendix Confession**
In Appendix A.6: "Since we cannot directly provide the simulator due to the data privacy issue, we adopt a **rough performance model** to estimate NMP/NPU operators' latency, and skip the evaluation of energy-related results during the AE process." Translation: the artifacts can't reproduce the paper's claimed results exactly. The reference plots in `ae/plots_ref` will "slightly different from that in the paper."

**3. The Genetic Algorithm is a Black Box**
Section 6.2 describes five genetic operators (OP1-OP5), but there's no sensitivity analysis on DSE hyperparameters. They use "100 rounds, 5k individuals per iteration, Top-50 selection"—why these numbers? Is convergence guaranteed? How much variance between DSE runs?

**4. Mixed-Scenario Evaluation is Underdeveloped**
Figure 11 shows "mixed scenarios" performance by "mixing the four scenarios evenly"—but real edge deployments have non-uniform request distributions. What if 80% of requests are code completion (short prompts) and 20% are context understanding (long prompts)? The DSE weights workloads equally by default (Section 6.2), which may not reflect reality.

**5. The Comparison with SpecPIM [47] is Incomplete**
SpecPIM also does architecture-dataflow co-exploration for speculative inference. H²-LLM claims their "data-centric" abstraction is superior to SpecPIM's "compute-centric" approach because of prefill awareness. But SpecPIM targets speculative inference (multiple models), not single-model inference. The comparison in Table 3 conflates different problem scopes.

**6. Area Numbers are Scattered**
Section 7.1 says "each NMP PE's area is 6.76mm²" at 40nm. For 8 NMP channels × 16 banks/channel × 6.76mm² = ~865mm² just for PEs. Add DRAM dies, centralized processor, etc. What's the total die area? Is this even manufacturable for edge?

**7. No Discussion of KV Cache Management**
LLM inference's main memory challenge is KV cache growth with context length. They cap context at 2048 tokens (Section 2.2). What happens with 8K or 32K context? The memory capacity analysis (channel overflow checking in Section 6.2) is opaque.