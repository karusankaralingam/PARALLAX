# LEGO: Supporting LLM-enhanced Games with One Gaming GPU

## Q1: Whiteboard Explanation

Imagine you're playing *Black Myth: Wukong* and you want an AI companion that can react like a human player. The problem? Your single RTX 4090 needs to do two things simultaneously: render gorgeous 4K frames at 60 FPS, and run an 8-billion parameter LLM to generate combat actions.

**The Resource Gap:**
- Rendering at 60 FPS means each frame has a 16.6ms deadline
- BlackMyth only uses ~61% of GPU time for rendering (Figure 3 shows max render time ~10.1ms)
- But that 39% "headroom" isn't enough—Llama3-8B at 100 APM needs 41.9% of GPU time (Section I)
- At 300 APM (professional player speed), you need actions every 200ms, making the gap worse

**The Core Insight:**
There's *hidden* GPU idle time *inside* each rendering task, not just between them. Game engines batch similar objects, creating gaps between rendering subtasks (Figure 10). LEGO exploits both inter-frame and intra-frame headroom.

**The Two-Part Solution:**

*Algorithm Side — Layer-Skipping Adaptor:*
Instead of running all 32 transformer layers, skip some based on available resources. But naive skipping kills accuracy (Figure 7 shows 4 layers skipped drops accuracy below a 3B model baseline). LEGO trains small FFN "adaptors" that distill knowledge from skipped layers. Key observation: later layers have high inter-layer similarity (Figure 8 heatmaps), so skipping consecutive later layers loses less information.

*System Side — Headroom-Maximizing Scheduler:*
1. Predict total headroom for next LLM execution window using Linear Regression on previous 3 windows (Table II: <1.3% error)
2. Choose layer-skipping strategy based on prediction
3. Split LLM inference into fine-grained subtasks (individual layers ~0.4ms) to fill fragmented intra-frame gaps
4. Switch to coarse-grained subtasks between frames

The result: maintain 60 FPS *and* 100-300 APM simultaneously, with up to 86.3% less accuracy loss than existing layer-skipping methods (Section VII-C).

---

## Q2: The Key Insight

**The Key Insight:** GPU headroom in gaming is not just the obvious gaps between rendering frames—substantial idle time exists *within* individual rendering tasks due to game engine batching optimizations, and this intra-task headroom can be systematically harvested for LLM inference through fine-grained subtask scheduling.

**Why This Matters:**
The naive view (Section II-C, Figure 1) assumes you can only use the ~6.5ms gap between frames. But profiling with nsight-system reveals that rendering tasks themselves contain multiple subtasks with auxiliary operations that don't use the GPU (Section V-A, Figure 10). The paper reports average intra-rendering headroom of 0.24ms per gap, with total intra-rendering headroom averaging 1.39ms per frame, maxing at 3.1ms.

**Why Others Missed This:**
Prior GPU co-location work (PilotFish [66]) monitors only frame completion, not the internal structure of rendering pipelines. Existing layer-skipping methods (LITE [58], CALM [52]) optimize for *average* computation reduction without guaranteeing per-request SLOs—LITE causes 47.1% of requests to miss latency targets (Figure 5).

**The Conceptual Shift:**
The paper reframes the problem from "how to reduce LLM computation uniformly" to "how to match LLM computation granularity to available resource fragments." This requires:
1. Predictable layer skipping (resource-oriented, not confidence-based)
2. Subtask granularity matching headroom granularity (~0.4ms transformer layers for intra-frame, multi-layer chunks for inter-frame)
3. Aggregate headroom prediction rather than per-frame prediction (Table II shows window-level LR achieves 0.6% average error vs. >3% for per-frame prediction in Figure 11)

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Real Hardware, Real Games:**
Evaluation runs on an actual RTX 4090 with DirectX 12 (Table III), using production games (BlackMyth, FFXVI, RDR2) at 4K/60FPS—not synthetic benchmarks. This is rare for systems papers and dramatically increases credibility.

**2. Comprehensive APM Coverage:**
Testing at 100, 200, and 300 APM (Section VII-B, Figure 12) covers casual to professional player speeds, demonstrating LEGO handles the full spectrum. The 99th percentile metrics (not just averages) are reported, showing tail latency discipline.

**3. End-to-End Gaming Validation:**
The Street Fighter III experiments (Section VII-D, Figure 13) provide ground-truth evaluation—models actually play against each other. LEGO-4 beats LITE-4 with 85% win rate, demonstrating real-world gaming impact beyond accuracy metrics.

**4. Ablation Quality:**
The paper systematically ablates:
- Layer-skipping accuracy vs. number of layers (Figure 7, Table IV)
- Headroom prediction models (Figure 11, Table II)
- Intra vs. inter-frame headroom contribution (Figure 15)

**5. Artifact Reproducibility Signals:**
Implementation uses llama.cpp (commit fc83a9e specified), UE4 with DirectX 12. The layer-skipping adaptor training time is quantified (36 hours for 14 adaptors—Section IV-C).

