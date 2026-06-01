# SEAL: A Single-Event Architecture for In-Sensor Visual Localization

Let me be direct with you: this paper is not about N:M structured sparsity or dynamic pruning accelerators. It's about **in-sensor computing for visual localization using race logic**—a fundamentally different beast. However, I can still deconstruct this paper using the same forensic lens I'd apply to any architecture paper. The core challenge here isn't "finding non-zeros" but rather "how do you compute inside an image sensor without the power/area budget of a conventional processor?"

---

## Q1: Whiteboard Explanation

Imagine you're building a robot that needs to know where it is in space using a camera. The traditional pipeline is:

1. **Sensor captures image** → ADC converts analog light to digital bits
2. **Send 752×480×10 bits over MIPI cable** → expensive in energy and latency
3. **CPU runs GFTT** (Good Features to Track) to find corners
4. **CPU runs Lucas-Kanade optical flow** to track those corners across frames
5. **Backend estimates pose** using tracked keypoints

The problem? Steps 2-4 are brutally expensive. Sending a raw image costs ~100 pJ/byte just in transmission (Section 1, citing [20, 38, 71]). The frontend (steps 3-4) consumes up to 83% of total VIO latency (Section 2.1, citing [15]).

**SEAL's trick:** Move computation *into* the sensor using "race logic"—a paradigm where information is encoded as *timing delays* rather than binary values.

Here's the whiteboard version:

```
Traditional: Light → Voltage → ADC (10-bit binary) → MIPI → CPU computes
                                     ↓
                              10 bits/pixel stored

SEAL:        Light → Voltage → ATC (delay-coded) → Race Logic Processor → 1-bit edges
                                     ↓
                              1 bit/pixel stored (90% reduction)
                                     ↓
                              Keypoints only sent to CPU (>99% reduction)
```

**Race logic basics (Figure 2):** Instead of encoding "pixel value = 30" as a 10-bit binary number, encode it as "signal transitions from 0→1 after 30 time units." Now a MIN operation is just an OR gate (first signal to arrive wins), and MAX is an AND gate. Edge detection becomes: does `max(a,b)` arrive more than N time units after `min(a,b)`? If yes, it's an edge (Figure 9).

The genius is that this representation uses **one wire per variable** and **exactly one switching event per computation**—dramatically reducing power compared to Boolean logic that might toggle the same wire many times.

---

## Q2: The Key Insight

**The "Delta" (The Real Contribution):**

The paper's core contribution is a **full-system co-design** that exploits the temporal (delay-coded) representation of data at multiple levels:

1. **Analog-to-Time Converters (ATCs) replace ADCs** (Section 3.1): Standard ADCs have two stages—ATC (analog-to-time) and TDC (time-to-digital). Since race logic operates on delays directly, they *eliminate the TDC entirely*, saving power/area for the counter and latches. The key co-optimization insight (Section 3.1.1) is counter-intuitive: make the comparator *faster* (higher power), but then the ramp completes in ~100ns instead of ~100μs, so you can power-gate aggressively and end up with 4.2× *less* energy per frame (Table 2: 28.4 pJ → 6.75 pJ).

2. **Race logic enables pixel-parallel processing at extreme efficiency** (Section 3.2-3.3): A Boolean median filter for 3 pixels requires 250 gates per pixel; their temporal version requires 4 gates (a 62.5× reduction). Edge extraction: 289 gates Boolean vs. 19 gates temporal. This is why Section 3 states SEAL's race logic implementation takes "23.4× fewer gates and 22.7× less energy than its Boolean counterpart."

3. **Binary edges enable aggressive quantization downstream** (Section 4): Because edges are 1-bit, the covariance matrix in GFTT shrinks from 24 bits to 4-8 bits per element (Figure 11). This allows square roots via 100-entry LUTs instead of floating-point units.

**The "Magic Trick" (The Mechanism):**

The key architectural choice is the **temporal-to-binary collapse at the edge extraction stage** (Section 3.3). They compute edges in race logic (getting the efficiency benefits), then immediately latch the result into a 1-bit SRAM per pixel. The paper explicitly notes (Section 3.3): "accuracy results show that the temporal part of this information adds minimal value, thus we simplify by collapsing the time dimension."

This is crucial because it means the frontend processor (GFTT + Lucas-Kanade) doesn't need to operate in race logic—it can use conventional binary arithmetic, just on heavily quantized (1-bit edge) inputs. The interface between temporal and Boolean domains is essentially free because the edge detector's output is already a threshold decision.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths:

1. **End-to-end system evaluation with real VIO frameworks** (Section 5.1.3): Unlike many accelerator papers that stop at kernel benchmarks, they integrate with HybVIO and VINS-Mono and report actual trajectory error on EuRoC. Table 10 shows ±1 cm average RMS ATE change—demonstrating the approximations don't destroy accuracy.

