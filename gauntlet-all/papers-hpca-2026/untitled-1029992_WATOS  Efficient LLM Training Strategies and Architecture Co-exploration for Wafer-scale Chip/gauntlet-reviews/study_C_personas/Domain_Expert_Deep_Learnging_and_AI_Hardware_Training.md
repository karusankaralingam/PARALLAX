## Q1: Whiteboard Explanation

Let me sketch out what WATOS is actually doing here.

**The Setup:** Imagine you have a 200mm × 200mm wafer—about the size of a dinner plate—packed with ~56 compute chiplets, each with its own HBM memory dies attached. Unlike a GPU cluster where you have fat NVLink pipes between separate boxes, here everything is on one wafer, connected via a 2D mesh of die-to-die (D2D) links running at ~4 TB/s per die. That's roughly 6× the bandwidth of NVLink at 5× lower latency (Section I, page 1).

**The Fundamental Problem:** You can't have it all. The wafer has ~40,000 mm² of usable area. Every mm² you spend on HBM dies is a mm² you *don't* spend on compute dies. More memory capacity = less compute power = less D2D bandwidth (since fewer die edges for interconnect). This is illustrated beautifully in Figure 6—three wafer configurations trading off storage vs. compute vs. communication.

**What WATOS Does (The Five-Step Co-Design):**

1. **Central Scheduler (§IV-A):** First, figure out if the model even *fits*. Can `(weights + gradients + optimizer_states) / num_dies < per_die_DRAM`? If not, prune immediately—don't waste time. Then enumerate (TP, PP) combinations that satisfy the 2D mesh topology constraints. The key insight: on a 2D mesh, TP=8 with ring all-reduce wastes links (Figure 5b shows underutilized links), while TP=4 often performs better.

2. **Recomputation Scheduler (§IV-B):** Here's where it gets clever. The 1F1B pipeline schedule creates *imbalanced* memory pressure—early stages hold activations for many micro-batches, late stages hold few. Naive recomputation creates "imbalance bubbles" (Figure 8a). WATOS uses dynamic programming to find the minimal recomputation that balances memory across stages while minimizing added compute. They identify "Senders" (memory-starved stages) and "Helpers" (memory-rich stages), then shuffle activations between them *on-wafer* rather than offloading to host.

3. **Memory Scheduler—Placement (§IV-C-1):** Given the Sender-Helper pairs, *where* do you physically place pipeline stages on the 2D mesh? Naive left-to-right, top-to-bottom placement (Figure 12a) creates 6-hop paths for activation balancing. WATOS co-locates Mem_pairs to get 4-hop paths, reducing communication cost by 30% (Equation 2 with conflict factor γ).

4. **Memory Scheduler—Allocation (§IV-C-2):** Fine-grained: which specific DRAM dies store which overflow activations? Since D2D bandwidth > DRAM bandwidth, cross-die DRAM access is DRAM-bound, not interconnect-bound. Algorithm 3 greedily allocates to nearest DRAM with capacity, dynamically reordering the priority queue.

5. **Genetic Algorithm Optimizer (§IV-D):** The greedy decisions in steps 2-4 can trap you in local optima. Five custom GA operators (Op1-Op5 in Figure 13) mutate recomputation configs, swap stage placements, and adjust Mem_pairs to escape. Fitness = `t_max × GlobalCost`.

**The Dataflow Detail:** At the core level, they implement hybrid dataflows (OS/WS/IS) switching based on EMA characteristics (Figure 15). TP communication uses bidirectional ring all-reduce on the 2D mesh. PP communication identifies shortest paths and assigns tasks to avoid congestion (Figure 14).

---

## Q2: The Key Insight

**The Real Contribution:** WATOS is the first framework to perform *joint optimization* of wafer-scale architecture parameters and LLM training parallelism/scheduling strategies under physical area constraints. The mechanism contributions are:

1. **Globally Coordinated Memory-Efficient Recomputation (GCMR):** Unlike naive per-stage recomputation, GCMR treats the entire wafer's DRAM as a unified pool. It uses DP to find minimum-latency recomputation that balances memory *globally*, then shuffles activations between "Sender" and "Helper" stages via the fast D2D mesh. This is only possible because D2D bandwidth (4 TB/s) >> DRAM bandwidth (2 TB/s), so cross-die transfers are DRAM-limited, not network-limited.