### Weaknesses

**1. No RTL Validation or Cycle-Accurate Simulation:**
All timing measurements rely on wall-clock profiling via nsight-system. The claimed 0.4ms transformer layer execution time (Section V-C) is empirical measurement, not validated against GPU microarchitecture models. Kernel launch overhead, memory bandwidth contention during co-location, and SM scheduling behavior are treated as black boxes.

**2. Limited GPU Coverage:**
Only RTX 4090 is tested. The paper claims "LEGO does not rely on any specialized hardware features" (Section VII-A) but provides no evidence—no testing on RTX 3080, AMD GPUs, or mobile GPUs where the resource constraints would be more severe.

**3. Rendering Workload Assumptions:**
The profiling assumes high visual settings remain constant. Section V-D claims sudden spikes affect only 1.2% of frames, but this is game-dependent. Boss fights, particle effects, or scene transitions could invalidate the LR prediction model. The "re-prediction after first token" mechanism (Section V-D) is mentioned but not systematically evaluated.

**4. Fixed Input/Output Length Assumption:**
The main evaluation uses 512 input tokens and 16 output tokens throughout (Section II-B). While Section VII-F tests variable-length prompts ([256, 1024]), this still excludes the long-context scenarios increasingly common in gaming (dialogue trees, quest histories). Table III doesn't report what happens beyond 1024 tokens.

**5. Layer Similarity Analysis is Empirical Only:**
The claim that consecutive later layers can be skipped because of high similarity (Figure 8) lacks theoretical justification. The 2400 samples from WebInstruct dataset (Section IV-B) may not generalize to actual gaming prompts.

**6. Multi-Agent Limitations Acknowledged but Not Resolved:**
Section VII-I admits LEGO cannot support Llama3-3B at 300 APM with 9 agents. For MOBA-style games (Dota, LoL), this is the primary use case—making LEGO inapplicable to a major game genre.

---

## Q4: What the Authors Didn't Tell You

### The Simulation/Measurement Infrastructure

**1. Profiling Methodology Black Box:**
The paper uses nsight-system (Section V-A) but never describes the profiling granularity. Are they using CUDA events? GPU timestamps? Nsight trace markers? The claimed 0.24ms average intra-rendering headroom (Section V-C) has no confidence intervals or measurement error analysis. With sub-millisecond scheduling decisions, measurement noise could be significant.

**2. No Warm-Up Period Discussion:**
LLM inference typically requires KV-cache warm-up. The paper assumes cached prefill states but doesn't discuss cold-start scenarios when the LLM hasn't been invoked recently. How does the first inference request after a cutscene perform?

**3. CUDA Stream/Context Switching Costs:**
Switching between rendering (DirectX 12) and inference (CUDA via llama.cpp) involves driver overhead. Section VI mentions "engine monitors rendering task state variables"—but the polling frequency and interrupt latency are unspecified. The paper's claim that scheduling overhead is "1.3ms with three input windows" (Section V-B) only covers the LR model, not the context switch costs.

### What's Abstracted Away

**4. Memory Bandwidth Contention:**
LLM inference is memory-bandwidth bound during decode phase. Game rendering also hammers VRAM for textures. The paper shows no memory bandwidth utilization curves or analysis of whether headroom utilization causes memory pressure that increases rendering time.

**5. GPU Power/Thermal Throttling:**
Running both workloads simultaneously on a 450W TDP card could trigger thermal throttling. No discussion of power measurements or thermal behavior during sustained co-location.

**6. OS Scheduling Interference:**
Section VI mentions integrating llama.cpp into UE4, but Windows 11 GPU scheduling (WDDM 2.7+) has its own preemption policies. The paper assumes deterministic scheduling but doesn't verify this against OS-level GPU preemption events.

### The Training Pipeline

**7. Adaptor Training Cost Buried:**
36 hours training time (Section IV-C) for one game seems reasonable, but this uses the same dataset for similarity heatmap construction and adaptor training. For game companies with unique mechanics (PUBG Ally's companion behavior, inZOI's NPC generation—Table I), creating suitable training data is the actual bottleneck, not the FFN training.

**8. No Continual Learning:**
What happens when game patches change the rendering pipeline? The similarity heatmaps and trained adaptors assume static models. No discussion of adaptor updates post-deployment.

### Missing Comparisons

**9. No NVIDIA ACE Head-to-Head on Same Tasks:**
Section VII-E compares against INT4 Nemotron3-4B in Street Fighter III, but NVIDIA ACE targets dialogue generation (Table I: "Conversational NPCs via NVIDIA ACE"). The comparison cherry-picks the combat scenario where action speed matters more than response quality.

**10. Quantization Dismissed Too Quickly:**
Section II-D claims "GPUs only support limited formats" making quantization inflexible. But AWQ, GPTQ, and other weight-only quantization methods provide multiple precision points. The paper's Table VI shows MoE models with varying top-k as the only flexibility example—but doesn't compare FP16 layer-skipping against INT4 full model.