# Paper Deconstruction: Dadu-Corki

## Q1: Whiteboard Explanation

Imagine you're watching a robot arm try to pick up a mug. The current approach works like this:

**The Old Way (Frame-by-Frame):**
1. Camera captures image → sends to cloud server
2. Big neural network (LLM/VLM) looks at image → predicts "move 2mm left, 1mm down"
3. Robot executes that tiny movement
4. Camera captures new image → sends to cloud
5. Repeat... for every single frame at 30Hz

The problem? Each cycle takes ~250ms (Figure 2a shows: 72.7% LLM inference, 17.4% communication, 9.9% control). You're running the expensive LLM *every single frame*, and everything happens sequentially.

**The Corki Way (Trajectory Prediction):**
Instead of asking the LLM "what's my next tiny step?", ask it "what's my path for the next 5 steps?" The LLM outputs *cubic polynomial coefficients* (a, b, c, d for each axis) that describe a smooth trajectory: `x(t) = at³ + bt² + ct + d` (Equation 4).

Now the pipeline becomes:
1. LLM predicts trajectory for next ~165ms (5 frames worth)
2. A small FPGA accelerator converts that trajectory into actual motor torques at high frequency (100Hz)
3. While robot is executing, camera can send new images back to server *in parallel*
4. When trajectory ends, LLM predicts next trajectory

**The Hardware Piece:**
The trajectory still needs to become actual motor commands. This is called "Task Space Computed Torque Control" (TS-CTC) - it's classical robotics math involving Jacobians, mass matrices, etc. The paper builds a custom FPGA accelerator (Figure 8) that:
- Pipelines the computation of pose→velocity→acceleration→force across different robot links
- Exploits the fact that when joints barely move, you can *reuse* expensive matrix computations instead of recomputing them (their "approximate computing" insight from Figure 9)

**Net Result:** Instead of running the LLM 30 times per second, you run it ~6 times per second, while maintaining smooth 100Hz control through the cheap hardware accelerator.

---

## Q2: The Key Insight

**The Delta (Real Contribution):**
This is fundamentally a *systems/pipeline* paper, not a new neural architecture paper. The core insight is recognizing that embodied AI pipelines have been designed by vision/ML researchers who think in "frames," but robotics engineers think in "trajectories." By changing the output representation from discrete per-frame actions to continuous polynomial trajectories, you can:

1. **Amortize LLM cost** across multiple control steps (5.1× reduction in inference frequency - Section 6.3)
2. **Decouple planning frequency from control frequency** - the LLM can run at 6Hz while the controller runs at 100Hz
3. **Pipeline communication with execution** - send new images while still executing the trajectory

**The Magic Trick (The Mechanism):**
The trajectory is represented as 6 cubic polynomials (one per DOF, excluding gripper), trained end-to-end with MSE loss against ground-truth trajectories (Equation 5). The clever part is they *mask intermediate vision-language tokens* during training (Figure 4) to simulate the reduced visual feedback the robot will actually experience during trajectory execution.

The hardware accelerator exploits a domain-specific property: for high-frequency control, joint positions change very little between cycles, but the mass/Jacobian matrices depend on joint positions. Figure 9 shows that movements in joints 1 and 7 (the end joints) barely change the mass matrix, while joint 2 (middle) changes it significantly. Their ACE (Approximate Computing Enable) unit dynamically decides whether to recompute or reuse matrices based on which joints moved.

**Why Cubic Polynomials?**
Section 3.2 explains: cubic functions have continuous first and second derivatives (velocity and acceleration), which is what you need for smooth physical motion. It's the minimum-degree polynomial that can do this while being constrained at both endpoints.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

1. **Real Hardware Implementation:** They actually built the FPGA accelerator on a Xilinx Zynq-7000 (Section 5.1) and measured real communication latency over WiFi to a Franka Panda arm. This is not a simulation-only paper.

2. **Appropriate Baseline Choice:** RoboFlamingo is a legitimate, recent (2024) embodied AI system achieving ~89.5% success rate. They retrained it themselves (Tables 1-2 footnote) and report numbers matching or exceeding the original paper.

3. **The CALVIN Benchmark is Standard:** 34 tasks, 22994 demonstrations, both "seen" and "unseen" scenarios. This is the community-accepted benchmark for this domain.

4. **Honest Accuracy Reporting:** They show that Corki-9 (taking 9 steps) actually *underperforms* baseline on unseen tasks (Table 2: 79.4% vs 82.4% on Task 1). Not all configurations win.

