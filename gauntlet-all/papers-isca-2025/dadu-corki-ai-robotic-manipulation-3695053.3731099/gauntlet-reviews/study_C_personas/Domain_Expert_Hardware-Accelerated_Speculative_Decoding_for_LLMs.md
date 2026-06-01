# Paper Deconstruction: Dadu-Corki

Let me be clear from the start: **this paper is not about speculative decoding for LLM text generation**. You may have expected a paper on hardware-accelerated speculative decoding given the persona framing, but this is actually a paper about **embodied AI for robotic manipulation**—specifically, accelerating the pipeline where an LLM (a Vision-Language Model) controls a robot arm.

That said, let me decode this paper properly, because it has interesting ideas worth understanding.

---

## Q1: Whiteboard Explanation

**The Problem (Napkin Sketch Version):**

Imagine you're remotely controlling a robot arm to pick up a coffee mug. The current pipeline works like this:

1. Camera captures a frame → sends it to the cloud server
2. VLM (Vision-Language Model) looks at the frame and says "move gripper 2cm left, 1cm down"
3. Robot executes that tiny movement
4. Camera captures the *next* frame → back to step 1

This happens **every single frame** at 30 Hz. The problem? Each cycle takes ~250ms (see Figure 2a, page 5): 72.7% is LLM inference, 17.4% is communication, 9.9% is robot control. You're trying to operate at 30Hz but your pipeline takes 4x longer than your frame budget. The robot moves like it's lagging on a bad internet connection.

**Corki's Solution:**

Instead of predicting "move 2cm left" for the next frame, predict **a whole trajectory** for the next 5+ frames—a smooth cubic polynomial curve describing where the gripper should be over the next ~165ms.

Think of it like this: Instead of giving turn-by-turn directions every 10 feet ("turn slightly left... now slightly right..."), you hand the robot a curved path on a map and say "follow this for the next half-second."

**The Three-Part Fix:**

1. **Algorithm Change (Section 3):** Train the VLM to output cubic polynomial coefficients (a, b, c, d for x(t) = at³ + bt² + ct + d) instead of discrete per-frame actions. This reduces how often you need to call the expensive LLM—up to 5.1× fewer inferences.

2. **Hardware Accelerator (Section 4):** The robot still needs to convert the trajectory into actual motor torques at high frequency (100Hz) for smooth motion. They build an FPGA accelerator for "Task Space Computed Torque Control" (TS-CTC)—the math that translates "gripper should be at position X" into "apply Y Newton-meters to motor 3." The trick is that the expensive matrices (Jacobian, Mass matrix) don't change much between control cycles, so they reuse computations when joint movements are small (Section 4.3, Figure 9).

3. **Pipeline Overlap (Section 4.4):** While the robot is executing a 165ms trajectory, you can simultaneously send the next batch of camera frames back to the server. Communication latency is hidden under execution latency.

**Net Result:** Frame latency drops from 250ms to ~42ms for Corki-ADAP (5.9× speedup per Figure 13), and paradoxically, accuracy *improves* (Table 1: average job length 3.2 vs 2.916 baseline).

---

## Q2: The Key Insight

**The Real Contribution (The Delta):**

The genuine insight is **recognizing the frequency mismatch between planning and control in robotics, then exploiting it**.

Robotics has known forever that your high-level planner doesn't need to run at 1000Hz—only your low-level controller does. The embodied AI community imported video-processing conventions (frame-by-frame sequential processing) that are fundamentally inappropriate for robot control. The paper explicitly calls this out on page 3:

> "Today's embodied AI pipeline is designed purely based on the convenience of algorithm designers, as executing frame by frame sequentially is a traditional method in video processing algorithms. Yet, it does not follow the design methodology in robotic domain."

**The Mechanism:**

The core mechanism is actually quite simple: **predict a continuous trajectory (cubic polynomial) instead of discrete per-frame actions, then use classical control theory to track that trajectory at high frequency**.

The *algorithm* contribution is the trajectory prediction training scheme with masked embeddings (Figure 4) and the adaptive waypoint-based early termination (Algorithm 1, Section 3.3). The idea of terminating early when curvature is high (robot is about to make a sharp turn) or gripper state changes is clever—it lets you balance responsiveness against inference frequency dynamically.

The *hardware* contribution is the dataflow accelerator for TS-CTC (Figure 8) with the approximate computing scheme that reuses Jacobian/Mass matrices when joint movements are small. Figure 9 quantitatively justifies this: when joints 1 or 7 move (end joints), the mass matrix barely changes; when joint 2 moves (middle joint), it changes significantly. This is physics-informed approximation.

**What's NOT new:**

- Trajectory prediction for robots (classical robotics)
- Task-space computed torque control (textbook material, cite [54])
- Accelerators for robotic dynamics (their own prior work: Dadu-RBD [74])

The novelty is the **co-design**: changing the LLM output format to enable decoupling, which in turn enables the accelerator and pipeline optimizations.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **Real Hardware Implementation:** They actually implemented the accelerator on a Xilinx Zynq-7000 FPGA (page 11) and measured real communication latency with a Franka Panda arm over WiFi. This is not a simulation-only paper. Section 6.1 reports actual resource utilization: 13.6% DSP, 7.8% FF, 16.9% LUT, 6.6% BRAM.

2. **Apples-to-Apples Comparison:** They retrained RoboFlamingo themselves (Tables 1-2 footnote: "Baseline is retrained") rather than copying numbers from another paper. Their baseline numbers match or exceed reported results.

