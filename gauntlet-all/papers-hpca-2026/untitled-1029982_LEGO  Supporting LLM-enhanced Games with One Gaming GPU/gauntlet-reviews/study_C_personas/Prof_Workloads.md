# LEGO: Supporting LLM-enhanced Games with One Gaming GPU

## Q1: Whiteboard Explanation

Let me walk you through LEGO as if I'm drawing this out for you.

**The Problem Setup:**
Imagine you're playing Black Myth: Wukong, and an LLM is controlling an enemy NPC. The game renders at 60 FPS (one frame every 16.6ms), while the LLM needs to generate combat actions at varying rates—100-300 Actions Per Minute (APM). At 100 APM, that's one action every 600ms.

*[Drawing two parallel timelines]*

Here's the core tension: Both tasks want the GPU, but they have wildly different deadlines. Rendering tasks are short (8-10ms each) but must complete every 16.6ms. LLM inference is longer (spread across multiple frames) but has a 200-600ms budget.

**The Key Observation:**
When I profile BlackMyth, I find the GPU is only 60.8% utilized—there's ~39% "headroom" sitting idle between and *within* rendering tasks. But here's the catch: Llama3-8B at 100 APM needs 41.9% of GPU time. The math doesn't work. And at 300 APM? Forget it.

*[Drawing the headroom gaps]*

**LEGO's Two-Part Solution:**

**Part 1: Layer-Skipping Adaptor (Algorithm Side)**
- When resources are tight, we skip some LLM transformer layers
- But naive skipping kills accuracy (skip 4 layers → accuracy drops below Llama3-3B)
- Key insight: Later transformer layers show high *inter-layer similarity* (Figure 8 shows cosine similarity approaching 0.9+ in layers 25-31)
- LEGO trains small FFN "adaptors" to distill knowledge from skipped layers—essentially, we approximate what those layers would have computed

**Part 2: Headroom-Maximizing Scheduler (System Side)**
- Predict total headroom across the next inference window using a simple linear regression model
- Key finding: Predicting *per-frame* headroom has 5.5% error; predicting *window-aggregate* headroom has only 0.6% error
- Split LLM inference into fine-grained subtasks (single transformer layers for decode, attention/FFN blocks for prefill)
- Exploit *intra-rendering* headroom—the GPU idles for 0.24ms on average *within* each rendering task due to game engine batching

The elegance is this: The predictor tells us how many layers we can afford to run, and the scheduler fills every GPU microsecond with useful work.

---

## Q2: The Key Insight

**The fundamental insight is that resource-driven layer skipping (not confidence-driven) requires knowledge distillation to remain viable.**

Existing layer-skipping methods like LITE and CALM use runtime confidence thresholds to decide "is this token confident enough to exit early?" But this approach fundamentally doesn't work for co-location scheduling because:

1. It provides no *guarantees*—Figure 5 shows 47.1% of inference tasks exceed their latency target even when average computation aligns with the budget
2. Forcing SLO compliance by early termination (LITE-S) causes 27.2% accuracy drop because you're skipping layers the model itself considers important

**LEGO flips the paradigm:** Instead of asking "which layers can I skip for *this token*?", it asks "given *X% resources*, which fixed set of layers should I pre-train to skip?"

The insight that makes this work is the **similarity heatmap observation** (Figure 8): consecutive later layers in transformers have very high output tensor similarity (layers 25-31 in Llama3-8B show cosine similarity >0.8). This isn't just correlation—it implies minimal unique knowledge contribution. You can approximate layers 26-29 with a single FFN and lose less than if you randomly skipped 4 layers.

This converts a runtime decision problem into an offline preparation problem, trading adaptor training time (36 hours) for deterministic latency bounds at runtime.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. The Benchmark Selection is Actually Reasonable**
Unlike many papers that pick obscure microbenchmarks, the authors use three commercially successful AAA games: Black Myth: Wukong, Final Fantasy XVI, and Red Dead Redemption 2. These represent different rendering workload profiles (Figure 3 shows BlackMyth uses 60.8% GPU time, RDR2 only 47.6%). Table I grounds the work in real industry adoption—16 games explicitly use runtime LLMs as of 2025.

**2. Multi-Dimensional SLO Evaluation**
Figure 12 reports *99th-percentile* FPS and APM, not averages. This is the right metric for latency-sensitive gaming. They show LEGO maintains 60 FPS and target APM across all 18 configurations (3 games × 2 models × 3 APM levels) while baselines fail catastrophically in 200/300 APM scenarios.

**3. End-to-End Real Gaming Validation**
Section VII-D's Street Fighter III tournament (Figure 13) is clever—they pit LLMs against each other in actual gameplay. LEGO-4 beats LITE-4 85% of the time at equivalent skip counts, validating that accuracy preservation translates to behavioral quality.

**4. Honest Comparison to Industry Practice**
Section VII-E directly compares against NVIDIA ACE's INT4-Nemotron3-4B. Showing 5-15% win rates against FP16 LEGO variants is a legitimate industry-relevant comparison.

### Weaknesses

**1. The Baseline (LITE-S) is a Strawman They Constructed**
Section II-D admits: "Building on the above experiment, we implement LITE-S, an extension of LITE that incorporates SLO constraints." They're comparing against their *own modification* of LITE, not LITE itself in a fair co-location setting. The 27.2% accuracy drop from LITE-S is self-fulfilling—they designed it to fail.

