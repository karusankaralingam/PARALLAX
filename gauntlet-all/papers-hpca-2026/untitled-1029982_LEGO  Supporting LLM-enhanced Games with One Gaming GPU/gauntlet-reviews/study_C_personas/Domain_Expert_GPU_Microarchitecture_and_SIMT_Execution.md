# Paper Deconstruction: LEGO

## Q1: Whiteboard Explanation

Let me draw you the picture of what's happening here, because the problem is actually quite simple once you strip away the jargon.

**The Setup:** You have a gaming GPU (RTX 4090). You want to run a game (like *Black Myth: Wukong*) AND have an LLM control an AI character simultaneously. The game needs 60 frames per second—that's a hard 16.6ms deadline per frame. The LLM needs to generate actions at some rate (Actions Per Minute, or APM)—100 APM means one action every 600ms, 300 APM means one every 200ms.

**The Core Problem:** The game doesn't actually use the GPU 100% of the time. Figure 3 (page 3) shows *BlackMyth* only uses about 60.8% of GPU time. But here's the catch—Llama3-8B at 100 APM needs 41.9% of GPU time. That's already more than the ~39% "headroom" available. At 300 APM? Forget it. And even if you *could* fit both, you can't just run them in parallel—they'd fight over the GPU and your frames would stutter.

**LEGO's Two-Part Solution:**

1. **Algorithm Side (The Adaptor):** Since you can't fit the full LLM, skip some transformer layers. But naive layer-skipping destroys accuracy (Figure 7 shows accuracy craters after ~4 layers). LEGO's trick: train a small FFN "adaptor" that *distills* the knowledge from the skipped layers. They use a similarity heatmap (Figure 8) to identify which *consecutive* layers are most similar (later layers tend to be)—those are the safest to skip. The adaptor learns to approximate what those layers would have done.

2. **System Side (The Scheduler):** The rendering headroom is fragmented—there's idle time *between* frames AND *within* frames (when the game engine is doing non-GPU work like batching objects). A linear regression model predicts total headroom over the next "inference window" (36 frames at 100 APM). Based on that prediction, the scheduler picks how many layers to skip, then slices the LLM inference into tiny subtasks that fit into the gaps—fine-grained (single transformer layers) for the small intra-frame gaps, coarse-grained for the larger inter-frame gaps.

**The Flow:** Predict headroom → Select layer-skipping strategy → Run LLM subtasks in the cracks between rendering → Both hit their deadlines.

---

## Q2: The Key Insight