3. **Multiple Metrics:** They report success rate, average job length, mean trajectory error, AND maximum trajectory distance (Figure 11). The trajectory comparisons in Figure 12 are actually informative—you can see where RoboFlamingo diverges from ground truth.

4. **Ablation Across Trajectory Lengths:** Tables 1-2 show results for Corki-1 through Corki-9, letting you see the accuracy-latency tradeoff curve. Corki-5 is the sweet spot; Corki-9 degrades in unseen scenarios.

5. **Hardware Ablation:** Section 4.2 reports that data reuse gives 54% latency reduction, pipelining gives 69.6% additional reduction, for 86% total reduction vs. naive implementation.

### Weaknesses:

1. **Simulation-Only Accuracy Evaluation:** While they use real hardware for latency/energy measurements, all accuracy numbers (Tables 1-2) come from the CALVIN simulation benchmark. They never demonstrate the full system working on a physical robot doing actual manipulation tasks. The paper essentially claims "the hardware works" and "the algorithm works in simulation"—but never "the system works end-to-end on a real robot." This is a significant gap for an embodied AI paper.

2. **Single Baseline Architecture:** They compare only against RoboFlamingo. What about OpenVLA [33], Octo [59], or ACT/Diffusion Policy approaches? The embodied AI space has many methods; showing only one baseline weakens the generality claims.

3. **Cherry-Picked Task Domain:** CALVIN is specifically manipulation tasks in a constrained tabletop environment. The Discussion section (page 14) admits: "our method is limited to robotic arms, which typically have 9 DoF or fewer" and "our method can currently handle relatively long trajectories, given that sudden changes in the movement of a robotic arm are rare." They're explicitly avoiding the hard cases.

4. **Long-Tail Latency Problem:** Figure 14c shows Corki has *worse* latency variance than the baseline (56% higher relative variation). Some frames take ~400ms while others take ~50ms. For real-time control, worst-case latency matters as much as average.

5. **No Comparison to Simpler Solutions:** What if you just ran the VLM less frequently with action chunking (predict K actions, execute all K, repeat)? This is common practice (ACT, Diffusion Policy). The paper never explains why trajectory polynomials are better than simply outputting K discrete actions. The cubic polynomial is arguably more constrained (can't represent arbitrary motion).

6. **Energy Claims Need Context:** Figure 13 shows 9.2× energy reduction, but Section 8 reveals: "the computing system inside the robot accounts for 40.6% of the total system power consumption (excluding server power)." So the *actual* system-level energy savings are much smaller.

---

## Q4: What the Authors Didn't Tell You

1. **The "speedup" is mostly from running inference less often, not from the accelerator.**

   Look at the numbers: Corki-5 runs inference every 5 frames instead of every 1 frame. That's a 5× reduction in LLM inference. The reported speedup is 5.9× (Figure 13). The accelerator contributes the remaining ~0.9× by accelerating control and enabling pipeline overlap. The vast majority of the win comes from the algorithmic change to trajectory prediction. The accelerator is nice, but not the main event.

2. **The accuracy improvement is surprising and under-explained.**

   Table 1 shows Corki-5 achieves 45.8% success on 5-task sequences vs. 31.2% for baseline—a 14.6 percentage point improvement. This is *huge*. The paper waves hands about "trajectory naturally provides a more robotic-friendly supervision during algorithm training" (page 12). But wait—if predicting trajectories is just *better*, why wasn't everyone already doing this? The paper doesn't provide a convincing mechanistic explanation. I suspect the real reason is that the baseline has error accumulation from sequential frame-by-frame prediction, while trajectory prediction is more stable because it's predicting further ahead with more context.

3. **The "close-loop feature" (Section 3.4) seems tacked on.**

   They admit the basic algorithm is open-loop during trajectory execution. Section 3.4 adds a ViT encoder to process images "randomly" sent during execution. But this is barely evaluated—there's no ablation showing how much the close-loop feature helps. It feels like a response to an obvious reviewer concern rather than a core contribution.

4. **The approximation threshold choice is poorly justified.**

   Figure 15 shows they picked 40% threshold, but the trajectory error at 40% is ~0.54cm while at 0% it's ~0.51cm—a 6% degradation for a 1.3× speedup. Why is this the right tradeoff? They don't discuss failure cases or safety margins.

5. **Real-time guarantees are missing.**

   The paper repeatedly invokes "real-time constraints" (page 3: "Without real-time assurances, the applicability of embodied AI systems is severely limited"). But they never define what "real-time" means here or prove they meet it. Figure 14c shows worst-case latency of ~400ms, which seems incompatible with any reasonable real-time definition for robot control.

6. **The hardware accelerator's approximate computing is fragile.**

   Figure 9 shows the sensitivity analysis was done with the robot in "home configuration." But during actual manipulation, the joint configurations change continuously. The paper assumes the impact factors are constant, but they likely vary with configuration. A manipulation task that requires lots of middle-joint movement (joint 2-4) will get less benefit from approximation than a task using mostly end joints.

7. **They buried the scope limitation.**

   Page 14: "our method is limited to robotic arms" and "in tasks where the robot moves quickly with abrupt changes, the trajectory prediction must adapt accordingly." Translation: this doesn't work for fast, dynamic tasks or high-DoF robots like humanoids. That's a significant limitation for a paper claiming to advance "embodied AI."