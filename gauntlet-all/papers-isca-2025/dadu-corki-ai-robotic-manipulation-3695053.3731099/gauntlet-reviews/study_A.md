# Study A — Simple Directive
**Paper:** 3695053.3731099  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 08:19

---

Q1: Whiteboard Explanation

Imagine you're controlling a robot arm to pick up a coffee mug. Current embodied AI systems work like this: the robot takes a photo, sends it to a server running an LLM, waits for the LLM to predict "move 1cm right," executes that tiny movement, takes another photo, and repeats. This frame-by-frame approach is painfully slow—each cycle takes ~250ms combining LLM inference (73%), communication (17%), and control (10%).

Corki fundamentally changes this pipeline by borrowing a principle from robotics: planning and control don't need to operate at the same frequency. Instead of predicting one discrete action per frame, Corki predicts a continuous trajectory (represented as a cubic polynomial) covering the next 5+ frames. This reduces LLM calls by 5x.

The system has three key components: (1) An algorithm that outputs trajectory coefficients (a,b,c,d for x=at³+bt²+ct+d) instead of single-step actions, trained with trajectory-level supervision; (2) A hardware accelerator that converts these trajectories into high-frequency motor torque signals using task-space computed torque control, with clever optimizations—they discovered that when joints 1 and 7 move, the mass matrix barely changes, enabling selective recomputation; (3) A pipelined execution where image capture and communication happen in parallel with trajectory execution, hiding communication latency entirely.

The adaptive length mechanism is elegant: if the trajectory shows high curvature (sharp turn) or gripper state change, terminate early and replan with fresh sensor data.

Q2: The Key Insight

The key insight is that current embodied AI systems are designed from a vision-centric perspective (processing frame-by-frame like video algorithms), but robotics fundamentally operates on trajectories. In robotic systems, the perception/planning front-end doesn't need real-time performance—only the control back-end does. These two can be decoupled through trajectory as an intermediate representation, allowing the expensive LLM to run at low frequency while maintaining high-frequency (100Hz) control.

This insight enables three optimizations simultaneously: reducing LLM inference frequency (the dominant cost), hiding communication latency through pipelining, and enabling smooth high-frequency control. The trajectory representation is also more natural for training since ground-truth data was collected as trajectories anyway—decomposing into frame-by-frame actions actually loses information.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**
- Comprehensive end-to-end evaluation on real hardware (FPGA implementation, actual Franka robot, real WiFi communication) rather than just simulation
- Multiple metrics: success rate, average job length, trajectory error, latency, and energy consumption
- Ablation across trajectory lengths (Corki-1 through Corki-9) reveals the accuracy-efficiency tradeoff clearly
- Hardware resource consumption is modest (13.6% DSP, 6.6% BRAM), demonstrating practical deployability
- Sensitivity analysis on approximation thresholds shows robustness
- Cross-platform validation (V100, H100, Jetson Orin, different precision formats)

**Weaknesses:**
- Only evaluated on Calvin benchmark with a single 7-DoF arm—no real-world robot experiments beyond latency measurements
- The baseline (RoboFlamingo) uses a relatively small 3B parameter model; unclear how results scale to larger VLMs now common in robotics
- Long-tail latency is worse than baseline (56% higher variation)—problematic for safety-critical applications
- The approximation strategy for control is validated only through trajectory error, not physical robot stability
- Communication latency hiding assumes trajectory execution time exceeds communication time—may not hold for shorter trajectories or slower networks
- No comparison against action chunking methods (like ACT) that also predict multiple future actions

Q4: What the Authors Didn't Tell You

**Implementation complexity:** The paper glosses over significant training challenges. Re-training with trajectory supervision and masked embeddings likely requires substantial hyperparameter tuning. The close-loop feature randomly inserting images during training adds another dimension of complexity.

**Generalization concerns:** The cubic polynomial trajectory representation assumes smooth, slow movements. For tasks requiring quick reactive motions (catching falling objects, contact-rich manipulation), this representation may be fundamentally limiting. The authors acknowledge this in the discussion but don't quantify the failure modes.

**Safety implications:** Predicting longer trajectories means committing to actions with stale observations. While they add closed-loop features, the robot executes ~165ms of motion based on information that becomes increasingly outdated. In dynamic environments with humans, this could be dangerous.

**Hardware accelerator scope:** The control accelerator is specifically designed for 7-DoF arms using task-space computed torque control. Extending to different morphologies (humanoids, quadrupeds) or different control schemes would require redesign—this isn't a general robotics accelerator.

**Missing baselines:** No comparison to action chunking approaches (ACT, Diffusion Policy) that similarly predict multiple future actions but remain in discrete action space. These are standard in recent embodied AI work and would clarify whether the gains come from trajectory representation specifically or just reduced inference frequency.

**Energy accounting:** The 9.2x energy reduction excludes motor power, which dominates total system power. Real-world energy savings would be much smaller.