## Q1: Whiteboard Explanation

Let me walk you through WATOS like I'm explaining it at the whiteboard.

**The Problem Setup:**
Imagine you have a 198mm × 198mm wafer (~40,000 mm²) and you want to train a 175B parameter LLM. The fundamental constraint is this: every square millimeter you allocate to DRAM is a square millimeter you *cannot* use for compute dies, and vice versa. This is the core trade-off illustrated in Figure 6 (page 3).

**The Architecture Template:**
Looking at Figure 4 (page 3), the WSC is a 2D mesh of chiplets. Each compute die contains:
- A 16×16 or 18×18 array of Dojo-style cores (each ~2 TFLOPS FP16)
- Shared SRAM per core (1.25 MB)
- HBM chiplets attached to the die edges
- D2D (die-to-die) links on all four edges (~12 TB/s total per die)

The key insight is that D2D bandwidth >> DRAM bandwidth, so cross-die communication can often be hidden behind DRAM access time.

**The Training Strategy Problem:**
When you do 1F1B pipeline parallelism (Figure 8, page 4), early pipeline stages must hold activations for p-s micro-batches while waiting for backward passes. This creates massive memory imbalance—stage 0 might need 100GB while stage 7 needs 30GB. Traditional Megatron just recomputes everything when memory is tight, but that's wasteful.

**WATOS's Three-Stage Solution:**

