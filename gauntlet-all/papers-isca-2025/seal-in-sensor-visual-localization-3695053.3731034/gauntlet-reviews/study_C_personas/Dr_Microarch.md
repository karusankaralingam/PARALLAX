## Q1: Whiteboard Explanation

Let me walk you through what SEAL actually does at the hardware level.

**The Problem:** Image sensors produce massive data streams (hundreds of MB/s). Transmitting this over MIPI to a host processor costs ~100 pJ/byte—two orders of magnitude more than computing on that byte. For visual localization (detecting and tracking keypoints for pose estimation), you're shipping raw pixels just to extract a handful of corner coordinates.

**SEAL's Core Architectural Insight:** Replace the standard ADC with an Analog-to-Time Converter (ATC), keeping signals in the *temporal domain* as delay-coded values, then process them with race logic circuits *before* they ever become traditional binary data.

**The Pipeline (referencing Figure 3):**

1. **Pixel Array → ATC (Section 3.1):** Standard single-slope ADCs have two stages: ATC (comparator + ramp) → TDC (counter + latch). SEAL *removes the TDC entirely*. The comparator output already encodes pixel intensity as a delay from a reference edge—race logic consumes this directly. The key trick: they speed up the comparator (lower bias voltage, ~100ns conversion vs ~100µs traditional) and power-gate it aggressively (Figure 7). Faster conversion = shorter active time = lower energy despite higher instantaneous power.

2. **Temporal Processor - Median Filter (Section 3.2):** A truncated bitonic sorter using only FA (First Arrival = min) and LA (Last Arrival = max) gates. For a 3-pixel window: 4 gates total, 2.5 fJ/pixel. The race logic version is **62× smaller** than Boolean equivalent (4 gates vs 250 gates for 10-bit data).

3. **Temporal Processor - Edge Extraction (Section 3.3):** Detects edges via `max(a,b) > min(a,b) + N`. Implemented using FA, LA, Delay, and Inhibit operators (Figure 9). The output collapses from temporal delay to 1-bit binary (edge/no-edge) and stores in a per-pixel SRAM—dropping from 10-14 bits to 1 bit.

4. **Frontend Processor (Section 4):** Now operating on 1-bit edge images, GFTT keypoint detection and Lucas-Kanade optical flow run with *heavily quantized* arithmetic. Ternary derivatives ([−1,0,1]) replace Sobel filters, covariance matrix elements shrink from 24-bit to 4-bit, and square roots become a 100-entry LUT (Figure 11). Column-wise vector-parallel processing completes a 752×480 frame in <760 cycles.

**The "Data Collapse" (Figure 1):** Raw image → Edge image (90% reduction) → Keypoints+tracks (>99% reduction). Only keypoint coordinates go over MIPI.

---

## Q2: The Key Insight

**The Magic Trick:** SEAL exploits the fact that the analog-to-time conversion stage *already exists inside every single-slope ADC*—they just throw away the temporal information by immediately digitizing it. By recognizing that race logic can consume delay-coded signals natively, SEAL eliminates the TDC (counter + latch per pixel) and performs preprocessing *in the temporal domain* before collapsing to binary.

The specific mechanism enabling this is **timescale manipulation** (Section 3.1.1): By speeding up both the ramp generator and comparator (biased for ~100ns conversion instead of ~100µs), the unit delay Δt shrinks. This has two effects:
1. The comparator's active time drops by 1000×, enabling power-gating savings that dominate even though instantaneous power increases (Table 2: 6.75 pJ vs 28.4 pJ per pixel).
2. Asynchronous race logic delay chains (inverter chains for the D operator) require fewer inverters because each inverter contributes a shorter Δt.

**The structural delta vs. baseline:** Where a standard DPS has [ATC→TDC→10-bit SRAM→MIPI→Host], SEAL has [ATC→Race-logic median+edge→1-bit SRAM→Frontend processor→MIPI (keypoints only)]. The TDC is gone. The per-pixel SRAM shrinks 10×. The temporal processor fits in the reclaimed area (14.6 µm² vs 16.5 µm² saved from SRAM reduction).

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Full-system evaluation with real VIO backends:** Unlike many accelerator papers that stop at synthetic benchmarks, SEAL integrates with HybVIO and VINS-Mono, running complete trajectories on EuRoC (Table 10). They measure actual RMS ATE (16.5 cm average), demonstrating the quantization doesn't break the algorithm.

