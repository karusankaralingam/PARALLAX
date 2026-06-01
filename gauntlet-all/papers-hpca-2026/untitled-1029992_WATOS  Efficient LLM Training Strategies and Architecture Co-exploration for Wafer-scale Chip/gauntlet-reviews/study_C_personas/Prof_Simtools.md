## Q1: Whiteboard Explanation

WATOS tackles a fundamental problem: training massive LLMs requires enormous compute, memory, and bandwidth, but wafer-scale chips (WSCs) have a fixed ~40,000 mm² silicon budget that forces painful tradeoffs between these resources.

**The Core Insight:** Unlike GPU clusters where you can just add more nodes, on a wafer, every mm² of HBM you add steals area from compute dies and D2D interconnects. The authors recognize that existing training frameworks like Megatron are optimized for GPU topologies with fully-connected NVLinks, not the 2D mesh interconnects of WSCs.

**The Three-Level Co-Design:**

1. **Architecture Level:** A configurable hardware template that explores different compute/memory/D2D bandwidth ratios. Figure 6 shows three wafer configs—Wafer 0 maximizes memory (6 HBM chiplets per die), Wafer 2 maximizes D2D bandwidth, Wafer 1 balances both.

2. **Training Strategy Level:** Instead of blindly applying Megatron's recommended TP/PP settings (which assume fully-connected topologies), WATOS searches for parallelism configurations that actually work on 2D meshes. Section III-A shows that Megatron's "optimal" TP=8 leads to link underutilization during Ring All-Reduce (Figure 5(b)), while TP=4 with PP=2 achieves better balance.

3. **Memory Scheduling:** The 1F1B pipeline schedule creates severe memory imbalance—early pipeline stages must retain activations for p-s microbatches (Figure 8(a)). WATOS introduces:
   - **GCMR (Globally Coordinated Memory-Efficient Recomputation):** Dynamic programming to find which activations to recompute vs. store (Algorithm 2)
   - **Sender-Helper pairing:** Offloads excess checkpoints from memory-constrained stages to memory-rich ones *within the wafer*, not to external host memory
   - **Topology-aware placement:** Minimizes D2D hops for both pipeline communication and activation balancing (Figure 12)

**The Search Problem:** The design space is combinatorially explosive (TP/PP configs × recomputation choices × placement strategies × memory allocation). WATOS uses genetic algorithms with five custom operators (Op1-Op5 in Figure 13) to escape local optima.

---

## Q2: The Key Insight

The key insight is that **wafer-scale chips fundamentally change the optimization landscape for LLM training because compute, memory, and communication resources compete for the same fixed silicon area—and this creates co-optimization opportunities that don't exist in traditional GPU clusters.**

In a DGX system, you can independently scale memory (add more GPUs), compute (add more GPUs), and bandwidth (upgrade interconnects). But on a wafer, these are zero-sum: "Expanding on-wafer memory capacity necessarily reduces the silicon budget available for compute resources, and vice versa" (Section I, page 1).

This constraint creates a **new design knob**: recomputation doesn't just save memory—it shifts the optimal architectural balance. Figure 16 shows this beautifully: Config 2 (high compute, low memory) performs *worse* without recomputation but *better* with it. Config 4 (low compute, high memory) shows the opposite behavior. This means the "best" wafer architecture depends on your training strategy, and vice versa.

The authors distill this into a concrete finding (Section V-B): "A WSC with moderate per-die DRAM capacity and high compute density can effectively balance compute, memory, and communication demand during LLM training." This isn't obvious—you might expect more memory is always better for memory-hungry LLM training, but the co-design reveals otherwise.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Comprehensive DSE across realistic architectural points:** Table II presents four physically plausible WSC configurations with concrete parameters (die counts, DRAM bandwidth, D2D bandwidth). The authors don't just assume an idealized wafer—they explore the tradeoff space systematically.