5. **Multiple Metrics:** Success rate, average job length, trajectory RMSE, *and* maximum trajectory distance (Figure 11). The last one is important - a robot can have low average error but one catastrophic deviation.

6. **Hardware Resource Numbers:** Section 6.1 reports actual FPGA utilization (13.6% DSP, 6.6% BRAM). The accelerator is genuinely small.

### Weaknesses

1. **The GPU Baseline is a V100:** The V100 is from 2017. Table 3 shows they also tested H100, but only report "normalized inference latency" (0.4×) and speedup (6.4×). They don't show absolute numbers. On an H100, the LLM inference might already be fast enough that communication becomes the dominant bottleneck, changing the story.

2. **Single Robot, Single Task Domain:** Everything is on a 7-DOF Franka Panda arm doing tabletop manipulation. Section 8 explicitly admits: "our method is limited to robotic arms, which typically have 9 DoF or fewer." Humanoids, mobile robots, or anything with faster dynamics is out of scope.

3. **The "Close-Loop Feature" is Underspecified:** Section 3.4 mentions randomly sending images back mid-trajectory and encoding them with ViT, but there's no ablation showing its contribution. How much does it actually help? Is Corki-5's success rate coming from trajectory prediction or from the close-loop correction?

4. **Long-Tail Latency Problem (Figure 14c):** They honestly report that Corki has 56% *worse* relative latency variation than baseline. Some frames hit the "predict trajectory" latency spike while others just execute. For real-time systems, worst-case latency often matters more than average.

5. **The Approximate Computing Contribution is Unclear:** Section 6.4 shows trajectory error increases with approximation threshold (Figure 15b), but they don't isolate its contribution to the overall speedup. How much comes from reduced LLM calls vs. the approximate control computation?

6. **Control Frequency Comparison Missing:** They claim 100Hz is needed for smooth control (Section 4.1), but Corki-5 achieves only 26.9 Hz (Section 6.3). The baseline runs at 30Hz. Where's the claimed 100Hz?

---

## Q4: What the Authors Didn't Tell You

1. **The 5.9× Speedup is for Average Frame Latency, Not Task Completion Time:** Figure 13 shows *per-frame* latency reduction. But the robot still has to complete the same physical motion. The *task completion time* savings depend heavily on how much the reduced-feedback trajectory prediction overshoots or undershoots, requiring correction.

2. **Model Size is Fixed at 3B Parameters:** RoboFlamingo uses OpenFlamingo-3B. The entire embodied AI field is moving toward much larger models (RT-2 uses PaLM-E at 562B). Their pipeline's benefit scales with LLM inference cost, but they don't show what happens with a 7B, 13B, or larger model where the tradeoff would be even more favorable.

3. **The "Success Rate Improvement" Framing is Misleading:** The abstract claims "up to 13.9% success rate improvement," but Table 1 shows the baseline at 89.5% and Corki-5 at 92.3% for Task 1 - that's only 2.8 percentage points. The 13.9% comes from the *relative* improvement on completing 5 consecutive tasks (31.2% → 45.8%), which is a much harder metric that compounds errors.

4. **No Comparison to Simple Baselines:** What if you just ran the LLM every 5th frame and linearly interpolated actions in between? That's a zero-hardware, zero-retraining baseline they never test. The trajectory prediction might not be essential - just *any* form of temporal amortization might work.

5. **The Accelerator's Necessity is Questionable:** Corki-SW (software-only with CPU control) achieves *the same accuracy* as Corki-5 (Table 1-2), just with 43.6% higher latency. At 18.7 Hz, that's still faster than the 4 Hz baseline frame rate. The accelerator buys you headroom, but you could deploy the core algorithm innovation without custom hardware.

6. **Communication Latency is Measured Over WiFi:** Section 5.1 says they "establish WiFi communication between... robot arm and our server." Real industrial deployments use wired connections with sub-millisecond latency. The communication pipelining benefit shrinks dramatically with better networking.

7. **The Control Accelerator Paper is Really a Robotics Paper:** Section 4 on TS-CTC, Jacobians, and approximate matrix computation is solid robotics engineering, but it's largely orthogonal to the LLM/embodied-AI contribution. The two pieces (trajectory prediction + control accelerator) could have been separate papers. The accelerator would help *any* trajectory-following robot, LLM-controlled or not.

8. **No Real-World Deployment Results:** Despite having actual hardware, all task success rates come from the CALVIN *simulator*. Section 5.1: "the predicted trajectory is then fed back into the simulation environments." The claim about real-world applicability remains unvalidated against real objects, lighting variations, and physics discrepancies.