1. **Central Scheduler (Algorithm 1, page 6):** Generates all feasible (TP, PP) configurations. The key pruning rule: if `modelP/MP > C` (your model params don't fit even distributed), prune immediately. If checkpoints overflow, delegate to the recomputation scheduler.

2. **GCMR Recomputation (Algorithm 2, page 7):** This is clever. Instead of uniform recomputation, WATOS uses dynamic programming to decide *which* operators to recompute on *which* stages. The DP table T[t,m] stores the minimum bubble time for stages t through pp-1 using memory budget m. It identifies "Senders" (memory-starved stages) and "Helpers" (stages with spare memory) and pairs them.

3. **Location-Aware Placement (Algorithm 3, page 8):** This exploits the 2D mesh topology. The objective function in Equation 2 (page 8) minimizes pipeline communication distance *plus* checkpoint transfer distance *plus* a conflict penalty γ when paths overlap. Figure 12 (page 7) shows this reducing average hops from 6 to 4.

**The Hardware "Trick":**
The magic is that D2D bandwidth (4-4.5 TB/s in Table II) exceeds DRAM bandwidth (1-2.5 TB/s). So when stage 1 ships overflow checkpoints to stage 8's DRAM, the transfer is DRAM-bandwidth-limited, not D2D-limited. This means you can use *any* die's DRAM as if it were local, at the cost of DRAM access time, not network latency.

---

## Q2: The Key Insight

**The Core Architectural Insight:**
The authors discovered that **wafer-scale D2D bandwidth is so much higher than DRAM bandwidth that cross-die memory can be treated as "pseudo-local" storage**. This fundamentally changes the training memory management problem from a local per-die constraint to a global wafer-wide constraint.

Specifically, from Section IV-C-2 (page 8): *"WSCs feature high D2D bandwidth, typically exceeding DRAM access bandwidth. This means that cross-die DRAM read and write operations are limited by DRAM bandwidth rather than D2D bandwidth."*

**Why This Matters:**
Traditional GPU clusters face the "stranded memory" problem—if GPU 0 has 50GB free and GPU 7 needs 20GB more, you cannot simply use GPU 0's memory because inter-node bandwidth is the bottleneck. On WSCs, you *can*, because the ~4.5 TB/s D2D bandwidth dwarfs the ~2 TB/s DRAM bandwidth.

**The Training Strategy Insight:**
The second key insight is that **optimal TP size on WSCs is smaller than on GPU clusters** (Section III-A, Figure 5, page 4). Megatron recommends TP=8, PP=4 for 32 dies, but the actual optimal on WSC is TP=4, PP=8. This is because Ring All-Reduce on a 2D mesh with TP=8 underutilizes links (Figure 5b), while smaller TP groups achieve better link utilization.

**The Co-Design Insight:**
Different recomputation strategies require different compute/memory/communication ratios (Figure 9, page 5). Type 0 (no recompute) needs high memory but low compute. Type 1 and Type 2 trade memory for compute differently. By co-exploring architecture (how much DRAM per die?) with training strategy (which operators to recompute?), WATOS finds configurations that neither pure architecture DSE nor pure training optimization would discover.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Comprehensive Baseline Comparisons (Section V-C, Figure 17):**
The authors compare against three relevant baselines: Megatron-GPU, Megatron-Wafer (applying GPU strategies to WSC), and Cerebras weight streaming. The 2.74× improvement over Megatron-Wafer is meaningful because it isolates the benefit of WSC-aware scheduling from the raw hardware advantage.

**2. Ablation Study Quality (Section V-D, Figure 19):**
The incremental ablation (+R, +M, +GA) clearly shows each component's contribution. The observation that "memory-aware scheduler gains increase with model size" (page 11) is well-supported—larger models have deeper pipelines with worse memory imbalance.

**3. Resource Utilization Analysis (Figure 18, page 11):**
The heatmap showing 75% DRAM utilization for WATOS vs 25% for Megatron-Wafer is compelling evidence that the memory scheduling actually works. The compute die utilization time series (80% vs 40%) directly supports the throughput claims.

**4. Multi-Model Generality (Figure 20, page 11):**
Testing on Mamba-2.8B (state space model) and Stable Diffusion demonstrates the framework isn't overfitted to standard Transformers.

### Weaknesses

**1. Simulated Hardware, Not Real Silicon:**
The entire evaluation runs on ASTRA-sim (Section IV-F, page 9). While the authors cite prior validation work, the 56-die WSC with specific D2D/DRAM bandwidths is hypothetical. The Tesla Dojo numbers they derive from [130] may not transfer to their topology.

**2. The GPU Baseline is Artificially Constrained:**
In Section V-C (page 11), they scale MG-GPU's DRAM from 2304 GB to 3920 GB "to match the WSC." This is generous to WSC—real Blackwell systems have 288GB per GPU, not the scaled-up values.

**3. Missing Power/Energy Analysis:**
For a system claiming datacenter relevance, there's no power consumption data. A 56-die WSC at 2 GHz with 18×18 cores per die is likely consuming 10-50 kW. The efficiency (TFLOPS/W) comparison against GPUs is absent.

**4. DNN Predictor Accuracy Concerns:**
Figure 11(b) (page 6) shows 2.3% timing error and 1.6% memory error for the DNN predictor. However, these errors compound across 30+ operators and 8-16 pipeline stages. The end-to-end accuracy validation is missing.

**5. Limited Fault Tolerance Evaluation:**
Section VI-D (page 13) addresses robustness, but only with synthetic fault injection. Real WSCs have significant yield challenges—20% die fault rate (their test case) represents a catastrophic yield scenario, yet they don't discuss how this affects the area budget or cost.

**6. No Comparison to Cerebras WSE-2/WSE-3:**
Despite citing Cerebras extensively, they only compare to "Cerebras weight streaming strategy" abstractly. No direct comparison to published Cerebras training throughput numbers.

---

## Q4: What the Authors Didn't Tell You

### The Hidden Hardware Costs

**1. The Configurable Hardware Template Assumes Ideal Packaging:**
Figure 4 shows HBM chiplets adjacent to compute dies, but CoWoS interposer size is limited. The claim of "6 HBM chiplets per die" in Wafer 0 (Figure 6) would require either massive interposers or hybrid bonding. The authors cite [47] for CoWoS but don't address the 2500 mm² interposer limit mentioned in that reference.

**2. NoC Complexity is Hand-Waved:**
Each die has a 2D mesh NoC internally *and* D2D links externally. The 12 TB/s D2D bandwidth (page 10) assumes all 4 edges are utilized, but the NoC-to-D2D routing overhead isn't modeled. A 16×16 core array communicating with 4 edge D2D ports creates significant contention.

**3. The DRAM Bandwidth Numbers Are Optimistic:**
Table II shows 2 TB/s DRAM bandwidth for Config 3. With HBM3 at ~819 GB/s per stack, this implies 2-3 HBM stacks per die. But each HBM stack requires ~100 mm² of interposer area for the PHY and redistribution layers—area that competes with the 40,000 mm² wafer budget.

### The Training Strategy Fine Print

**4. GCMR Doesn't Handle Activation Fragmentation:**
Algorithm 2 assumes activations can be moved in bulk between Sender and Helper. In practice, activations have complex tensor shapes and may require reshaping for efficient transfer. The communication model (Equation 1, page 7) uses a simple α-β model that ignores this.

**5. The GA Convergence Isn't Guaranteed:**
Figure 25(b) shows the GA converges in ~50-100 steps, but the paper admits "naive greedy co-design strategies are prone to getting trapped in local optima" (page 2). The fitness function `tmax × GlobalCost` is non-convex, and the 5 genetic operators (page 8) may not provide sufficient coverage of the joint architecture-strategy space.

**6. Recomputation Profiling is Offline:**
Section IV-B states profiling is "conducted offline" (page 7). This means the DNN predictor must be retrained for new model architectures, new batch sizes, or new hardware configurations—the framework isn't truly plug-and-play.

### What They Glossed Over

**7. The 2.62× Communication Reduction (Figure 1) is Cherry-Picked:**
This comparison uses a specific parallelism configuration (D(1)T(4)P(14)) on Deepseek-671B. The average improvement across all configurations in Figure 1 is much smaller for LLaMA3-70B.

**8. Multi-Wafer Scaling is Problematic:**
Section VI-F (page 13) admits WATOS-4 (400 GB/s W2W bandwidth) still outperforms, but the scaling beyond 4 wafers isn't addressed. The 1.8 TB/s W2W bandwidth from [130] requires specialized optical interconnects that aren't commercially available.

**9. The Area Trade-Off Analysis (Figure 6) Ignores Yield:**
Larger compute dies have exponentially worse yield. A 25.5mm × 25.2mm die (~640 mm²) at 7nm might have <50% yield, while smaller dies achieve >90%. The cost implications of Config 2-4 (larger dies, fewer HBMs) are never discussed.