The *real* contribution here is **not** layer-skipping (that's well-trodden ground) and **not** GPU time-slicing for co-location (PilotFish [66] did that). The insight is the **co-design** that links them:

**Insight #1: Resource-driven layer-skipping needs different machinery than accuracy-driven layer-skipping.**

Existing methods like LITE [58] and CALM [52] decide *per-token* whether to skip layers based on confidence thresholds. They optimize for average compute reduction. But in a hard real-time setting with SLOs, you need *guaranteed* latency. When you force LITE to meet an SLO by skipping layers preemptively, it skips layers its own mechanism says are important—27.2% accuracy drop (Section II-D-2, page 4).

LEGO flips this: the *system* tells the algorithm how much compute budget it has, and the algorithm has pre-trained adaptors ready for each budget level. The adaptor isn't making runtime decisions about importance; it's pre-learned to approximate the skipped layers' function. This decouples the "what to skip" decision (made offline via similarity analysis) from the "how much to skip" decision (made at runtime based on predicted headroom).

**Insight #2: Rendering headroom exists *within* frames, not just between them.**

Section V-A (page 6-7) and Figure 10 reveal that naive inter-frame scheduling leaves significant compute on the table. The game engine batches rendering work, creating small GPU-idle gaps *during* a frame. By monitoring rendering subtask boundaries and dispatching transformer-layer-sized LLM subtasks into these ~0.24ms average gaps, LEGO captures this "intra-rendering headroom." This is the scheduler's contribution—it's not just opportunistic gap-filling; it's *predictive* and *adaptive* to gap sizes.

**Insight #3: Predicting per-frame headroom is hard; predicting aggregate headroom over an inference window is easy.**

Figure 11 shows per-frame prediction errors of 3-5.5%. But Table II shows that predicting total headroom over 36 frames (100 APM window) achieves <1.5% error with simple linear regression. This enables the system to commit to a layer-skipping strategy upfront rather than reactively scrambling.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Real Games, Real Hardware, Real Metrics (Section VII):**
Unlike many systems papers that simulate everything, LEGO runs on an actual RTX 4090 with actual commercial games (*Black Myth: Wukong*, *Final Fantasy XVI*, *Red Dead Redemption 2*). They measure real 99th-percentile FPS and APM (Figure 12). This is credible.

**2. The Street Fighter Validation is Clever (Section VII-D, Figure 13):**
Win-rate heatmaps from actual LLM-vs-LLM combat in Street Fighter III provide a functional test of accuracy degradation. LEGO-4 beating LITE-4 (85% vs 15%) demonstrates that distillation-based skipping preserves task-relevant capabilities better than confidence-based skipping.

**3. They Compare Against the Right Baselines:**
- SmallModel (Llama3-3B): Tests whether you should just use a smaller model.
- LITE/CALM: Tests against state-of-the-art layer-skipping.
- NVIDIA ACE (Section VII-E): Tests against the industry's actual deployed solution.

The comparison showing FP16 Llama3-8B with LEGO-12 skipping beats INT4 Nemotron3-4B (NVIDIA ACE's approach) at 85% win rate is a strong result.

**4. Honest About MoE Limitations (Section VII-H, Table V):**
They don't hide that layer-skipping degrades more severely on MoE models (DeepSeek, Mixtral) because expert routing gets disrupted. This is intellectually honest.

### Weaknesses

**1. The "86.3% Accuracy Loss Reduction" Claim is Cherry-Picked:**
The abstract and contributions tout "reduces LLM inference accuracy loss by up to 86.3%." Looking at Table IV, this is comparing LEGO skipping 12 layers to LITE skipping 12 layers on SQuAD. But:
- At skip-4, LEGO's advantage is much smaller.
- On MMLU at skip-12, LEGO gets 66.3% vs LITE's 8.7%—that's dramatic, but LITE's numbers look suspiciously broken (14.3% at skip-4?). Did they misconfigure LITE's thresholds?
- The "up to" framing hides that in many configurations the gap is modest.

**2. The 300 APM Scenario is Barely Viable:**
Section VII-C admits that at 300 APM, LEGO skips 13 layers "in 80% of cases," and accuracy falls *below* Llama3-3B. The paper frames this as "LEGO meets SLOs while Llama3-3B doesn't"—but that's a Pyrrhic victory. Table VII (Section VII-J) shows 150 APM Llama3-8B only achieves 12.5% win rate against LEGO-12 at 200 APM, which they dismiss because "LLMs maintain accuracy under skipping while humans don't." This logic is circular.

**3. Workload Regularity:**
Games have relatively *predictable* rendering patterns (Figure 3 shows periodic, bounded variation). The LR model works because these workloads are smooth over 36-frame windows. Would this hold for games with highly dynamic scenes (explosions, particle effects, sudden camera changes)? Section V-D (page 8) claims "only 1.2% of frames exhibit spikes >50%," but this is for their three benchmark games. No robustness test against adversarial rendering patterns.

**4. Single LLM Agent Focus:**
Section VII-I shows that with 9 AI agents (batched inference), Llama3-3B is required, and 300 APM fails entirely. This limits applicability to complex multi-agent games (MOBAs, strategy games).

**5. Training Cost is Buried:**
Section IV-C-2 (page 6) mentions BlackMyth needs "up to 14 LLM adaptors" and "total training time is approximately 36 hours." For a game studio, this is non-trivial—and it's per-game, per-LLM. The 3.23 GB storage for 12 adaptors (Section VII-K) is also glossed over.

---

## Q4: What the Authors Didn't Tell You

**1. The Baseline Configuration for LITE/CALM is Suspicious:**
Table IV shows LITE achieving only 14.3% on MMLU when skipping 4 layers. The original LITE paper [58] doesn't report results this catastrophic. Either:
- The authors configured LITE's confidence thresholds incorrectly, or
- LITE genuinely fails on their specific prompt structure (512 input, 16 output tokens for gaming).

Either way, this deserves explanation. If LITE's thresholds weren't tuned for the gaming prompt distribution, that's an unfair comparison.

**2. They Don't Report End-to-End Latency Including Scheduling Overhead:**
The LR prediction takes 1.3ms (Section V-B), monitoring rendering subtasks has overhead (not quantified), and dispatching fine-grained subtasks requires coordination. For a 16.6ms frame budget, even 1-2ms of scheduler overhead is 6-12% of the budget. Figure 12 shows 99th-percentile metrics, but the scheduler's contribution to tail latency variance is never isolated.

**3. Memory Bandwidth Contention is Ignored:**
LLM inference is memory-bandwidth bound (especially in the decode phase). Rendering is also memory-intensive. Co-locating them means competing for HBM/GDDR bandwidth. The paper never discusses whether this creates interference beyond compute time-slicing. An RTX 4090 has 1TB/s bandwidth—is this saturated? Do the LLM subtasks cause cache thrashing that affects rendering quality?

**4. The "Intra-Rendering Headroom" Discovery Deserves More Scrutiny:**
The claim that 0.24ms average intra-frame gaps can be usefully filled with transformer layer execution (0.4ms per layer, Section V-C) seems contradictory. If the average gap is shorter than one layer's execution, how does this work without blocking the next rendering subtask? The paper says "feedback-driven scheduling" monitors subtask completion, but doesn't quantify the fraction of intra-frame gaps that are *actually* usable vs. too short.

**5. No Discussion of Game Engine Modifications:**
The implementation (Section VI) integrates LEGO into Unreal Engine 4 by monitoring "rendering task state variables." Commercial games ship with proprietary, closed engines. How would LEGO deploy on games not built on modifiable engines? The paper claims this is "designed for commercial game companies"—but those companies would need engine-level integration, which isn't always feasible.

**6. Quality of Generated Actions is Unmeasured:**
The win-rate experiments (Figure 13) measure whether one model beats another, but not whether the *gameplay* is good. An LLM that takes random but frequent actions might achieve high APM but create poor player experience. The paper assumes APM + accuracy correlates with gameplay quality, but this is untested with human evaluation.