2. **Honest area accounting:** They explicitly show the race logic temporal processor (14.6 µm² per pixel) plus 1-bit SRAM (1.65 µm²) totals 16.3 µm²—*less* than the baseline 10-bit SRAM (16.5 µm²). This matters because DPS pixel area budgets are tight (4-50 µm² cited).

3. **Analog/digital co-simulation:** ATC characterized in TSMC 28nm with Spectre X (Table 2), digital synthesized in 22nm with Design Compiler, scaled appropriately. They provide switching-activity-annotated power from PrimeTime PX—not just gate count estimates.

4. **Multi-host-processor evaluation:** Testing on RPi4, i7, and Threadripper (Table 9) reveals how SEAL's benefit varies with backend capability—1.5× speedup on RPi4 vs 2.4× on Threadripper, because frontend was a larger fraction on faster hosts.

**Weaknesses:**

1. **Fixed edge threshold is a major limitation:** Tables 10-11 show accuracy swings of 10+ cm across sequences depending on threshold N. They acknowledge "flexible thresholding in hardware is beyond the scope" (Section 5.3.2), but this is critical—their best accuracy (13.8 cm, Table 11) requires per-sequence tuning, while the fixed-threshold result (16.5 cm) is what the hardware delivers.

2. **ATC area not reported:** Section 5.1.1 states "Obtaining the ATC area would require a layout under specific pixel size and sensor requirements, which is beyond the scope." But they *do* claim the design "remains within the per-pixel area budget." Without the ATC area, this claim is unverifiable.

3. **Comparison baseline asymmetry:** Navion and RoboVisio are near-sensor accelerators, not in-sensor. Comparing SEAL's 0.8 ms latency to Navion's 10.1 ms (Table 6) conflates data movement savings with processing speedups. A fairer comparison would be SEAL vs. an in-sensor design like BlissCam, which they only compare on area (0.33 mm² vs 0.5 mm²).

4. **Global shutter assumption baked in:** Section 3 notes "pixel-parallel design... enabling a global shutter." But global shutter DPS sensors are expensive and less common than rolling shutter. They don't address how SEAL would adapt to rolling shutter sensors, where rows expose sequentially.

---

## Q4: What the Authors Didn't Tell You

**The Hidden Hardware Tax:**

1. **Comparator complexity understated:** Figure 6(ii) shows a "positive feedback loop to reduce delay" in their comparator. This isn't a trivial modification—it likely increases comparator area and requires careful matching to avoid offset errors that would corrupt the delay encoding. They simulate it in Spectre but provide no layout-level validation.

2. **Race logic timing margins:** The asynchronous temporal processor (Section 3.3, 19 gates) assumes Δt ≈ 1ns (4-inverter delay). At 28nm, process variation on inverter delays can be 10-20%. With a 10-bit equivalent range (1024 levels), that's ~1µs total computation window. Any timing skew between adjacent pixels accumulates through the median filter and edge detector. They provide no analysis of how PVT (process/voltage/temperature) variation affects accuracy.

3. **The "90% data reduction" is also a 90% information loss:** They claim edges provide "sufficient structural detail" (Section 3.3), but edge images fundamentally lose intensity gradations. Their accuracy is preserved on EuRoC—a well-lit indoor dataset—but Table 12 shows 10-13× EPE increase on some HD1K sequences (high-frame-rate autonomous driving with varying illumination).

4. **Keyframe detection still happens on host:** Figure 4 shows GFTT runs only on keyframes, but the decision of *which frames are keyframes* isn't made by SEAL—it's determined by the host running the VIO backend. This means SEAL must sometimes detect keypoints on frames that turn out not to be keyframes, or the host must receive enough information to make keyframe decisions without keypoint data.

5. **SRAM read energy during LK tracking:** Section 4.2 describes patch-based readout for optical flow (13×13 to 31×31 windows). With 3-level pyramids and 200 keypoints, this means thousands of SRAM accesses per frame. Table 5 shows 9.3 pJ/pixel for LK, but this is "per pixel per frame"—the patch-based access pattern may have different row-activation costs than full-column readout used for GFTT.

6. **The "7× energy reduction" includes removing functionality:** Table 7 compares SEAL DPS (18.9 pJ) to Base DPS (132.0 pJ), but Base DPS includes 10-bit pixel values going over MIPI. SEAL only sends keypoint coordinates. The downstream host loses access to raw imagery for any purpose other than localization—no recording, no visualization, no alternative algorithms.