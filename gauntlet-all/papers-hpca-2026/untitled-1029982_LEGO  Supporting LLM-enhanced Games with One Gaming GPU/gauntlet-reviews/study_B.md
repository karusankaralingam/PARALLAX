# Study B — Rich Directive
**Paper:** 1029982 LEGO  Supporting LLM enhanced Games with One Gaming GPU  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-05-31 19:25

---

Q1: Whiteboard Explanation

Imagine you're playing a modern game like Black Myth: Wukong, and you want an AI companion powered by an LLM to fight alongside you. The LLM needs to generate actions at human-like speeds—say 100-300 actions per minute—while the game renders at 60 FPS on the same GPU.

Here's the core problem: The game uses about 60% of GPU time for rendering, leaving ~40% "headroom." But running Llama3-8B at 100 APM needs 42% of GPU time—there's a gap. At higher APM levels (200-300), the resource deficit grows dramatically. Cloud offloading isn't viable due to 20-110ms network latency violations.

LEGO solves this through algorithm-system co-design:

**Algorithm Side (Layer-Skipping Adaptor):**
- Not all transformer layers contribute equally. Later layers show high inter-layer similarity in their output tensors—meaning they add little new information.
- When resources are scarce, skip contiguous blocks of these "redundant" layers (e.g., layers 25-29 for 4-layer skip).
- Critical innovation: Train a small FFN "adaptor" to distill knowledge from skipped layers using MSE loss. This preserves accuracy far better than naive skipping (86.3% less accuracy loss vs. LITE at 12 layers skipped).

**System Side (Headroom-Maximizing Scheduler):**
- Key insight: Headroom exists not just *between* rendering frames but *within* them (game engines batch similar objects, creating internal GPU idle gaps averaging 0.24ms each, totaling ~1.4ms per frame).
- Use a simple Linear Regression model to predict total headroom across the next LLM inference window (36 frames at 100 APM). This works because individual frame variance averages out—prediction error is only 0.6%.
- At runtime: dispatch fine-grained LLM subtasks (single transformer layers ~0.4ms) into intra-frame gaps, switch to coarse-grained execution during inter-frame headroom.

The result: Both 60 FPS rendering and target APM are maintained across all tested scenarios on an RTX 4090.

Q2: The Key Insight

The central insight is that **temporal aggregation fundamentally changes the predictability of fragmented GPU headroom**, enabling resource-aware layer skipping that would otherwise cause SLO violations.

Prior layer-skipping methods (LITE, CALM) make per-token decisions based on confidence thresholds, optimizing average skip rates. But gaming requires *guaranteed* SLO compliance per inference—47% of LITE inferences exceed latency targets because individual token behavior is unpredictable. Forcing early exits for SLO compliance causes 27% accuracy drops because it skips "important" layers.

LEGO inverts this: instead of token-level decisions, predict aggregate headroom across the entire inference window (12-36 frames). Individual frame rendering times vary significantly (Figure 3 shows 5-10ms swings), but the *sum* over many frames is remarkably stable—LR prediction error drops from 3-5% per-frame to 0.6% per-window.

This stability enables *proactive* layer-skipping selection: knowing total available compute before inference starts, choose exactly how many layers to skip upfront. The adaptor-based distillation then preserves accuracy for that specific skip configuration.

The second key insight is that intra-rendering headroom—previously invisible to schedulers—doubles effective utilization. Game engine optimizations create GPU bubbles within frames that can absorb fine-grained inference subtasks without delaying rendering.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Comprehensive co-location validation**: Testing across 3 games × 2 LLMs × 3 APM levels (18 scenarios) with consistent 99th-percentile FPS/APM measurements demonstrates robustness. The Street Fighter III win-rate evaluation (Section VII-D) provides compelling end-to-end evidence that accuracy metrics translate to gameplay outcomes.

2. **Strong baselines**: Comparing against Llama3-3B (same compute as 12-layer-skipped Llama3-8B), LITE, CALM, and NVIDIA ACE provides fair comparisons across the solution space. The head-to-head win rates (95% Llama3-8B vs LITE-4) are particularly convincing.

3. **Headroom characterization**: Figure 3's 30-minute traces and the discovery of intra-rendering headroom (1.39ms average per frame) are valuable empirical contributions. The 28.6% improvement in headroom utilization (Figure 15) validates the scheduling gains.

4. **Prediction model justification**: Table II's comparison of ARIMA/SVM/LR at different granularities rigorously justifies the window-level LR approach. The inference overhead analysis (1.3ms) addresses practical deployment concerns.

**Weaknesses:**

1. **Limited GPU diversity**: All experiments use RTX 4090. The claim "does not rely on any specialized hardware features" (Section VII-A) is untested. Mid-tier GPUs (RTX 3060/4060) with tighter margins would stress the approach differently.

2. **Adaptor training cost glossed over**: 36 hours for 14 adaptors on BlackMyth is mentioned only in passing. For games with different headroom profiles, retraining is required. The WebInstruct training dataset may not generalize to game-specific reasoning.

3. **MoE evaluation is incomplete**: Table V shows significant accuracy degradation for DeepSeek/Mixtral at higher skip levels (45.1%/59.9% on MMLU at skip-9/12). The claim MoE is supported is weakened—these models may need different techniques.

4. **Variable-length prompt handling is underspecified**: Section V-D mentions integrating duration predictors from prior work but provides no evaluation of prediction accuracy or its impact on layer-skipping decisions.

5. **Sudden spike handling**: The claim that 1.2% spike frames don't affect window-level prediction isn't validated with pathological cases (e.g., boss fights with sustained rendering increases).

Q4: What the Authors Didn't Tell You

**Memory pressure is the elephant in the room**: Each FFN adaptor is 268.8MB, totaling 3.23GB for 12 adaptors. Combined with Llama3-8B weights (~16GB FP16) and the game's VRAM requirements (Black Myth at 4K recommends 12GB+), an RTX 4090's 24GB is nearly exhausted. This approach likely fails on 12GB GPUs (RTX 4070/4080) that comprise most of the gaming market.

**The APM metric conflates action frequency with action quality**: Table VII attempts to address this but actually undermines the framing—150 APM Llama3-8B loses to 200 APM LEGO-12 despite theoretically higher action quality. This suggests the real metric should be something like "effective APM" accounting for action coherence, which the paper never measures.

**Prompt construction is hand-waved**: The 512 input/16 output token assumption (Section II-B) is stated as "representative" without justification. Real game states may require significantly more context (multi-agent tracking, extended history), and the prefill phase scales quadratically with sequence length.

**Adaptor layer selection heuristic may be fragile**: Using cosine similarity to identify skip-worthy layers assumes similarity implies redundancy. But high similarity could also indicate critical residual connections that *should not* be disrupted. The paper provides no ablation studying alternative skip positions.

**Commercial deployment path unclear**: The paper targets "commercial game companies" but doesn't discuss: (1) how to handle model updates without retraining adaptors, (2) anti-cheat implications of exposing LLM inference APIs, or (3) how offline adaptor training handles the diversity of player hardware configurations.

**The 300 APM scenario is effectively unsupported**: Table IV shows skip-13 drops to 42% F1 on SQuAD (vs. 70.1% baseline), and Section VII-I admits LEGO "cannot support Llama3-3B at 300 APM" for multi-agent scenarios. Professional-level play (300 APM) appears beyond current feasibility despite being highlighted in the abstract.