2. **Topology-Aware Placement under Area Constraints:** The insight that a 2D mesh penalizes large TP sizes (ring all-reduce underutilizes links at TP=8, Figure 5b) leads to the counterintuitive finding that *smaller TP with larger PP often wins* on wafer-scale chips—the opposite of GPU cluster wisdom where NVLink favors large TP.

3. **Co-Design Search Space:** The magic is in jointly exploring `(die_size, DRAM_capacity, D2D_bandwidth, TP, PP, recomputation_config, stage_placement, DRAM_allocation)`. Table I shows no prior work touches all these dimensions simultaneously.

**Why It Matters:** The authors show (Figure 2, step 3→4) that without this co-design, applying Megatron's GPU-optimal parallelism to a WSC leaves an 80% performance gap between real and potential throughput. The area constraint creates a fundamentally different optimization landscape than GPU clusters.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **Comprehensive Baseline Comparisons (Section V-C, Figure 17):** They compare against Megatron-GPU (8× Blackwell Ultra, 40,000 TFLOPS, scaled DRAM), Megatron-Wafer (Megatron scheduling on WSC), and Cerebras weight-streaming. The GPU baseline is *not* straw-man—it's a scaled-up Blackwell system with matched compute and memory. The 2.74× vs. MG-wafer and 1.53× vs. Cerebras gains are substantial.

2. **Ablation Study Structure (Figure 19):** They incrementally add components (+R recomputation, +M memory scheduler, +GA optimizer) and show each contributes. The insight that memory scheduling benefits *grow* with model size while central scheduler benefits *shrink* is well-reasoned.

3. **Multi-Model Coverage:** Dense (Llama2-30B, Llama3-70B, GPT-175B) and MoE (Gshard-137B, Deepseek-671B) models tested. Figure 20 extends to emerging architectures (Mamba, Qwen3-Next, Stable Diffusion, GR-24), demonstrating the framework isn't LLM-specific.

4. **Honest Architecture DSE (Figure 16):** They show Config 3 (moderate DRAM, balanced compute) consistently wins across models, but don't hide that Config 2 beats Config 4 *only* with recomputation. This nuance—that optimal architecture depends on whether you recompute—is a genuine insight.

5. **Robustness Analysis (Figure 23):** Fault tolerance under 20% die/link failure rates shows 18-35% throughput retention advantage. This is practical for real WSC deployment.

### Weaknesses:

1. **No End-to-End Training Convergence:** The paper reports *iteration time* and *throughput*, but never shows a model actually converging to target loss/accuracy. This is a significant gap for a training paper. Mixed precision (FP16 weights, FP32 optimizer) is standard, but the aggressive recomputation and activation shuffling across dies could introduce subtle numerical issues. Section V-A mentions "mixed precision training" but no convergence curves appear.

2. **Simulation-Only Evaluation:** The entire evaluation is on an extended Astra-sim simulator (§IV-F). While they validate the DNN predictor at 2.3% error (Figure 11b), they acknowledge "cycle-accurate simulators often require minutes to hours per run" and use lookup tables. There's no silicon, no real power measurements, no actual wafer tape-out. The 2.74× claim is entirely simulated.

3. **Baseline GPU Scaling Issues:** The GPU baseline (8× Blackwell Ultra) is a single node. They don't compare against multi-node DGX systems with NVLink + NVSwitch + InfiniBand, which is how GPT-175B is *actually* trained. The "equivalent compute" comparison ignores that real 175B training uses 100s-1000s of GPUs with mature software stacks.

4. **Conveniently Chosen Die Configurations (Table II):** Config 1-4 are "representative," but the selection criteria aren't exhaustive. Figure 26 shows a broader DSE, but the main results cherry-pick configs. Why 56 dies and not 64? Why these specific DRAM bandwidths?

5. **Power/Energy Absent:** No Perf/Watt numbers anywhere. WSC advocates (Cerebras) emphasize power efficiency as a key advantage over GPU clusters. The omission is glaring for an HPCA paper.

