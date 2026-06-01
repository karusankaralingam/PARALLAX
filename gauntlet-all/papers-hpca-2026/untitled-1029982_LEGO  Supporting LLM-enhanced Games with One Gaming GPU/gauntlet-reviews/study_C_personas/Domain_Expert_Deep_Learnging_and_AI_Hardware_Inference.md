# LEGO: Supporting LLM-enhanced Games with One Gaming GPU

## Q1: Whiteboard Explanation

Imagine you're playing *Black Myth: Wukong* and you want an AI companion powered by an LLM like Llama3-8B to control an enemy or give you real-time tactical advice. The problem? You only have one RTX 4090, and it needs to do *both* render your beautiful 4K/60fps game *and* run a multi-billion parameter language model.

**The Core Tension:**
- Your game needs to render a frame every 16.6ms (for 60 FPS). Miss that deadline, and the player sees stutter.
- Your LLM needs to generate an action every 200-600ms (depending on whether the AI plays like a newbie or a pro). This is measured in "Actions Per Minute" (APM): 100 APM means one action every 600ms; 300 APM means one every 200ms.

**The Naive Problem:** When the game runs alone, it only uses ~60% of the GPU time per frame. There's ~40% "headroom" (idle GPU time). But running Llama3-8B at 100 APM needs ~42% of the GPU time. That's already more than the headroom, and at 300 APM it's far worse. You *cannot* just run both side-by-side.

**LEGO's Two-Pronged Solution (Algorithm + System):**

1.  **The Algorithm Side: A "Layer-Skipping Adaptor"**
    *   **The Intuition:** If you have limited GPU cycles, make the LLM faster by skipping some of its internal transformer layers. But naively skipping layers destroys the model's knowledge.
    *   **LEGO's Trick:** They observe that the *later* layers in LLMs like Llama have highly similar input and output tensors (see the heatmaps in Figure 8). This means those layers aren't adding much *new* information. So, LEGO identifies which *consecutive* block of layers is least impactful. Then, they train a tiny Feed-Forward Network (FFN) "adaptor" to act as a *shortcut* that mimics what those skipped layers would have done. It's like a lightweight "knowledge distillation" module baked into the model itself (Figure 9). They pre-train adaptors for skipping 4, 8, 12, etc. layers.

2.  **The System Side: A "Headroom-Maximizing Scheduler"**
    *   **Observation 1:** Headroom isn't just the gap *between* frames. It also exists *within* a single rendering task, because the game engine itself has GPU-idle phases (e.g., batching draw calls, CPU-side work). They call this "intra-rendering headroom."
    *   **Observation 2:** Predicting headroom for *each individual frame* is noisy and hard. But predicting the *total* headroom across the entire LLM execution window (e.g., 36 frames for 100 APM) is much more stable and can be done with a simple Linear Regression (LR) model with <1.3% error (Table II).
    *   **The Scheduler's Job:** At the start of each LLM action window, the LR model predicts total headroom. Based on this, the scheduler picks the right layer-skipping strategy (e.g., "skip 4 layers" vs. "skip 8 layers"). Then, it breaks the LLM inference into fine-grained subtasks (single transformer layers). It monitors the GPU: when a rendering subtask finishes, it dispatches a small LLM subtask to fill the intra-rendering gap. When a whole rendering frame finishes, it dispatches larger LLM subtasks for the inter-rendering gap.

**In short:** LEGO makes the LLM *flexibly smaller* based on resource predictions, and then *surgically interleaves* its execution into every GPU idle moment the game leaves behind.

---

## Q2: The Key Insight

The paper's key insight is a clever *decoupling* of the layer-skipping decision from the token being generated.

Prior layer-skipping methods like LITE [58] and CALM [52] make a *per-token* decision: "Is this token confident enough to exit early?" This is fundamentally incompatible with strict latency SLOs because the total inference time becomes stochastic. As Figure 5 shows, even when LITE's average time hits the target, 47.1% of individual inferences violate the deadline.

**LEGO's insight is to flip this:** Instead of asking "what does the token need?", they ask "what do the *resources* allow?". The decision of *how many layers to skip* is made *once*, at the start of the entire inference request, based on a prediction of available GPU headroom.

This works because of a secondary, supporting insight about LLM architecture (Section IV-B, Figure 8): **Knowledge contribution is not uniform across layers.** The later layers of Llama and Mistral have highly correlated input/output representations, meaning they contribute less unique information. LEGO exploits this by always targeting these less-impactful layers for skipping, and training a dedicated adaptor to recover what little knowledge is lost.

The combination is powerful: the *system* makes a deterministic, resource-driven choice, and the *algorithm* (the adaptor) ensures that choice doesn't catastrophically hurt accuracy. This is the "algorithm-system co-design" the authors tout in the abstract, and it's a genuine contribution.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1.  **Real-World, End-to-End System Evaluation:** This is not a simulation. They run actual AAA games (*Black Myth: Wukong*, *Final Fantasy XVI*, *Red Dead Redemption 2*) at 4K/60fps on a real RTX 4090 (Section VII-A, Table III). They integrate their system into Unreal Engine 4 and `llama.cpp`. This is a high bar for systems papers.

2.  **Comprehensive Metrics:** They report the metrics that matter: 99th-percentile FPS (Figure 12a) and 99th-percentile APM (Figure 12b), not just averages. This correctly captures tail latency, which is critical for interactive applications.

