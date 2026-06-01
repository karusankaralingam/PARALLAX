# WATOS Paper Deconstruction

## Q1: Whiteboard Explanation

Let me sketch this for you like we're at a whiteboard in my office.

**The Problem (in plain English):**
Imagine you have a 12-inch silicon wafer (~40,000 mm²) and you want to turn it into one giant "superchip" for training LLMs. Sounds great—massive compute, everything connected with blazing-fast die-to-die (D2D) links instead of slow cables between separate GPUs. But here's the catch: **you have a fixed pizza-sized piece of silicon, and everything must fit on it.**

Want more memory (DRAM chiplets)? You lose space for compute dies. Want more compute? You lose memory. Want wider D2D links? You need more edge real estate on each die, which means fewer DRAM connections. It's a zero-sum game, and nobody has figured out how to jointly optimize *what you build* (architecture) with *how you use it* (training strategy like Tensor Parallelism, Pipeline Parallelism, recomputation).

**The WATOS Solution (the napkin sketch):**

```
┌─────────────────────────────────────────┐
│           WAFER (~198mm × 198mm)        │
│  ┌─────┬─────┬─────┬─────┬─────┬─────┐  │
│  │Die+HBM│Die+HBM│Die+HBM│Die+HBM│...│  │
│  │  ←D2D→  │  ←D2D→  │  ←D2D→  │    │  │
│  └─────┴─────┴─────┴─────┴─────┴─────┘  │
│         2D Mesh Topology (≈4.5 TB/s)    │
└─────────────────────────────────────────┘
```

Each die has compute cores + local HBM. They talk via D2D links in a 2D mesh (up/down/left/right neighbors only—no all-to-all switch fabric like NVLink).

**The Magic in Three Moves:**

1. **Central Scheduler:** "Given this wafer config, what's the *actual* best TP×PP split?" Megatron says TP=8, PP=4 is optimal. WATOS says "hold on—on a 2D mesh, TP=8 causes terrible link underutilization during ring all-reduce because you're only using one 'ring' path." It finds TP=4, PP=8 is better (Section III-A, Fig. 5).

2. **GCMR Recomputation Scheduler:** In pipeline parallelism, early stages hoard activations (for backward pass later), while late stages sit nearly empty. This memory imbalance is *massive* (Fig. 5(c) shows Stage 1 at 90GB, Stage 8 at 30GB). WATOS balances this by:
   - Dynamically deciding *which* operators to recompute per stage
   - Shipping overflow checkpoints from "Senders" (memory-heavy stages) to "Helpers" (memory-light stages) *across the wafer*, not off-chip

3. **Location-Aware Placement:** When you pair a Sender with a Helper, *where* you physically place them on the 2D mesh matters. Fig. 12 shows naive placement = 6 hops; smart placement = 4 hops (30% reduction in communication cost).

**The Bottom Line:** WATOS treats the wafer as a constrained optimization problem—jointly searching over (1) how many HBM chiplets per die, (2) TP/PP configuration, (3) which activations to recompute, and (4) physical placement on the mesh. This is fundamentally different from taking Megatron's strategy and plopping it onto a wafer.

---

## Q2: The Key Insight

**The Real Contribution (The Delta):**

This paper's genuine novelty is **treating wafer-scale architecture and training strategy as a coupled optimization problem under hard area constraints**—and providing a concrete algorithmic framework to solve it.

Specifically:

1. **The Mechanism:** The core insight is that a 2D mesh topology fundamentally changes the optimal training configuration. Unlike GPU clusters with NVLink (which approximate all-to-all connectivity), a 2D mesh has *local* connectivity only. This means:
   - Large TP groups create pathological ring all-reduce patterns (Fig. 5(b))
   - Pipeline stages must be *physically placed* intelligently to minimize hop counts
   - Memory can be "borrowed" across dies at minimal cost because D2D bandwidth (4+ TB/s) exceeds DRAM bandwidth (2 TB/s)—so cross-die DRAM access is *DRAM-limited*, not D2D-limited (Section IV-C-2)

2. **The Policy Innovation (GCMR):** Algorithm 2 is the heart of the recomputation scheduler. It uses dynamic programming to find the globally optimal recomputation strategy that minimizes the *maximum* stage execution time (not average—this is crucial for pipeline balance). The insight: treat recomputation choices as a resource allocation problem where the "cost" is extra compute time and the "reward" is freed memory that can be used to reduce pipeline bubbles.

