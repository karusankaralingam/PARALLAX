# WATOS Paper Analysis: Prof. Bench's Evaluation Methodology Audit

## Q1: Whiteboard Explanation

Let me break down what WATOS actually does, stripping away the marketing language.

**The Core Problem:**
Wafer-scale chips (WSCs) are essentially multiple chiplets bonded onto a single wafer substrate (~40,000 mm² usable area). Unlike GPU clusters connected via NVLink, WSCs have a fundamental constraint: **compute dies, memory (HBM), and interconnects all compete for the same fixed wafer area**. More memory = less compute = fewer D2D links.

**The Solution Architecture:**
WATOS is a design space exploration (DSE) framework that jointly optimizes:

1. **Hardware Configuration** - How many compute dies vs. HBM chiplets to place on the wafer
2. **Parallelism Strategy** - Tensor Parallelism (TP) and Pipeline Parallelism (PP) settings
3. **Recomputation Scheduling** - Which activations to discard and recompute to save memory
4. **Memory Placement** - Where to physically place pipeline stages on the 2D mesh to minimize communication hops

**The Key Mechanism:**
The framework uses a hierarchical scheduler:
- Central Scheduler generates feasible TP/PP configurations
- Recomputation Scheduler uses dynamic programming to decide which activations to checkpoint vs. recompute (GCMR strategy)
- Memory Scheduler pairs "Sender" stages (memory-constrained) with "Helper" stages (memory-rich) and optimizes physical placement
- A genetic algorithm (GA) then searches across remaining configurations

**Think of it as:** A compiler that takes an LLM architecture and a wafer area budget, then outputs (a) the optimal die configuration, and (b) the training schedule that maximizes throughput.

---

## Q2: The Key Insight

**The Genuine Technical Contribution:**

The paper's core insight is that **the 2D mesh topology of WSCs fundamentally changes optimal parallelism settings compared to GPU clusters**. Specifically:

1. **Smaller TP is better on 2D mesh** (Section III-A, Figure 5): Megatron recommends TP=8, but WATOS finds TP=4 outperforms because Ring All-Reduce on a 2D mesh with TP=8 leaves links underutilized. The paper shows a "real optimal" vs. "MG optimal" gap in Figure 5(a).

2. **Memory imbalance across pipeline stages is severe** (Section III-A, Figure 5(c)): In 1F1B scheduling, early pipeline stages store far more activations than later stages. On memory-constrained WSCs, this creates a "Sender-Helper" pairing opportunity where excess checkpoints from stage 0 can be offloaded to stage 7's unused DRAM via the high-bandwidth D2D links.

3. **The compute-memory-bandwidth trade-off creates a non-obvious optimum** (Section III-B, Figure 6): The paper identifies that Config 3 (moderate DRAM, high compute density) consistently wins—not the config with maximum memory or maximum compute.

**Why this matters:** GPU-centric training frameworks like Megatron assume fully-connected topologies and abundant memory. WSCs break both assumptions simultaneously, requiring co-design of the hardware configuration and training strategy.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Baseline Comparisons (Section V-C, Figure 17)**
The paper compares against four baselines:
- Megatron-GPU (actual GPU cluster)
- Megatron-wafer (Megatron strategy on WSC)
- Cerebras weight streaming
- WATOS

This is methodologically sound—they don't just compare against a strawman.

**2. Ablation Study (Section V-D, Figure 19)**
The ablation clearly isolates contributions:
- Baseline → +R (Recomputation) → +M (Memory Scheduler) → +GA (Global Optimizer)
Each component shows incremental gains, with recomputation and memory scheduling contributing more for larger models. This is exactly how ablations should be structured.

**3. Hardware DSE Across Multiple Configurations (Section V-B, Figure 16)**
They evaluate 4 distinct wafer configurations across multiple models with and without recomputation, showing Config 3 wins consistently. This demonstrates the DSE framework actually explores meaningful trade-offs.

**4. Diverse Workload Coverage (Section V-A, VI-C)**
Models range from 30B to 671B parameters, including dense (Llama, GPT) and MoE (Gshard, Deepseek-v3) architectures, plus emerging models like Mamba and Stable Diffusion in Figure 20.

---

### Weaknesses

**1. The "Equal Compute" Comparison is Misleading (Section V-C)**

The paper claims fairness by matching compute power: "WSC is configured with equivalent compute power" (Section V-C, page 11). But look closely:

- **MG-GPU**: 8× Blackwell Ultra GPUs = 40,000 TFLOPS, 2304 GB HBM
- **WSC**: 39,648 TFLOPS, 3920 GB DRAM

They **scaled MG-GPU's memory from 2304 GB to 3920 GB** to "ensure fairness." This is artificial—real Blackwell systems don't have 3920 GB. The WSC has **70% more memory** than the actual GPU configuration. Since LLM training is often memory-bound, this inflates WSC's apparent advantage.

**2. The Baseline WSC Configurations are Not Real Products**