3.  **Strong Baselines and Fair Comparison:** They compare against smaller models (Llama3-3B, Mistral-4B) and the best prior layer-skipping method (LITE [58]). Crucially, they *augment all baselines* with PilotFish [66], a state-of-the-art time-slicing mechanism, to give them the fairest possible chance (Section VII-A). This prevents LEGO from looking good simply because it has *any* scheduler.

4.  **The "Street Fighter" Experiment is Brilliant (Section VII-D, Figure 13):** Instead of just reporting accuracy on benchmarks like MMLU, they have the LLMs *actually play a game against each other*. The win-rate heatmap (Figure 13) is a far more compelling demonstration of real-world impact than an F1 score. It shows LEGO-4 beating Llama3-3B, even though Llama3-3B is a fully-trained, dedicated small model. This is the kind of evaluation that sells a paper.

5.  **Ablation of Headroom Utilization (Section VII-G, Figure 15):** They show LEGO uses up to 28.6% more GPU headroom than baselines. This directly proves the value of their "intra-rendering headroom" insight.

**Weaknesses:**

1.  **The 300 APM Scenario is Marginal:** The paper's own data shows this is a stress test the system barely survives. Table IV shows that at the layer-skipping level needed for 300 APM (~13 layers skipped), accuracy on some benchmarks drops *below* that of Llama3-3B. The Street Fighter experiment (Section VII-D) explicitly states they *don't show 300 APM results* because they are "similar." This suggests the 300 APM mode trades away too much model quality to be practically useful. The paper doesn't discuss this trade-off honestly.

2.  **Adaptor Training Overhead is Hand-Waved:** Section IV-C-2 states training "up to 14 LLM adaptors" for BlackMyth takes "approximately 36 hours." This is framed as "negligible" because it's offline. But this means every new (Game, LLM) pair requires a new, multi-day training run. The generalization story is weak; this is a bespoke solution for each deployment.

3.  **Memory Overhead is Understated (Section VII-K):** Each adaptor is 268.8 MB, totaling 3.23 GB for 12 adaptors. This is a significant addition to the ~16GB footprint of Llama3-8B in FP16. On a 24GB RTX 4090, this leaves very little room for the game's own VRAM needs (textures, framebuffers). The paper never discusses peak VRAM usage or whether memory becomes a bottleneck before compute does.

4.  **Limited Scope on Prompt/Output Length:** The paper fixes input length at 512 tokens and output at 16 tokens (Section II-B). The variable-length experiment (Section VII-F) only goes up to 1024 input tokens. For many LLM gaming applications (e.g., dialogue with long history), much longer contexts are needed. The prefill phase scales quadratically with input length, and the paper doesn't explore if the system holds up at, say, 4K or 8K tokens.

5.  **MoE Results are Weak (Section VII-H):** Table V shows that for Mixtral-8x7B, skipping 12 layers causes ARC-C accuracy to collapse from 61.7% to 14.9%. The authors acknowledge "removing entire transformer layers disrupts expert routing." This is a significant limitation, as MoE models are the dominant architecture for frontier models (GPT-4, Mixtral, DeepSeek). LEGO is fundamentally a technique for *dense* models.

---

## Q4: What the Authors Didn't Tell You

1.  **The "Intra-Rendering Headroom" Relies on Benevolent Game Engines:** The core scheduling insight (Section V-A, Figure 10b) is that GPU idle time exists *within* rendering frames. This is an artifact of how current game engines (like Unreal Engine) batch and pipeline work. A more aggressively optimized game engine, or one using asynchronous compute more heavily, could have far less intra-frame headroom. The generality of this finding to *all* games is an open question the paper doesn't address.

2.  **Why Not Just Use Speculative Decoding?** The paper never mentions speculative decoding. A standard approach to accelerate LLM inference is to use a small "draft" model (like Llama3-3B) to generate candidate tokens, then verify them in parallel with the large model (Llama3-8B). This could achieve similar latency reductions without custom adaptors. The comparison to speculative decoding on a shared, resource-constrained GPU is a glaring omission.

3.  **The Baseline Implementation is Suspicious:** In Section II-D-2, they state that LITE with SLO constraints ("LITE-S") causes a "27.2% drop in accuracy." But LITE-S is their *own invention* for this paper, not a published method. The actual LITE paper [58] is designed for datacenter throughput, not interactive latency. Comparing against a hastily-adapted version of LITE designed to fail is a weak strawman.

4.  **The Adaptor is Not "Distillation" in the Standard Sense:** The paper uses the term "knowledge distillation" (Section III, IV). True knowledge distillation uses a teacher model's soft output probabilities to train a student. LEGO's adaptor is trained with a Mean Squared Error (MSE) loss on *hidden state activations* (Equation in Section IV-C-2). This is feature matching, not distillation. It's a minor point, but the framing is misleading.

5.  **The Practical Deployment Model is a Bottleneck:** The paper states "LEGO is designed for commercial game companies, rather than end users" (Section III). The workflow is: the game company fine-tunes an LLM, builds the similarity heatmap, trains 14+ adaptors, and ships the ~20GB+ package (model + all adaptors) with the game. This is a massive distribution and update burden. If the base LLM model ever needs updating (for safety, capabilities, etc.), the entire adaptor training pipeline must be re-run. The paper ignores the practical DevOps nightmare this creates.