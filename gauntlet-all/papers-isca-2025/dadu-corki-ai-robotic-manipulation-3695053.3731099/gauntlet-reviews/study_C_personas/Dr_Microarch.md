# Dadu-Corki: Algorithm-Architecture Co-Design for Embodied AI-powered Robotic Manipulation

## Q1: Whiteboard Explanation

Let me decode what's actually happening in this system at the hardware level.

**The Problem They're Solving:**
Current embodied AI robots use a frame-by-frame execution model where:
1. LLM inference predicts action for ONE frame → ~181ms (72.7% of latency, per Figure 2a)
2. Robot executes that single action → ~25ms (9.9%)
3. New image sent back to server → ~43ms (17.4%)
4. Repeat for EVERY frame

This sequential chain accumulates to ~249ms per frame—way too slow for real-time control (need 30Hz minimum, prefer 100Hz).

**The Core Hardware Mechanism:**

**(1) Algorithm Change - Trajectory Prediction Instead of Frame Prediction:**
- Instead of outputting discrete (Δx, Δy, Δz, Δα, Δβ, Δγ, gripper) per frame
- Output cubic polynomial coefficients for trajectory: `r_x(t) = at³ + bt² + ct + d` (Equation 4, Section 3.2)
- Six such polynomials for 6 DOF position/rotation, plus binary gripper
- This covers N future timesteps (up to 9 steps = 297ms) with ONE LLM call

**(2) Hardware Accelerator for Task-Space Computed Torque Control (TS-CTC):**
The accelerator (Figure 8) converts trajectory polynomials into motor torques at high frequency. The control equation is:

`τ = J^T(θ)[M_x(θ)(ẍ_d + K_p·e + K_v·ė) + h_x(θ,θ̇)]` (Equation 6)

The dataflow accelerator has five key compute blocks chained through FIFOs:
- **Pose Unit** → Forward kinematics FK(θ)
- **Velocity Unit** → Jacobian J(θ), end-effector velocity
- **Acceleration Unit** → computes accelerations
- **Force Unit** → Task space mass matrix M_x(θ), bias force h_x
- **Torque Unit** → Final joint torques

Key insight: These units form a pipeline where different links can be computed in parallel. While computing link 1's force, simultaneously compute link 2's acceleration and link 3's velocity (Section 4.2).

**(3) Approximate Computing Trick (ACE - Approximate Computing Enable):**
Figure 9 shows the magic: when joints 1 and 7 move, the mass matrix barely changes (even 29° rotation causes <0.1 absolute change). But joint 2 moving 29° causes up to 45.2% relative change.

The hardware dynamically decides whether to recompute matrices based on which joints moved and by how much. Impact factors weight each joint's contribution. If probability of needing update < threshold (40%), reuse previous values. This skips >51% of matrix updates (Section 4.3).

**(4) System Pipeline (Section 4.4):**
Communication is pipelined with execution. While robot executes trajectory steps 2-5, images are already being sent back to the server. The next LLM inference can start before the current trajectory ends.

---

## Q2: The Key Insight

**The Single Architectural "Trick":**

The paper has *two* coupled insights, but the hardware-level trick that makes it work is the **joint-sensitivity-based approximate computing for control matrices**.

The insight is stated in Section 4.3: "robotic control has a unique feature: the compute frequency is high, yet the change in each control signal is low."

**How it works mechanically:**
- For a 7-DOF robot, the Jacobian matrix is at most 6×7
- The mass matrix M_x and Jacobian J need recomputation at 100Hz
- But Figure 9 shows joints 1, 5, 6, 7 barely affect the mass matrix even with large movements
- Only joints 2, 3, 4 (the "middle" joints that change robot morphology, per Figure 10) significantly affect control parameters

The ACE (Approximate Computing Enable) unit in Figure 8 takes joint angles θ as input and computes a probability of needing updates based on "impact factors" derived from which joints moved. If below threshold, the gear icons in Figure 8 indicate those blocks reuse cached results.

**Why this is clever:**
This is NOT general-purpose approximate computing. It exploits the specific physics that end joints (shoulder rotation, wrist) don't change the robot's overall inertial properties much, while elbow/mid-arm joints do. The 51% computation skip comes essentially for free because it's baked into the robot's kinematics.

**The algorithmic insight** (trajectory vs. frame prediction) is equally important but less novel from an architecture perspective—it's the approximate computing strategy that lets them hit 100Hz control on FPGA.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **Real Hardware Implementation (Section 5.1):** They actually implemented on Xilinx Zynq-7000 FPGA, measured on real Franka Emika Panda robot, used real Wi-Fi communication. This isn't just simulation. Resource utilization is provided: 13.6% DSP, 7.8% FF, 16.9% LUT, 6.6% BRAM (Section 6.1).

