# Study C — Multi-Persona Synthesis
**Paper:** 1029982 LEGO  Supporting LLM enhanced Games with One Gaming GPU  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 07:30

---

# Q1: Whiteboard Explanation

LEGO addresses a concrete resource conflict: running both a AAA game (like *Black Myth: Wukong*) and an LLM (like Llama3-8B) on a single gaming GPU (RTX 4090). The game demands 60 FPS (16.6ms per frame), while the LLM must generate combat actions at 100-300 Actions Per Minute (APM), meaning one action every 200-600ms.

**The Fundamental Arithmetic Problem:**
Figure 3 (page 3) reveals that BlackMyth uses only ~60.8% of GPU time per frame—leaving ~39% "headroom." However, Llama3-8B at 100 APM requires 41.9% of GPU time. The math doesn't work: 60.8% + 41.9% = 102.7%. At 300 APM, the deficit worsens dramatically.

**LEGO's Two-Part Solution:**

**Part 1: Layer-Skipping Adaptor (Algorithm Side)**
When resources are tight, skip some transformer layers to reduce LLM compute. But naive skipping destroys accuracy—Figure 7 shows accuracy craters below Llama3-3B after ~4 layers skipped. LEGO's key observation (Figure 8, page 6): later transformer layers have high cosine similarity between inputs and outputs (>0.8 for layers 25-31 in Llama3-8B), meaning they contribute less unique information. LEGO trains small FFN "adaptors" via self-distillation to approximate what skipped layers would have computed. The adaptor learns the transformation `T_k → T_{k+n}` using MSE loss on hidden state activations.

**Part 2: Headroom-Maximizing Scheduler (System Side)**
The scheduler exploits two types of headroom:
- **Inter-rendering headroom**: Gaps between frames (~6.5ms if rendering takes 10ms)
- **Intra-rendering headroom**: GPU idle time *within* frames due to game engine batching (average 0.24ms per gap, up to 3.1ms total per frame—Section V-C)

A linear regression model predicts *aggregate* headroom over the entire LLM inference window (e.g., 36 frames for 100 APM). Table II shows this achieves <1.3% error, compared to 3-5.5% for per-frame prediction (Figure 11). Based on this prediction, the scheduler selects the appropriate adaptor/layer-skip configuration, then dispatches LLM subtasks opportunistically: fine-grained subtasks (~0.4ms single transformer layers) for intra-frame gaps, coarse-grained subtasks for inter-frame gaps.

**The Flow:** Predict headroom → Select layer-skipping strategy → Fill every GPU microsecond with useful work → Both workloads meet their deadlines.

---

# Q2: The Key Insight

The paper's fundamental contribution is recognizing that **resource-driven layer skipping requires fundamentally different machinery than confidence-driven layer skipping**, and that these two approaches have incompatible guarantees for real-time systems.

**The Problem with Existing Approaches:**
Prior methods like LITE [58] and CALM [52] make *per-token* decisions: "Is this token confident enough to exit early?" This optimizes for average compute reduction but provides no latency guarantees. Figure 5 demonstrates the catastrophic consequence: even when LITE's average computation aligns with the budget, 47.1% of individual inferences violate the SLO. Forcing SLO compliance by early termination (LITE-S) causes 27.2% accuracy drop because you're skipping layers the model itself considers important.

**LEGO's Paradigm Inversion:**
Instead of asking "which layers can I skip for *this token*?", LEGO asks "given *X% resources*, which fixed set of layers should I pre-train to skip?" This converts a runtime decision problem into an offline preparation problem. The decision of *how many layers to skip* is made *once* at inference start, based on predicted resource availability—not token difficulty.

**Why This Works (The Supporting Insight):**
The similarity heatmap observation (Figure 8) provides principled guidance: consecutive later layers have highly correlated input/output representations, meaning they contribute less unique information. This isn't just correlation—it implies the *delta* they contribute is small and approximable. LEGO exploits this by always targeting these high-similarity layers for skipping and training dedicated adaptors to recover lost information.

**The Architectural Elegance:**
The combination is powerful: the *system* makes a deterministic, resource-driven choice (making execution time predictable and schedulable), and the *algorithm* (the adaptor) ensures that choice doesn't catastrophically hurt accuracy. Prior layer-skipping methods are *token-adaptive* with variable execution times; LEGO is *resource-adaptive* with deterministic execution—a critical distinction for real-time scheduling.

---

# Q3: Evaluation Critique

## Strengths

**1. Real Hardware, Real Games, Real Metrics:**
All reviewers praised the evaluation's credibility. LEGO runs on an actual RTX 4090 with production games (*Black Myth: Wukong*, *Final Fantasy XVI*, *Red Dead Redemption 2*) at 4K/60FPS—not synthetic benchmarks. They report 99th-percentile FPS and APM (Figure 12), correctly capturing tail latency critical for interactive applications. Table I grounds the work in real industry adoption (16 games using runtime LLMs as of 2025).

**2. Strong Baseline Methodology:**
The comparison framework is rigorous. They test against smaller models (Llama3-3B), state-of-the-art layer-skipping (LITE, CALM), and industry solutions (NVIDIA ACE). Crucially, all baselines are augmented with PilotFish [66] for fair scheduling comparison (Section VII-A).

