# Study B — Rich Directive
**Paper:** 3695053.3731099  
**Model:** azure/anthropic/claude-opus-4-5 (via https://inference-api.nvidia.com/v1)  
**Generated:** 2026-06-01 08:19

---

Q1: Whiteboard Explanation

Imagine you're building a robot arm that uses an AI model to pick up objects. The current approach works like this: the robot takes a photo, sends it to a server running a large language model (LLM), waits for the LLM to predict "move 1cm left," executes that tiny movement, takes another photo, and repeats. This happens 30 times per second, creating a massive bottleneck because every single frame requires an expensive LLM inference plus network communication.

Corki fundamentally restructures this pipeline around a simple insight from robotics: planning doesn't need to happen at the same frequency as control. Instead of predicting one discrete action per frame, Corki trains the LLM to output a continuous trajectory—a cubic polynomial describing the robot's path over the next ~165ms (roughly 5 frames). The key equation is r(t) = at³ + bt² + ct + d for each dimension of movement.

The architecture has three main components: (1) A modified policy head that outputs trajectory coefficients instead of single-step actions, trained with trajectory-level supervision rather than frame-level MSE loss. (2) An FPGA accelerator that converts these trajectories into high-frequency torque signals (100Hz) for the robot's motors, using task-space computed torque control. The accelerator exploits data reuse across kinematics computations and employs approximate computing—since joint movements between control cycles are tiny, parameters like the mass matrix often don't need recomputation. (3) A pipelined system design where new camera frames are transmitted back to the server while the robot continues executing its current trajectory, hiding communication latency.

The result: LLM inference frequency drops by 5×, communication overlaps with execution, and the robot actually achieves higher task success rates because trajectory supervision is more natural for robotics than frame-by-frame discrete actions.

Q2: The Key Insight

The central insight is that current embodied AI systems are designed from a computer vision perspective (frame-by-frame processing) rather than a robotics perspective (trajectory-based planning and control). This mismatch is both unnecessary and harmful.

The key difference from prior work is recognizing that LLM inference, robot control, and communication operate at fundamentally different frequencies and can be decoupled. Traditional robotic systems have long used trajectories as an intermediate representation between low-frequency planners and high-frequency controllers—Corki applies this principle to LLM-based systems where no one had thought to question the frame-by-frame paradigm.

What makes this insight non-obvious is that it required challenging a deeply ingrained assumption inherited from video processing algorithms. The embodied AI community adopted frame-by-frame prediction because that's how vision models work, not because it's optimal for robotics. The authors demonstrate that trajectory prediction not only reduces inference frequency (an efficiency win) but actually improves task success rates (an accuracy win)—the trajectory representation provides more natural supervision during training.

The hardware insight is secondary but valuable: robotic control computes at high frequency but with minimal change between cycles, enabling joint-specific approximate computing where parameters are selectively reused based on which joints moved significantly.

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **End-to-end system evaluation**: The paper evaluates on the Calvin benchmark with both seen and unseen tasks, measuring actual task success rates rather than proxy metrics. The 17.3% improvement in average job length on seen tasks is substantial.

2. **Real hardware implementation**: The FPGA implementation on a Zynq-7000 with measured resource utilization (13.6% DSP, 6.6% BRAM) demonstrates feasibility. Using actual communication latency measurements with a real Franka Panda robot arm adds credibility.

3. **Comprehensive ablation**: The paper systematically varies trajectory length (Corki-1 through Corki-9), evaluates adaptive length selection, and isolates the accelerator's contribution (Corki-SW variant).

4. **Honest long-tail latency analysis**: Figure 14c acknowledges that Corki exhibits 56% worse latency variation than the baseline—a real trade-off they don't hide.

**Weaknesses:**

1. **Limited baseline comparisons**: The paper only compares against RoboFlamingo. There's no comparison with other recent embodied AI systems like RT-1, RT-2, or Octo, nor with classical trajectory prediction methods from robotics.

2. **Questionable control frequency claims**: The paper claims 100Hz control is necessary, but the actual Corki-5 achieves only 26.9Hz. The accelerator achieves 29× speedup over CPU control, but absolute frequency numbers for the accelerator are not clearly stated.

3. **Simulation-only accuracy evaluation**: While latency uses real hardware, task success rates come entirely from Calvin simulation. No real-world manipulation experiments are presented despite having access to a physical Franka arm.

4. **Weak approximate computing justification**: The 51% matrix update avoidance claim lacks rigorous error analysis. The sensitivity study in Figure 15 shows trajectory error increases with approximation threshold, but the relationship to task success is unclear.

5. **Close-loop feature under-evaluated**: Section 3.4 introduces close-loop features for sensing environmental changes, but this is never quantitatively evaluated in the results.

Q4: What the Authors Didn't Tell You

**Engineering complexity hidden**: The paper glosses over significant integration challenges. Converting existing embodied AI models to trajectory prediction requires retraining from scratch—this isn't a drop-in optimization. The masked policy head training (Figure 4) is presented as straightforward but likely required substantial hyperparameter tuning.

**Generalization limitations**: The approach is validated on a single 7-DoF arm in constrained tabletop scenarios. The Discussion section admits this won't directly apply to humanoid robots or fast-moving systems, but doesn't quantify how trajectory prediction degrades with higher DoF or faster dynamics. The cubic polynomial assumption becomes limiting for complex movements.

**Real-time guarantees absent**: Despite framing around real-time constraints, the paper never provides worst-case latency bounds. The 56% higher latency variation with Corki means unpredictable performance—problematic for safety-critical applications.

**Communication assumptions**: The pipelining strategy assumes WiFi communication can complete within one trajectory execution period (~165ms). In congested networks or with larger image sizes, this assumption breaks down. The paper uses synchronized timestamps but doesn't discuss clock drift or network jitter.

**Approximate computing risks**: The joint-based approximation decides dynamically whether to recompute parameters. In adversarial cases (simultaneous movement of joints 2-4), the system could accumulate errors. The 40% threshold choice appears empirical rather than principled.

**Cost of trajectory supervision**: Training with trajectory-level loss requires ground truth trajectories, which the paper notes "was in the form of trajectory at first" in datasets. However, many newer embodied AI datasets provide only discrete actions—retrofitting trajectory supervision may not always be possible.

**Energy claims misleading**: The 9.2× energy reduction applies only to the computing system, which is just 40.6% of total robot power. Actual system-level savings are closer to 3-4×.