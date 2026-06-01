## Q1: Whiteboard Explanation

Imagine you're controlling a robot arm to pick up a mug. The current approach works like a movie—frame by frame. For every single frame (~33ms), the system:
1. Sends an image from the robot's camera to a cloud server
2. Runs a large language model (LLM) to predict "move 1cm left, 0.5cm up"
3. Sends that command back to the robot
4. Robot executes that tiny movement
5. Repeat for the next frame

**The Problem:** Each cycle takes ~250ms (Figure 2a shows: 72.7% LLM inference, 17.4% communication, 9.9% control). This is far too slow for real-time robotics (need ≥30Hz, preferably 100Hz).

**Corki's Solution:** Instead of predicting one tiny step, predict an entire *trajectory* for the next ~165ms (5 frames). Think of it like giving directions: instead of saying "turn your wheel 1 degree... now 1 more degree... now 1 more," you say "follow this curve for the next 500 meters."

The trajectory is modeled as a cubic polynomial: `r(t) = at³ + bt² + ct + d` (Equation 4). A dedicated hardware accelerator on the robot then converts this smooth curve into high-frequency motor torques (100Hz) using computed torque control.

**Key Architectural Trick:** The paper observes that joint angles change slowly between control cycles. Joints 1 and 7 (end joints) barely affect the robot's mass matrix even with 29-degree movements (Figure 9). So they skip recomputing dynamics parameters when changes are small—an application-specific approximation that saves ~51% of matrix updates.

---

## Q2: The Key Insight

**The Core Insight:** The existing embodied AI pipeline is "vision-centric"—designed by ML researchers who think in frames—but robotics is "trajectory-centric." In classical robotics, the planning module (slow, ~10Hz) and control module (fast, ~100Hz) have always been decoupled through trajectory representations. Corki simply applies this decades-old robotics principle to LLM-based systems.

**Why This Matters:**
Section 2.2 states: *"From the perspective of a robotic system designer, the planning module does not need to match the high frequency of the control module. Trajectory is usually used as a bridge to eliminate the frequency mismatch."*

This is genuinely insightful because it reframes the problem: the bottleneck isn't that LLMs are slow (they are), but that the system architecture *forces* LLM inference at camera framerate when it doesn't need to be. By predicting trajectories instead of single actions, you reduce LLM calls by 5× (from every frame to every 5 frames) while actually *improving* control quality through smoother, higher-frequency execution.

**The Hidden Gem:** The approximation strategy for dynamics computation (Section 4.3) is clever. They quantitatively show (Figure 9) that different joints have vastly different impacts on the mass matrix—end joints barely matter, middle joints matter a lot. This joint-specific sensitivity analysis enables intelligent computation skipping that wouldn't be possible with generic approximate computing.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Comprehensive Latency/Energy Breakdown (Figure 2):** The 72.7%/17.4%/9.9% split for inference/communication/control provides clear motivation. The energy breakdown (95.8% inference) is especially compelling for battery-powered robots.

2. **End-to-End Task Success Metrics (Tables 1 & 2):** They report actual task completion rates on the CALVIN benchmark across 5-task sequences, not just proxy metrics. The "Average Job Length" metric (max 5) captures long-horizon capability. Corki-5 achieves 3.421 vs. baseline 2.916 on seen tasks—a meaningful 17.3% improvement.

3. **Multiple Trajectory Length Variants (Corki-1 through Corki-9):** This sweep reveals the accuracy-latency tradeoff. The inverted-U curve (accuracy peaks at Corki-5, degrades at Corki-9) is informative and helps readers understand when trajectory prediction fails.

4. **Trajectory Error Analysis (Figure 11):** Providing both mean trajectory error and maximum trajectory distance adds nuance—showing that lower mean error doesn't always correlate with higher success rate (Section 6.2 acknowledges this limitation honestly).

5. **Hardware Resource Utilization (Section 6.1):** Only 13.6% DSP, 7.8% FF, 16.9% LUT, 6.6% BRAM on ZC706 FPGA—demonstrating practical deployability.

### Weaknesses

1. **The "Cherry-Pick" Check — Single Benchmark:** The entire evaluation uses **only the CALVIN dataset** with a **single robot (Franka Emika Panda)**. Section 8 explicitly acknowledges: *"our method is limited to robotic arms, which typically have 9 DoF or fewer"* and *"given that sudden changes in the movement of a robotic arm are rare."* This is a slow-moving, confined-space manipulation task—the best-case scenario for trajectory prediction. What about:
   - Mobile manipulation with dynamic obstacles?
   - Bimanual tasks requiring coordination?
   - Contact-rich tasks (insertion, wiping)?