2. **Honest baseline comparisons:** Figure 17 compares against MG-GPU (Megatron on 8×Blackwell Ultra GPUs with scaled resources for fairness), MG-wafer (Megatron's scheduling on WSC), and Cerebras weight streaming. The 2.74× and 1.53× improvements over MG-wafer and Cerebras respectively are meaningful because these baselines represent real deployment strategies.

3. **Ablation study isolates contributions:** Figure 19 shows incremental gains from each component (+R, +M, +GA). Notably, the recomputation scheduler's benefit *grows* with model size while the central scheduler's benefit *shrinks*—this matches intuition about memory pressure scaling.

4. **Model generality validation:** Figure 20 tests on Generative Recommender, Stable Diffusion, Mamba, and Qwen3-Next (linear attention). This addresses the "LLM-only" criticism and shows the framework handles diverse workloads.

5. **Robustness analysis:** Figure 23 quantifies graceful degradation under link/die faults (18% and 35% throughput improvement over non-robust baseline at 20% fault rates). This is critical for wafer-scale systems where defects are inevitable.

### Weaknesses

1. **The simulation infrastructure is underspecified.** Section IV-F mentions extending ASTRA-sim with "detailed modeling" and using a "DNN model to predict execution latency and memory footprint" (Section IV-B). But Figure 11(b) shows this DNN predictor achieves only 2.3% timing error—yet there's no validation against RTL or real silicon. The analytical model has 19.6% error, so the DNN is clearly better, but **we have no ground truth for the DNN itself**.

2. **The hardware template lacks process-technology grounding.** Section V-A states compute dies operate at "2 GHz" on "TSMC's 7nm process" based on Tesla Dojo. But Dojo is real silicon from 2022; claiming 708 TFLOPS per die (Config 2-4) would require validation. The paper doesn't discuss yield assumptions despite wafer-scale defect rates being notoriously high.

3. **The GA-based optimizer's convergence claims are weak.** Figure 25(b) shows 100 exploration steps, but the y-axis only spans 1.0-1.4× normalized throughput. The "performance gap" between ω=0 and ω=1 is ~8%—is 100 steps actually sufficient for the "global optimum" claim (Section IV-D)? No comparison against simulated annealing or other metaheuristics.

4. **Memory bandwidth accounting is suspicious.** Table II lists Config 3 with 2 TB/s DRAM bandwidth and 4 TB/s D2D bandwidth. Section V-C scales MG-GPU's DRAM to 3920 GB "to match the WSC." But HBM3 stacks provide ~1.2 TB/s per stack; 70 GB per die with 2 TB/s bandwidth would require exotic configurations. These numbers feel aspirational rather than validated.

5. **The multi-wafer scaling evaluation is limited.** Section VI-F uses only 4 wafers with 1.8 TB/s W2W bandwidth (citing Tesla [130]). But Figure 25(a) shows WATOS-4 (400 GB/s W2W) still beats Megatron—this suggests the *intra-wafer* optimizations dominate, and the multi-wafer story is underdeveloped.

---

## Q4: What the Authors Didn't Tell You

### 1. The Simulation-Reality Gap is Enormous

The entire evaluation runs on an extended ASTRA-sim simulator (Section IV-F). The authors train a DNN predictor for operator latency/memory with 2.3%/1.6% error (Figure 11(b)), but **these errors are against the simulator itself, not physical measurements**. The "ground truth" for training this DNN came from cycle-accurate simulations that "require minutes to hours per run"—which themselves have unknown fidelity to actual wafer-scale silicon.

Worse, the paper validates against Tesla Dojo parameters (Section V-A) but Dojo is a *real chip* while WATOS simulates a *hypothetical configurable template*. There's no mention of:
- DRAM refresh modeling (critical for HBM utilization)
- Thermal throttling under sustained training loads
- Actual defect rates and repair mechanisms
- Power delivery constraints at wafer scale

### 2. The "Configurable Hardware Template" is Paperware

Section II-A describes a "highly configurable hardware template" with parameters like (XC, YC), (XM, YM), die counts, etc. But **no implementation exists**. The template is purely analytical—you can't download it, synthesize it, or validate it. Table I compares against Timeloop, Hecaton, etc., but those tools have public codebases. The artifact availability question looms large.

### 3. The DNN Predictor Training Data is Never Disclosed

Section IV-B states "we train a DNN model to predict the execution latency and memory footprint of each operator." But:
- How many training samples? 
- What operator configurations were covered?
- How does prediction error extrapolate to unseen model architectures?

Figure 11(c) shows operator-level profiling for Llama-65B, but the DNN was presumably trained on a subset. The "operator-centric" generality claim (Section VI-C) rests on this predictor working for Mamba's state-space operators and Qwen3-Next's linear attention—neither of which appear in the training set description.

### 4. The Genetic Algorithm Operators are Heuristically Chosen

Section IV-D describes five GA operators (Op1-Op5) for mutation/crossover. The paper claims these "enable any feasible configuration to evolve into another through valid transformations"—but **this ergodicity claim has no proof**. Are there unreachable configurations? How sensitive is convergence to operator probabilities? Figure 25(b) shows ω controls elitism vs. diversity, but the optimal ω isn't derived from any principled analysis.

### 5. Communication Modeling Assumes Perfect Routing

Section IV-E-2 describes inter-stage communication using "shortest paths" with a "punishment factor" for contention. But the actual routing algorithm is unspecified. On a real 2D mesh:
- Deadlock avoidance requires dimension-ordered routing
- Hotspots emerge under collective communication patterns
- Congestion is highly workload-dependent

The α-β model (Equation 1) is fine for first-order analysis but ignores injection rate limits, buffer depths, and credit-based flow control—all of which affect real D2D performance.

### 6. The Baseline Comparisons Have Asymmetric Advantages

Section V-C scales MG-GPU's memory from 2304 GB to 3920 GB "to match the WSC" for fairness. But:
- MG-GPU's NVLink bandwidth stays at 1.8 TB/s while the WSC has 4 TB/s D2D
- MG-GPU's topology is fully-connected; WSC is 2D mesh
- The paper doesn't show what happens if MG-GPU gets equivalent *aggregate* bandwidth

The comparison proves WATOS + WSC beats Megatron + GPU, but doesn't isolate whether the gains come from the hardware (more D2D bandwidth), the software (better scheduling), or both.