**2. Cherry-Picked APM Scenarios Avoid the Hard Cases**
They test 100/200/300 APM, but Section VII-I reveals the system breaks at 300 APM with 9 agents (batch inference takes 400ms vs. 200ms window). They acknowledge this limitation but bury it in a subsection. The 300 APM single-agent scenario is already operating at the edge—Table IV shows LEGO-13 (300 APM typical) drops to 42.0% on SQuAD, below the Llama3-3B baseline.

**3. The Headroom Prediction Evaluation is Underspecified**
Table II reports only aggregate error rates (0.44-1.31%). What's the *tail* error? If 5% of windows have 5% prediction error, that translates to SLO violations. The paper never shows a CDF of prediction accuracy or discusses how prediction errors propagate to layer-skipping decisions.

**4. The "Win Rate" Metric is Noisy and Game-Specific**
40 combat rounds per model pair (Figure 13) is statistically weak. With binomial variance, a 62.5% win rate has a 95% CI of roughly ±15%. The claim that "LEGO-4 consistently outperforms LEGO-8" is within noise margins. Moreover, Street Fighter III is vastly simpler than the AAA games they profile—it doesn't even use GPU rendering.

**5. Variable-Length Prompt Evaluation is Narrow**
Section VII-F tests uniform sampling in [256, 1024] tokens. Real gaming prompts include historical action sequences that grow over time. What happens at 2048 tokens? The prefill phase latency scales quadratically with sequence length—this would stress the scheduler in ways not shown.

**6. MoE Results Show Method Limitations**
Table V shows that for DeepSeek-V2-Lite at skip-9, MMLU accuracy drops from 56.6% to 45.1% (a 20% relative decline). Table VI shows that reducing top-k for MoE models doesn't scale inference time proportionally (top-k=1 still takes 84.47% of top-k=6 time). The method works less well for MoE architectures, which are becoming standard for efficient LLMs.

---

## Q4: What the Authors Didn't Tell You

**1. The 36-Hour Training Time is Per-Game, Not Amortized**
Section IV-C mentions "up to 14 LLM adaptors" and "total training time is approximately 36 hours" for BlackMyth. But every new game requires new rendering headroom profiling, new similarity analysis on game-specific prompts, and retraining all adaptors. For a game studio releasing across multiple titles, this doesn't amortize.

**2. The Memory Overhead is Actually Significant**
Section VII-K claims adaptors are 268.8 MB each, totaling 3.23 GB for 12 adaptors. On a 24GB RTX 4090 already running Llama3-8B (~16GB for FP16 weights + KV cache) and a AAA game (~4-6GB VRAM), you're at capacity. They don't discuss whether all adaptors must be resident simultaneously or if they can be dynamically loaded.

**3. The "Intra-Rendering Headroom" Depends on Engine Cooperation**
Section V-A reveals that intra-rendering headroom exists because game engines batch similar objects into subtasks. But this is engine-specific behavior. The authors implemented their system in Unreal Engine 4 (Section VI). Games using different engines (Unity, proprietary engines) or different rendering architectures (deferred vs. forward rendering) may have entirely different headroom patterns.

**4. The LR Model's 3-Window Lookback Assumes Temporal Stationarity**
Table II's prediction accuracy relies on the assumption that rendering workload in windows N-1, N-2, N-3 predicts window N. This fails during gameplay transitions: entering a boss fight from exploration, loading new areas, or cinematic cutscenes. Section V-D mentions "severe spikes" occur in only 1.2% of frames, but what about *gradual shifts* where the LR model persistently under/over-predicts for multiple windows?

**5. They Excluded Cloud Gaming Latency from the Comparison**
Section I claims cloud LLM services have 20-110ms network latency plus 300-700ms API overhead, making them unsuitable. But they never actually benchmark against a cloud service under realistic conditions. With edge inference servers (which NVIDIA is deploying for GeForce NOW), the latency equation changes. The "cloud is too slow" claim is asserted, not measured.

**6. The Accuracy Metrics Don't Match the Task**
They evaluate on MMLU, ARC-C, and SQuAD-2.0—benchmarks for language understanding and question answering. But the gaming task is action generation from game state (Figure 2). How does SQuAD accuracy correlate with "generate correct combat actions"? The footnote on Page 4 admits "there is a lack of mature, standardized datasets specifically tailored for LLM-based gaming." They're measuring the wrong thing and hoping it transfers.

**7. The Paper Glosses Over Quantization Interaction**
Section II-D dismisses quantization because "current GPUs only support limited formats, which means several fixed resource usage levels." But Section VII-K then claims "After applying INT4 quantization, LEGO can further support LLMs up to 30B parameters." Which is it? Can LEGO stack with quantization or not? The interaction between layer-skipping adaptors and quantized weights (especially mixed-precision) is never characterized.

**8. The "100% Win Rate" Claim is Methodologically Suspect**
Section VII-D concludes: "When applying real layer-skipping traces from BlackMyth, FFXVI, and RDR2, LEGO achieves a 100% win rate over the baselines due to its ability to maintain the target APM under limited resources." This compares a system that *meets* APM targets against baselines that *violate* them. The win rate measures APM compliance, not inference quality. A stopped clock achieving 0 APM would lose 100% of matches too—it's not a fair accuracy comparison.