3. **Why This Matters:** Previous work either:
   - Optimized training strategy assuming fixed GPU hardware (Megatron, Alpa, DeepSpeed)
   - Optimized wafer architecture assuming fixed workload mapping (UCLA wafer-scale GPU work, PD topology paper)
   - Focused on inference, not training (WSC-LLM)

   Table I (page 5) is the authors' honest positioning: they're claiming the first "full checkmark row" for wafer-scale training co-design with recomputation awareness.

**What It Is NOT:**
- A new physical-layer interconnect technology
- A new network topology (they use standard 2D mesh)
- A new parallelism paradigm (still TP+PP+1F1B)

This is fundamentally a **scheduling and allocation paper** with architecture as a degree of freedom, not a circuits or packaging paper.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Honest Comparison Setup (Section V-C):**
The baseline comparison is carefully constructed. They normalize compute (40,000 TFLOPS for GPU, 39,648 TFLOPS for WSC—slightly *disadvantaging* the wafer). They scale GPU memory to 3920GB to match WSC. They don't just compare against a strawman GPU setup. This is good practice.

**2. Ablation Study (Section V-D, Fig. 19):**
The incremental ablation (+R → +M → +GA) shows each component adds value. Crucially, they demonstrate that **memory scheduling gains increase with model size** (2.5× for GPT-175B vs. 1.5× for Llama2-30B), which makes physical sense—larger models have deeper pipelines with more severe memory imbalance.

**3. Realistic Workload Mix:**
They test dense models (Llama, GPT) and MoE (Gshard, DeepSeek-V3). The MoE results are important because MoE communication patterns are more irregular.

**4. Fault Tolerance Analysis (Section VI-D, Fig. 23):**
They actually address yield/reliability, showing graceful degradation under 20% die fault rate. This is unusual for wafer-scale papers, which often ignore that real wafers have defects.

**5. Multi-Wafer Scalability (Section VI-F, Fig. 25):**
They demonstrate performance on multi-wafer configurations with realistic W2W bandwidth (1.8 TB/s from Dojo, 400 GB/s lower bound). This addresses the "what if my model is too big for one wafer" question.

---

### Weaknesses (The Skeletons)

**1. It's All Simulation (The Big One):**

The entire evaluation runs on **Astra-sim**, extended with DNN-based performance predictors (Section IV-F). There is no taped-out chip. The claim of "2.3% error" for their DNN predictor (Fig. 11(b)) is *only validated against what?*—presumably another simulation or analytical model, not silicon measurements. 

When they say "Config 3 is optimal" (Table II), this is optimal *within their simulation framework*. Real wafer-scale chips have signal integrity issues, clock domain crossing challenges, power delivery nightmares, and thermal hotspots that simulators typically miss. The 2-GHz Dojo-style cores at 7nm (Section V-A) are borrowed assumptions, not validated implementations.

**2. Power Budget Is Invisible:**

There is **no power analysis anywhere in the paper**. This is a glaring omission. A 56-die wafer with 39,648 TFLOPS of compute would draw *hundreds of kilowatts*. Questions they don't answer:
- What's the pJ/bit for D2D links?
- What's the power cost of recomputation vs. storing checkpoints?
- How does their "Config 3" compare to a GPU DGX system in performance/watt?

For a paper published at HPCA 2026, this absence is surprising. The area-constrained optimization ignores that thermal constraints may be the real limiter.

**3. Software Stack Handwaving:**