6. **Multi-Wafer Scalability (Figure 25a):** They test 4-wafer configurations but wafer-to-wafer bandwidth is 1.8 TB/s (SOTA) vs. 4 TB/s D2D *within* wafer. At Deepseek-671B scale, the W2W bottleneck likely dominates, but they only show results at 400 GB/s and 1.8 TB/s without analyzing where the cliff edge is.

7. **Search Time Overhead:** Section V-A mentions 0.274s per 100 GA steps, but doesn't report total exploration time for full DSE. How many iterations to converge? Figure 25b shows ω tradeoff but absolute times are missing.

---

## Q4: What the Authors Didn't Tell You

1. **The "2.74×" is Against a Crippled Baseline:** Megatron-Wafer uses Megatron's scheduling *designed for NVLink-connected GPUs* and applies it to a 2D mesh. Of course it underperforms—Megatron assumes all-to-all TP communication is cheap. The fairer comparison is Megatron-GPU at 1.92× (Figure 17), which is still strong but less eye-catching.

2. **Cerebras Comparison is Apples-to-Oranges:** Cerebras uses "weight streaming"—weights live off-chip, only activations on-wafer. WATOS assumes model weights fit on-wafer DRAM. For models that *don't* fit (say, a 671B dense model), WATOS would need multi-wafer communication, while Cerebras's architecture is designed for exactly this. The 1.53× advantage disappears if you're truly memory-limited.

3. **The DNN Predictor is Trained on... What?:** Figure 11(b) shows 2.3% latency error, but they don't disclose the training data source. If it's from their own analytical model, they're fitting noise. If it's from cycle-accurate simulation, why not just use that? The "DNN vs. Analytical 17.3% accuracy gain" claim (page 6) needs more scrutiny.

4. **Pipeline Bubble Overhead Hidden:** Figure 8 shows GCMR reducing bubbles, but the steady-state pipeline efficiency (n micro-batches vs. p stages) isn't reported. For p=14 PP stages (Deepseek-671B), you need n >> 14 micro-batches to amortize warmup/cooldown. What's the actual MFU (model FLOP utilization)?

5. **The "Moderate DRAM Capacity" Insight is Circular:** Config 3 wins because it balances compute/memory/bandwidth. But Config 3 was *selected* as a DSE candidate based on... what? If you'd picked Config 3.5 (between 2 and 3), might it be even better? The design space is continuous, but exploration is discrete.

6. **No Discussion of Yield:** Wafer-scale integration's elephant in the room. A single defective die doesn't kill the chip (§VI-D shows fault tolerance), but yield impacts cost. At 56 dies/wafer with ~90% per-die yield, you lose 5-6 dies on average. The placement algorithm (§IV-C-1) assumes all dies functional—what happens with a "Swiss cheese" wafer?

7. **HBM Placement Constraints Ignored:** Figure 4 shows HBM dies on the periphery of compute dies, but HBM has strict thermal and interposer constraints. You can't arbitrarily resize compute dies without redesigning the HBM integration. The "configurable hardware template" (§II-A) is more aspirational than practical.

8. **Activation Shuffling Latency:** The Sender→Helper checkpoint transfer happens "on-wafer," but at what point in the pipeline? If it's on the critical path (before backward pass needs the activation), latency matters. If it's overlapped, memory bandwidth for other operations is reduced. The paper doesn't quantify this tradeoff.

9. **GA Convergence Not Guaranteed:** Section IV-D claims the GA "ensures any point within the design space can be reached," but GAs are heuristics—they find *good* solutions, not *optimal* solutions. With 5 operators and a fitness function of `t_max × GlobalCost`, local optima are still possible. The ω parameter (Figure 25b) just controls exploration/exploitation, not optimality.

10. **The Real Competition is Software, Not Hardware:** For GPT-175B training, the bottleneck on GPU clusters is software (collective communication libraries, memory management, pipeline scheduling). Megatron-LM, DeepSpeed, and PyTorch FSDP have years of optimization. WATOS assumes a hypothetical WSC with equally mature software—a significant assumption.