2. **Fair baseline comparisons for energy** (Table 6-7): They include ADC, SRAM readout, and MIPI costs in their baseline (132 pJ total), not just processing. Navion [69] and RoboVisio [81] are scaled to 28nm for fair comparison. SEAL achieves 7.9× and 35.7× energy savings respectively.

3. **Latency breakdown is transparent** (Table 9): They show exactly where time is spent across different host processors (RPi4, i7, Threadripper), revealing that SEAL shifts the bottleneck from frontend to backend—an honest acknowledgment that there's now a new bottleneck.

4. **FPGA prototyping validates the digital design** (Section 5.1.2): They didn't just simulate—they prototyped on an Altera Arria 10.

### Weaknesses:

1. **The ATC area is conspicuously absent** (Section 5.1.1): They explicitly state "Obtaining the ATC area would require a layout under specific pixel size and sensor requirements, which is beyond the scope of this paper." This is a significant omission for an in-sensor design where area-per-pixel is the critical constraint. They claim they "do not introduce any extra analog components," but the power-gating circuitry (Figure 6 ii) and co-optimized comparator are modifications.

2. **Fixed vs. flexible edge threshold inconsistency** (Tables 10-11): Table 10 uses a fixed threshold (N=17 for HybVIO, N=13 for VINS-Mono), but Table 11 shows that flexible thresholding improves accuracy by 16.4%. The paper admits "implementing flexible thresholding in hardware is beyond the scope of this work"—but this is arguably essential for robust operation.

3. **Noise model is optimistic** (Section 3.2): They use synthetic salt-and-pepper noise assumptions. Real sensor noise (read noise, dark current, photon shot noise) varies with exposure/temperature. The median filter evaluation (Table 3) only shows HybVIO accuracy on EuRoC, not sensitivity to different noise conditions.

4. **The "7× energy reduction" claim requires careful parsing** (Section 5.2.2): This compares SEAL DPS (18.9 pJ total) to a baseline DPS *without processing* (132 pJ). But the baseline doesn't do anything useful—it just captures and transmits raw images. The fair comparison would be SEAL vs. baseline+off-sensor-accelerator. When they do this (Table 6), the savings are 1.5× vs. Navion for processing-only, which is still good but less dramatic.

5. **HD1K evaluation (Table 12) shows concerning outliers**: Sequences 10, 34, and 35 show SEAL performing significantly worse than baseline (13.01 vs 3.20 EPE for sequence 10). The mean EPE across all sequences is worse for fixed-threshold SEAL (1.35) than baseline (0.96).

---

## Q4: What the Authors Didn't Tell You

1. **The temporal processor latency is hidden by "pixel-parallel" framing**: Section 3 emphasizes pixel-parallel operation, but the ATC conversion happens in 100ns (Figure 7, Table 2 footnote). For a 1000 fps frame rate (1ms frame time), this seems fine. But race logic operations have depth: median filtering is 3 gates deep, edge extraction adds more. The paper never explicitly states the *temporal processor's total propagation delay* from ATC output to edge SRAM write. They list "Temporal: 0.02 pJ" in Table 7 but no latency—the dash suggests it's subsumed into the ATC timing, but this deserves clarification.

2. **SRAM costs for previous frame storage are underplayed**: Section 4.2.1 discusses recomputing vs. storing gradients, but the fundamental requirement remains: LK optical flow needs the *previous frame*. The "Previous Frame SRAM" in Figure 10 stores 752×480×1 bits = 45 KB for edge images. This is smaller than 10-bit raw (450 KB), but it's still a non-trivial on-chip SRAM that must be accounted for in the frontend processor's 0.33 mm² area.

3. **The global shutter assumption is load-bearing**: Section 3 states SEAL "inherently synchroniz[es] temporal processing across the sensor array and enabling a global shutter—crucial for visual localization." However, global shutter sensors are more expensive and have worse light sensitivity than rolling shutter sensors. This constrains deployment scenarios.

4. **Backend energy dominates in the full system** (Figure 13): SEAL's energy is 0.01 mJ/frame; the RPi4 backend consumes 238 mJ active + 163 mJ idle = 401 mJ total. SEAL's 7× sensor energy reduction translates to only 1.5× system energy reduction because the backend (93.6 ms on RPi4 per Table 9) is the elephant in the room. The paper acknowledges this but doesn't propose solutions.

5. **The accuracy vs. threshold tuning problem is unresolved**: Table 11's flexible threshold results show accuracy can improve by 16.4% with per-sequence tuning. But Table 11 also shows *worse* accuracy at some thresholds (MH_05 goes from 29cm at N=17 to 51cm at N=24). A deployed system needs automatic threshold selection, which they explicitly defer to future work.

6. **Comparison to event cameras is absent**: Event cameras (like Dynamic Vision Sensors) also promise low-latency, low-power visual sensing by only transmitting changes. The paper doesn't discuss why race logic in-sensor processing is preferable to event-based approaches for VIO—an obvious competitor in this design space.