2. **Comprehensive Ablation (Tables 1-2):** They test multiple trajectory lengths (Corki-1 through Corki-9), adaptive mode (Corki-ADAP), and software-only mode (Corki-SW). The Corki-5 sweet spot is empirically justified.

3. **Honest Long-Tail Analysis (Figure 14c):** They explicitly show that while average latency improves, Corki has WORSE latency variation than baseline (56% higher relative variation). This is an honest admission of the tradeoff.

4. **Multi-Baseline Comparison (Tables 3-4):** They vary the GPU (V100, H100, Jetson Orin, Xeon) and precision (FP32, FP16, INT8). Speedups hold across configurations (5.3×–6.4×).

### Weaknesses:

1. **Simulation-Only Task Success Evaluation:** All accuracy numbers (Tables 1-2) come from CALVIN simulation, not real robot experiments. The paper admits this in Section 8: "Our results indicate that the proposed method performs well in a setting where a single robotic arm manipulates objects within a confined space." They never demonstrate real-world task completion.

2. **Missing Control Frequency Claim Validation:** They claim 100Hz is crucial (Section 4.1), but never explicitly show they achieve 100Hz. Figure 13 shows Corki-5 achieves "26.9 Hz frequency" and Corki-SW only 18.7 Hz. Where's the 100Hz?

3. **Approximate Computing Threshold Selection (Section 6.4):** The 40% threshold is chosen empirically from Figure 15. But Figure 15a shows only ~1.3× speedup improvement from approximate computing—the bulk of gains come from reduced LLM inference, not the accelerator. The 29× accelerator speedup claimed in Section 6.3 needs more scrutiny.

4. **Communication Latency Hiding Assumptions:** The pipelining in Figure 1b assumes communication time < trajectory execution time. If network latency spikes (real-world Wi-Fi), this falls apart. No robustness analysis is provided.

5. **Limited Robot Scope (Section 8):** Explicitly limited to "robotic arms with 9 DOF or fewer." Humanoid robots, legged robots are acknowledged as out-of-scope. The cubic polynomial trajectory representation may not scale.

---

## Q4: What the Authors Didn't Tell You

### Hidden Hardware Costs:

1. **The "Scratchpad Memory" is Unspecified (Section 4.2):** They mention "remaining intermediate data is stored in a small scratchpad memory" but never specify its size. Given they process mass matrices and Jacobians, this could be non-trivial. The 6.6% BRAM claim seems suspiciously low for storing all reusable matrices.

2. **FIFOs and Line Buffer Sizing:** Figure 8 shows 3 FIFOs and a line buffer between Force and Torque units. No depth specifications are given. The rate mismatch they mention implies the line buffer may need to be substantial.

3. **Micro-Controller Overhead:** The "simple micro-controller" managing control flow in Figure 8 is hand-waved. This coordinates the ACE decisions, dataflow scheduling, and approximate computing enables—potentially a complex state machine.

### Glossed-Over Latencies:

4. **ViT Encoding for Close-Loop Features (Section 3.4):** They casually mention "images are encoded using an encoder network ViT" for closed-loop feedback. ViT inference is NOT free—it adds latency on the robot side. This isn't included in their latency breakdown.

5. **Waypoint Identification Algorithm Latency:** Algorithm 1 claims "<500 FLOPs" but this runs on what processor? If on the micro-controller, it adds cycles. If on the main CPU, it's outside their measurement scope.

### Questionable Assumptions:

6. **The 30Hz Camera Assumption:** Section 3.3 assumes "the camera sensor works in a 30 Hz frequency." Many manipulation scenarios need higher-rate feedback. Their trajectory length is fundamentally limited by this assumption.

7. **Cubic Polynomial Sufficiency:** They justify cubic polynomials as capturing "changes in velocity and acceleration" (Section 3.2), but manipulation tasks with collisions, contact forces, or dynamic objects may need higher-order representations. No analysis of when cubics fail.

8. **Training-Inference Trajectory Length Mismatch:** Section 3.3 admits "accuracy is higher when the actual trajectory length is consistent between training and inference." Corki-ADAP varies length at runtime—they never quantify the accuracy penalty of this mismatch.

### What Figure 2 Doesn't Show:

9. **Motor Power is Excluded:** Section 8 admits "the computing system inside the robot accounts for 40.6% of the total system power consumption (excluding server power)." The 9.2× energy reduction (Section 6.3) is only for compute, not the full robot. Real energy savings are ~4× at best.

10. **The Intel i7-6770HQ Strawman:** They use the Franka's onboard CPU (i7-6770HQ, a 2015 mobile chip) as the control baseline. This is conveniently slow. A modern ARM core or even the same FPGA running unoptimized control would be a fairer comparison.