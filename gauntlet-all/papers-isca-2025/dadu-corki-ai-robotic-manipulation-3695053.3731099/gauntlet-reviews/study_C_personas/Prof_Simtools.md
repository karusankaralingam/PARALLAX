Q1: Whiteboard Explanation

Imagine you're building an embodied AI robot that manipulates objects—a robot arm that picks up a mug when you say "grab the blue mug." The current approach works frame-by-frame: capture image → run LLM inference → predict single action → execute → repeat. This creates a latency bottleneck because every single frame requires a full LLM inference round-trip to a cloud server.

**Corki's key innovation** is decoupling the LLM inference frequency from the robot control frequency by predicting *trajectories* instead of discrete actions.

Here's how it works:

1. **Algorithm Change**: Instead of predicting "move Δx, Δy, Δz for the next frame," the LLM predicts a cubic polynomial trajectory (e.g., `r_x(t) = at³ + bt² + ct + d`) covering multiple future timesteps (Section 3.2, Equation 4). This reduces LLM calls by up to 5.1×.

2. **Hardware Accelerator**: A custom FPGA accelerator converts these trajectories into high-frequency torque signals using Task Space Computed Torque Control (TS-CTC). The accelerator exploits data reuse across kinematics calculations and employs approximate computing—reusing Jacobian/mass matrices when joints haven't moved significantly (Section 4.2-4.3, Figure 8).

3. **Pipeline Parallelism**: While the robot executes a trajectory, the system simultaneously sends new camera frames back to the server, hiding communication latency (Section 4.4, Figure 1b).

The result: You predict once, execute multiple steps, and pipeline everything—achieving 5.9× speedup with 13.9% higher success rates (Abstract).

Q2: The Key Insight

The key insight is that **existing embodied AI systems are "vision-centric" when they should be "robotic-centric."**

Current systems treat robot control like video processing: frame-by-frame, forcing the expensive LLM to run at control frequency (~30 Hz). But robotics has long known that *planning* and *control* operate at fundamentally different frequencies. The front-end (perception/planning) doesn't need real-time performance; the back-end (motor control) does. Trajectory is the natural intermediate representation that bridges this gap (Section 1, page 328-329).

**Why this matters**: The LLM's job is reasoning about *what* to do, not generating 30 micro-commands per second. By predicting a smooth trajectory function, you let the LLM operate at its natural frequency while a lightweight controller interpolates at 100+ Hz.

**The overlooked assumption they challenge**: Algorithm designers defaulted to frame-by-frame execution because it mirrors video processing conventions—not because it's optimal for robotics. The authors explicitly state: "Today's embodied AI pipeline is designed purely based on the convenience of algorithm designers" (Section 1, page 328).

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Real Hardware Validation**: They implement the control accelerator on actual FPGA hardware (Xilinx Zynq-7000, Section 5.1) and measure real communication latency with a physical Franka Emika Panda robot arm—not just simulation numbers.

2. **Comprehensive Metrics**: They report both success rate AND trajectory error (Figure 11), acknowledging these don't always correlate (Section 6.2: "a lower trajectory error does not always correlate with higher accuracy").

3. **Ablation Coverage**: Tables 1-2 show multiple Corki variants (Corki-1 through Corki-9, Corki-ADAP), demonstrating the accuracy-latency tradeoff systematically.

4. **Artifact Availability**: Full code on GitHub with detailed reproduction instructions (Appendix A), including training scripts and CALVIN benchmark integration.

**Weaknesses:**

1. **Simulation-Dominant Accuracy Evaluation**: While hardware timing is measured on real systems, the *accuracy results* (Tables 1-2) come entirely from the CALVIN simulator, not real-world robot experiments. The paper acknowledges this implicitly by only discussing "simulation environments" for task completion (Section 5.1).

2. **Control Accelerator Validation Gap**: The FPGA implements TS-CTC, but they don't validate the accelerator's torque outputs against ground truth or RTL simulation. They claim 29× speedup over CPU (Section 6.3) but don't show whether the approximate computing introduces control errors that compound over time.

3. **Limited Baseline Hardware Comparison**: The CPU baseline is an Intel i7-6770HQ (the robot's onboard processor from ~2015). While they mention trying an i7-13700 (Section 2.2), detailed comparisons against modern embedded GPUs or existing robotic control accelerators are absent.

4. **Single Robot Morphology**: All results use a 7-DoF Franka arm. Section 8 acknowledges "our method is limited to robotic arms, which typically have 9 DoF or fewer"—a significant scope limitation.

Q4: What the Authors Didn't Tell You

**The Simulation Setup Masks Real-World Complexity:**
- The CALVIN benchmark (Section 5.1) is a *simulated* environment with perfect state estimation. Real robots have sensor noise, calibration errors, and communication jitter that could invalidate trajectory predictions.
- They measure communication latency using "a Wi-Fi module" (Section 5.1) but don't specify network conditions, packet loss handling, or worst-case latency guarantees critical for real-time systems.

**Approximate Computing Risks Are Underexplored:**
- The 40% approximation threshold (Section 6.4, Figure 15) was chosen empirically to "balance speedup and accuracy." But Figure 15b shows trajectory error *does* increase with higher thresholds. For safety-critical manipulation (e.g., near humans), even small cumulative errors could be dangerous.
- The claim that "over 51% of matrix updates can be avoided without any loss in control accuracy" (Section 4.3) uses trajectory error as the metric—but trajectory error ≠ task success, as they themselves note.

**Long-Tail Latency Concerns:**
- Figure 14c reveals Corki has 56% *higher* relative latency variation than the baseline. For real-time systems, worst-case latency often matters more than average. They acknowledge this ("severer long tail problem," Section 6.3) but don't analyze implications for hard real-time constraints.

**What Happens When Trajectories Go Wrong?**
- The paper mentions closed-loop features (Section 3.4) using random intermediate images, but doesn't evaluate how quickly the system can recover from prediction errors or unexpected obstacles.