2. **Baseline Validity Concerns:** The baseline is RoboFlamingo running on **V100 GPU** (2017-era hardware). Table 3 shows H100 reduces inference latency by 0.4×, but they still claim 6.4× speedup. The implicit comparison is against a specific, somewhat dated hardware configuration. Running on Jetson Orin (10× slower inference) would make any optimization look good.

3. **The Control Frequency "Zero-Event" Problem:** Section 2.2 claims baseline control at 22.1 Hz is insufficient (needs "at least 30 Hz, 100 Hz preferable"). But **where is the evidence that 22.1 Hz actually causes task failures in this benchmark?** The CALVIN tasks involve picking up blocks and opening drawers—not precision surgery. The claimed 100Hz requirement comes from robotics literature on torque control (citations [11, 74]), but those papers address different scenarios (humanoid balance, high-speed manipulation).

4. **Simulation-Only Evaluation:** All results are from the CALVIN *simulator*. Section 5.1 describes hardware measurements on real robot/FPGA, but the accuracy numbers (Tables 1-2) come from simulation. Real robots have sensor noise, actuator delays, and sim-to-real gaps that could break the trajectory prediction assumption.

5. **Long-Tail Latency Acknowledged but Not Addressed:** Figure 14c shows Corki has 56% *worse* latency variation than the baseline. The authors admit *"our method achieves lower average frame latency, [but] it does exhibit severer long tail problem."* For safety-critical robotics, worst-case latency often matters more than average.

6. **Approximation Threshold Sensitivity:** Figure 15 shows trajectory error increases from 0.50cm to 0.58cm as threshold increases from 0% to 80%. They choose 40% without justification beyond "balance speedup and accuracy." Why 40%? Is 0.58cm error acceptable for all tasks?

---

## Q4: What the Authors Didn't Tell You

1. **The Closed-Loop Feature is Underspecified:** Section 3.4 describes sending "random" images during trajectory execution for closed-loop feedback. But how does this actually affect trajectory correction? The ViT-encoded features are "concatenated" with LLM tokens—but is there any evidence this actually helps? They never ablate the closed-loop feature's contribution to the success rate numbers.

2. **Training Cost is Hidden:** Section A.2 (Artifact Appendix) reveals training requires **8× A100 (80GB) GPUs** and takes approximately **10 days** including training and evaluation. The paper never discusses this cost in the main text, yet claims the approach is practical for robotics deployment.

3. **The Cubic Polynomial Assumption May Be Limiting:** Equation 4 uses a cubic function for trajectory fitting. The justification (Section 3.2) is that cubics capture velocity/acceleration smoothly. But cubic polynomials cannot represent trajectories with inflection points or rapid direction changes—exactly the scenarios where trajectory prediction would fail. The adaptive termination (Section 3.3) is a patch for this limitation, not a solution.

4. **Communication Latency "Hiding" Requires Generous Assumptions:** Figure 1b shows communication latency hidden during robot execution. But this only works if trajectory execution time > communication time. With WiFi latency of ~43ms (Figure 2a) and Corki-3 executing for only 99ms, the margin is thin. Network congestion or longer trajectories would break the pipeline.

5. **The Approximation Analysis Uses Extreme Joint Angles:** Figure 9 shows mass matrix changes for 6°, 17°, and 29° rotations. But 29° is a large movement—typical control cycles involve sub-degree changes. The claim that "over 51% of matrix updates can be avoided" (Section 4.3) may not generalize to different motion profiles.

6. **Safety Discussion is Superficial:** Section 8's safety discussion claims collaborative robot arms are "initially introduced as a safer alternative" and that higher control frequency "reduces safety concerns." This sidesteps the real issue: predicting longer trajectories means the robot commits to actions without environmental feedback. A human entering the workspace mid-trajectory would not be detected until the next LLM inference—potentially 165ms later.

7. **Generalization to Other Models is Unproven:** The paper builds on RoboFlamingo (3B parameters), but embodied AI is rapidly moving to larger models (OpenVLA [33], RT-2 [6]). The trajectory prediction approach may not transfer—larger models might have different inference characteristics, and the policy head modification (Figure 4) is architecture-specific.