**3. The Street Fighter Validation (Section VII-D, Figure 13):**
Multiple reviewers highlighted this as "brilliant" and "clever." Having LLMs actually play against each other provides ground-truth evaluation beyond accuracy metrics. LEGO-4 beats LITE-4 with 85% win rate, demonstrating real-world gaming impact. The comparison showing FP16 Llama3-8B with LEGO-12 beats INT4 Nemotron3-4B (NVIDIA ACE) at 85% win rate is particularly strong.

**4. Honest Ablation and Limitation Acknowledgment:**
The paper systematically ablates layer-skipping accuracy (Figure 7, Table IV), headroom prediction models (Figure 11, Table II), and headroom utilization (Figure 15, showing 28.6% more headroom captured). They honestly acknowledge MoE limitations (Section VII-H, Table V) where layer-skipping disrupts expert routing.

## Weaknesses

**1. The "86.3% Accuracy Loss Reduction" Claim is Cherry-Picked:**
This headline number compares LEGO skipping 12 layers to LITE skipping 12 layers on SQuAD specifically. At skip-4, advantages are much smaller. Multiple reviewers noted LITE's numbers look "suspiciously broken" (14.3% on MMLU at skip-4?)—raising questions about baseline configuration.

**2. The 300 APM Scenario is Marginal:**
Table IV shows LEGO-13 (typical for 300 APM) achieves only 42.0% on SQuAD—below Llama3-3B baseline. Section VII-D explicitly omits 300 APM results from Street Fighter experiments. Section VII-I shows the system fails entirely with 9 agents at 300 APM. The paper frames meeting SLOs while accuracy degrades as success, but this is arguably a Pyrrhic victory.

**3. The LITE-S Baseline is a Strawman:**
Section II-D admits LITE-S is the authors' *own modification* of LITE for SLO constraints—not a published method. The original LITE paper doesn't report results as catastrophic as shown. This raises fairness concerns about baseline configuration.

**4. Training and Storage Overhead is Understated:**
Section IV-C states 36 hours training time for "up to 14 adaptors" per game—this is per-game, per-LLM, requiring retraining if models update. Storage totals 3.23 GB for 12 adaptors (Section VII-K). On a 24GB RTX 4090 already running Llama3-8B (~16GB) and a AAA game (~4-6GB VRAM), capacity becomes tight.

**5. Variable-Length and Long-Context Evaluation is Limited:**
Main evaluation uses fixed 512 input / 16 output tokens. Section VII-F tests only up to 1024 tokens with uniform sampling. Real gaming prompts (dialogue histories, quest logs) may require 4K+ tokens where prefill scales quadratically.

**6. Memory Bandwidth Contention is Ignored:**
LLM decode is memory-bandwidth bound; rendering is also memory-intensive. The paper profiles compute time but never analyzes whether fragmented scheduling causes cache thrashing or bandwidth saturation on the RTX 4090's 1TB/s interface.

---

# Q4: What the Authors Didn't Tell You

**1. The "Intra-Rendering Headroom" is Engine-Specific:**
The observation that rendering subtasks leave GPU gaps (Section V-A, average 0.24ms) depends entirely on how Unreal Engine 4 batches draw calls. Different engines (Unity, proprietary), rendering pipelines (forward vs. deferred, ray tracing), or more aggressively optimized engines could have completely different gap distributions. The paper evaluates only UE4 games—generalization is unproven.

**2. The Scheduling Relies on Polling, Not Interrupts:**
Section VI describes monitoring "rendering task state variables"—a polling-based approach consuming CPU cycles with unquantified latency. True zero-overhead scheduling would require GPU-side preemption unavailable on consumer GPUs. The 1.3ms LR prediction overhead (Section V-B) is reported, but total scheduler overhead including monitoring and dispatch coordination is never isolated.

**3. The Similarity Heuristic Lacks Theoretical Grounding:**
The claim that high cosine similarity implies "reduced contribution of unique knowledge" (Section IV-B) is empirically observed, not theoretically justified. Why doesn't lower similarity between early layers (0.4 in heatmap) hurt more than high similarity between later layers? Why must skipping be *contiguous*? Only experimental claims are provided.

**4. KV Cache Management During Layer Skipping is Unaddressed:**
When skipping layers 25-29, what happens to KV cache for those layers? The paper mentions LITE uses "KV replication" causing accuracy loss (Section VII-C), but never explicitly states whether LEGO's FFN adaptor eliminates KV cache needs for skipped layers—potentially a significant unreported memory benefit (~40% KV cache reduction).

**5. Why Not Speculative Decoding?**
A glaring omission: speculative decoding using a small draft model (Llama3-3B) to generate candidates verified by the large model could achieve similar latency reductions without custom adaptors. No comparison or discussion is provided.

**6. The 60 FPS Assumption is Critical but Unstated:**
The entire system targets 16.6ms frame deadlines. Modern games increasingly target 120 FPS (8.3ms deadline) or variable refresh rates. At higher frame rates, inter-rendering headroom shrinks proportionally and intra-rendering gaps become harder to exploit. Scalability is undiscussed.

**7. Quality of Generated Actions is Unmeasured:**
Win-rate experiments measure whether one model beats another, not whether gameplay is *good*. An LLM taking random but frequent actions might achieve high APM but create poor player experience. The assumption that APM + accuracy correlates with gameplay quality is untested with human evaluation.

**8. The Practical Deployment Model Creates DevOps Burden:**
The workflow requires game companies to fine-tune LLMs, build similarity heatmaps, train 14+ adaptors, and ship ~20GB+ packages per game. If base LLMs need updating (safety, capabilities), the entire adaptor pipeline must re-run. This practical burden is ignored.