Table II shows 4 hypothetical wafer configurations. None correspond to Cerebras WSE-3 or Tesla Dojo's actual specifications. The paper states the compute die is "Dojo-style" (Section V-A), but Dojo has fundamentally different memory architecture (SRAM-dominant, 11GB per die vs. 48-96GB DRAM per die in WATOS).

**This is simulation against simulation, not validation against real hardware.**

**3. Cherry-Picked Parallelism Configurations (Section III-A)**

Figure 5(a) shows Megatron's "optimal" is (TP=8, PP=4) for 32 dies, but WATOS finds (4,8) is better. However:
- Did they search Megatron's full configuration space on WSC?
- The paper admits they "derived optimal parallelism settings recommended by the Megatron framework" but Megatron was designed for NVLink topologies, not 2D mesh.

**A fairer comparison** would be to give Megatron topology-aware configuration (which exists in recent Megatron versions) rather than using GPU-optimal settings on WSC.

**4. The 2.74× Speedup Claim Needs Decomposition**

The abstract claims "2.74× average throughput improvement over Megatron." But Figure 17 shows:
- Llama2-30B: ~1.8×
- Llama3-70B: ~2.2×
- Gshard-137B: ~2.5×
- GPT-175B: ~3.5×

The average is skewed by GPT-175B. **Where does 2.74× come from?** The paper doesn't specify whether this is geometric mean, arithmetic mean, or which models are included.

**5. The Recomputation Overhead Comparison is Incomplete**

Figure 17 shows "Recomp Throughput" separately from "Throughput," but:
- WATOS's recomputation proportion is "30% to 60% of MG-Wafer" (Section V-C)
- But they don't show the **wall-clock training time** comparison including all overheads

**Throughput can be misleading**: if WATOS does more recomputation but hides it better, the raw throughput metric doesn't capture total energy or time-to-convergence.

**6. Missing Real-World Validation Signals**

- No power consumption comparison (WSCs have severe cooling constraints)
- No yield analysis (wafer-scale chips have defective dies)
- Figure 23 shows fault tolerance, but only through **manual fault injection**, not actual defect statistics
- No comparison of model convergence or training accuracy—only throughput

---

## Q4: What the Authors Didn't Tell You

**1. The Memory Bandwidth Bottleneck is Buried**

Section III-C, Figure 9(b) quietly reveals that different recomputation strategies have wildly different "storage" vs. "communication" vs. "computation" footprints. The paper optimizes for throughput, but **never quantifies the DRAM bandwidth utilization** during steady-state training.

Figure 18 shows "DRAM Memory Utilization" heatmaps, but these are **capacity utilization**, not **bandwidth utilization**. If the DRAM bandwidth is saturated, adding more compute dies won't help—a possibility the paper doesn't address.

**2. The Genetic Algorithm's Convergence Guarantees are Weak**

Section IV-D claims the GA "ensures that any point within the design space of WSC can be reached from any other point." This is theoretically true for ergodic GAs, but:
- Figure 25(b) shows ω=0 (slowest convergence) achieves ~8% higher final performance than ω=1 (fastest)
- They use only **100 exploration steps** (Section V-A)
- The design space is combinatorially explosive (recomputation decisions × parallelism × placement × memory allocation)

**There's no guarantee they found the global optimum**, only a local one. The "near-optimal" claim is unsubstantiated.

**3. The Comparison Against Cerebras is Unfair**

The paper compares against "Cerebras weight streaming strategy" but:
- Cerebras WSE uses **on-chip SRAM**, not HBM chiplets
- Cerebras's actual training approach involves weight streaming from external memory, which has fundamentally different performance characteristics
- The paper applies "Cerebras strategy" to their **own hypothetical WSC architecture**, not to actual Cerebras hardware

This is comparing WATOS's strategy on WATOS's hardware against Cerebras's strategy on WATOS's hardware—not a real product comparison.

**4. The Multi-Wafer Scaling Results Hide Communication Costs**

Section VI-F shows multi-wafer performance (Figure 25(a)), but:
- "WATOS-18" uses 1.8 TB/s W2W bandwidth (matching Tesla Dojo's claimed specs)
- "WATOS-4" uses 400 GB/s (matching typical inter-node bandwidth)

But they don't show **how performance degrades as wafer count increases**. For Deepseek-v3 (671B), they use 4 wafers. What happens at 8 or 16 wafers? The W2W communication could become the bottleneck, but this scaling analysis is absent.

**5. The "First Work" Claim is Overstated**

The paper claims "to the best of our knowledge, this is the first work to enable such joint optimization" (Section II, page 2). But:
- TMAC [147] (cited in the paper) explores recomputation trade-offs on WSCs
- Calculon [50] does training-aware system co-design
- WSC-LLM [159] does LLM architecture co-exploration on WSCs

The paper's novelty is the **combination** of these techniques, not any single technique being "first."

**6. The Roofline is Never Shown**

For a paper about compute-memory-bandwidth trade-offs, there's no roofline model analysis showing where different workloads fall relative to the hardware's capabilities. This would immediately reveal whether workloads are compute-bound, memory-bandwidth-bound, or communication-bound—critical information for understanding when WATOS's optimizations matter.