Section IV-E describes TP and PP "execution engines," but the actual programmability is unclear. How does a user express their model? Is there a compiler from PyTorch/JAX? The paper implies static mapping decisions made by WATOS's offline exploration, but:
- What happens for dynamic sparsity (like in DeepSeek-V3's MoE)?
- How do you handle variable sequence lengths?
- What's the reconfiguration overhead when switching models?

**4. Memory Model Simplifications:**

The DRAM allocation strategy (Algorithm 3) assumes you can freely ship activations between any Sender-Helper pair at the cost of hop distance. But:
- What about bank conflicts when multiple dies access the same Helper's DRAM?
- What about temporal coordination—when does the Sender ship activations, and when does the Helper receive them, in the 1F1B schedule?

The paper treats cross-die DRAM access as "DRAM-bandwidth-limited" (Section IV-C-2), which is optimistic. Real traffic patterns may cause NoC congestion that this model ignores.

**5. Comparison Against Cerebras Is Indirect:**

They compare against "Cerebras weight streaming strategy" (Section V-C), but Cerebras WSE-3 is a *monolithic* wafer-scale chip, not a chiplet-based integration. The paper's architecture (Fig. 4) is fundamentally different—discrete dies bonded via CoWoS, not a continuous fabric. The comparison is apples-to-oranges. 

Cerebras also has 44GB of on-chip SRAM (no HBM), whereas this paper assumes HBM-based memory. The "1.53× over Cerebras" claim (Abstract) is comparing their *simulated chiplet wafer* against their *simulated version of Cerebras's strategy*, not against actual Cerebras hardware.

**6. Yield Is Acknowledged But Not Quantified:**

Section VI-D shows fault tolerance, but they inject faults manually ("manually injecting failures"). Real yield models for chiplet-based integration would show a distribution of defects. They don't report:
- Expected yield for their 56-die configuration
- Cost per working wafer
- Whether their Config 3 is optimal when weighted by expected yield

---

## Q4: What the Authors Didn't Tell You

**1. The Search Space Is Still Huge:**

Despite the GA-based optimizer (Section IV-D), the design space is intractable. They explore:
- Die configurations (4 variants in Table II)
- TP sizes (1 to N)
- PP sizes (1 to N)
- Per-operator recomputation decisions
- Sender-Helper pairings
- Physical placements

Fig. 25(b) shows the optimizer takes 100 steps to converge. But this is for *one* model at *one* batch size at *one* sequence length. Training a real LLM involves dynamic batch sizes, long-context fine-tuning, varying parallelism during curriculum. **WATOS finds one static optimal configuration, not a runtime-adaptive system.**

**2. The "Moderate DRAM" Sweet Spot Might Be Fragile:**

Section V-B concludes Config 3 (70GB DRAM/die, 2 TB/s DRAM BW, 4 TB/s D2D) is "universal optimal." But look at Fig. 16—the performance differences between configs are often within 20%. This suggests:
- The "optimal" is sensitive to model architecture and hyperparameters
- A slight shift in workload (e.g., longer sequences, different batch sizes) could flip the ranking

The authors' own insight (Section V-B): "Config 2 excels with recomputation; Config 4 excels without." If your workload mix changes, your "optimal wafer" becomes suboptimal.

**3. The Megatron Comparison Isn't Entirely Fair:**

Section V-C says MG-GPU uses 8×Blackwell Ultra GPUs with 1.8 TB/s NVLink. But Blackwell NVL72 systems have **72 GPUs** at that bandwidth tier. They're comparing against an 8-GPU node, not the full NVL72 rack. Figure 1 teases a 56-die WSC vs. 56-GPU comparison, but the main evaluation (Fig. 17) uses 8 GPUs vs. 56 dies. This asymmetry inflates the wafer's advantage.

**4. 3D Stacking Is "Future Work" Hidden in Plain Sight:**

Section VI-E mentions "3D stacking variants" and acknowledges it "fundamentally shifts the memory access pattern." This is important because:
- TSMC's SoW-X (cited as [124]) uses 3D stacking
- Intel Ponte Vecchio uses Foveros
- The paper's planar chiplet model may be outdated by the time this wafer exists

The authors admit 3D stacking "decouples area competition between DRAM and compute dies"—which would *invalidate* their core area-constrained trade-off analysis.

**5. The DNN Predictor Is a Black Box:**

Figure 11(b) claims 2.3% error for latency prediction. But:
- What's the training data? (Presumably simulation outputs, creating circular validation)
- What's the model architecture? (Not specified)
- What's the extrapolation error for unseen configurations?

If the predictor is trained on the same simulator it's accelerating, errors compound. A real silicon measurement might show 20%+ discrepancy.

**6. The "1F1B" Constraint:**

Section II-B dismisses bidirectional PP schedules (Chimera, etc.) because they "intensify memory pressure." But recent work (after this paper's submission) shows interleaved schedules can outperform 1F1B for memory efficiency. By restricting to 1F1B, WATOS may miss better pipeline strategies.

**7. What About Inference?**

The entire paper is training-focused. But wafer-scale chips are often pitched for inference (Cerebras, Tesla Dojo inference mode). If you design a wafer "optimal" for training Config 3, what happens when you deploy it for inference? The compute-memory-bandwidth balance shifts dramatically. A co-design framework should arguably consider both workloads.

---

**Bottom Line:**

WATOS is a solid *framework paper* that formalizes the wafer-scale training co-design problem and provides algorithmic solutions. The insights about 2D mesh topology + 1F1B scheduling are genuinely useful. But the entire story rests on simulation, ignores power/thermal, and the "optimal configuration" is workload-specific. Treat the numbers as directional guidance for architecture exploration, not as production-ready specifications.