Q1: Whiteboard Explanation

SEAL is an in-sensor computing architecture that performs visual localization preprocessing (keypoint detection and tracking) directly inside a digital pixel sensor, rather than shipping raw images off-chip.

**The Pipeline:**
1. **Pixel Array** captures light → photodiodes produce voltage signals
2. **Analog-to-Time Converter (ATC)** converts voltages to *delay-coded signals* (race logic encoding), eliminating the traditional ADC's time-to-digital converter (TDC)
3. **Temporal Processor** performs pixel-parallel denoising (median filter) and edge extraction using race logic circuits
4. **Frontend Processor** reads 1-bit edge images and executes GFTT keypoint detection + pyramidal Lucas-Kanade optical flow tracking
5. **Output** sends only keypoint coordinates and displacement vectors (~1% of raw data volume) over MIPI to a host processor running VIO backend

**The Race Logic Trick:**
Values are encoded as *delays* from a reference signal—the 0→1 transition timing carries information (Figure 2). This enables single-wire-per-variable encoding regardless of bit-width, and single-event-per-wire switching activity. MIN becomes First-Arrival (OR gate), MAX becomes Last-Arrival (AND gate), and threshold comparisons use an Inhibit operator (SR latch).

**Key Data Reduction Points:**
- Raw image → Edge image: 90% reduction (10-bit pixels → 1-bit edges)
- Edge image → Keypoints: 99%+ total reduction (Figure 1)

---

Q2: The Key Insight

The key insight is **encoding manipulation at the sensor boundary**: by switching from traditional binary ADC output to delay-coded race logic signals immediately after the pixel array, SEAL eliminates the TDC component while enabling massively parallel digital computation that exhibits analog-like energy efficiency.

This is clever because race logic's single-event-per-wire property means switching activity scales with *value count*, not *bit-width*. A 10-bit pixel value requires only one wire transition regardless of whether it represents 0 or 1023. Combined with the observation that edge images are inherently sparse (only 5–30% of pixels are non-zero, Section 4.1.1), this collapses both the storage requirements (10 bits → 1 bit per pixel) and the dynamic power consumption.

The paper makes this explicit in Section 3: "SEAL's race logic implementation takes 23.4× fewer gates and 22.7× less energy than its Boolean counterpart" for the temporal processor components.

The second insight is **aggressive frontend quantization co-designed with edge representations**: by feeding 1-bit edge data into GFTT instead of 8-bit pixels, the covariance matrix elements shrink from 24-bit to 4-bit (Figure 11), enabling a 100-entry LUT for square root computation and reducing the processing unit area from 5,397 µm² to 137 µm² (Table 4).

---

Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Multi-fidelity Methodology:** The authors employ a layered simulation approach—analog circuits in Cadence Virtuoso/Spectre X (TSMC 28nm), digital synthesis via Synopsys Design Compiler (TSMC 22nm), VCS cycle-accurate simulation, AND FPGA prototyping on Altera Arria 10 GX (Section 5.1). This cross-validation is rigorous.

2. **Full-System Integration:** Unlike many accelerator papers that stop at module-level PPA, SEAL is evaluated end-to-end with real VIO frameworks (HybVIO, VINS-Mono) on real datasets (EuRoC), measuring actual trajectory error (Tables 10–11). They even include idle power on the RPi4 host (Figure 13).

3. **Artifact Availability:** Code is publicly available (GitHub + Zenodo DOI), with Docker containers for reproducibility (Appendix A). This is exemplary.

4. **Honest Accuracy Reporting:** Table 10 shows SEAL *increases* error on some sequences (MH_01 HybVIO: 24→28 cm, V1_01 VINS-Mono: 8→12 cm). They don't cherry-pick.

**Weaknesses:**

1. **No ATC Area Characterization:** Section 5.1.1 explicitly states "we analyze this design both in terms of energy consumption and latency, but not area. Obtaining the ATC area would require a layout under specific pixel size and sensor requirements, which is beyond the scope of this paper." This is a significant gap—the authors claim area savings from eliminating TDC but cannot quantify the ATC comparator's footprint.

2. **Process Node Mismatch & Scaling:** Analog (28nm) and digital (22nm) designs use different processes, then results are scaled to 28nm using DeepScaleTool (Section 5.1.2). Cross-node scaling for mixed-signal designs is notoriously unreliable—parasitic capacitances and analog matching don't scale linearly.

3. **Fixed Edge Threshold Limitation:** The entire system uses a *static* edge threshold N (e.g., N=17 for HybVIO). Table 11 shows flexible thresholding improves accuracy by 16.4%, but Section 3.3 admits "such circuit optimizations are beyond the scope of this paper." This is a significant mode of operation left unimplemented.

4. **Baseline Fairness for Off-Sensor Accelerators:** Table 6 compares against Navion and RoboVisio, but SEAL's "Total" column includes its custom DPS while Navion/RoboVisio assume a baseline DPS. The comparison conflates in-sensor vs. off-sensor placement with the specific architectural innovations.

5. **Limited Sensor Noise Modeling:** The temporal median filter (Section 3.2) is evaluated for denoising, but there's no characterization of how ATC comparator noise, voltage ramp non-linearity, or process variation affect the delay-coded signals. Real silicon would face these challenges.

---

Q4: What the Authors Didn't Tell You

1. **The 100ns ATC Timeline is Aggressive:** Table 2 claims the SEAL ATC operates on a "100 ns" timeline vs. "100 µs" for SS-ADC. This 1000× compression requires a comparator with ~3ns delay at 0.33V bias (Figure 6 iii). They don't discuss what happens when the ramp generator can't produce a sufficiently linear slope at this speed, or how comparator metastability affects timing uncertainty.

2. **Race Logic's Warm-Up and Reset Overhead:** Race logic circuits require all wires to return to 0 between computation cycles. For pixel-parallel operation across 752×480 = 361K pixels, the global reset distribution and timing skew are non-trivial. The paper never mentions reset energy or latency.

3. **SRAM Sizing Assumptions:** Section 5.2.1 states per-pixel SRAM shrinks from 16.5 µm² to 1.65 µm² by going from 10-bit to 1-bit storage. But the frontend processor also requires storing the previous frame (Section 4.2.1 mentions "preserving spatial gradients does not eliminate the need to store the previous frame"). Where does this second frame live? The area accounting seems incomplete.

4. **The Box Filter vs. Gaussian Trade-off:** Section 4.2.2 claims the 2×2 Box filter maintains "comparable accuracy (~1 cm variation)" vs. 5×5 Gaussian. But Table 5 shows the full LK pipeline goes from 14.5 cm (baseline) to 16.8 cm (Box filter)—a 16% degradation, not "~1 cm." The isolated downsampling accuracy isn't shown.

5. **No Thermal or Leakage Analysis:** The power-gating scheme (Figure 7) assumes negligible wake-up energy and no leakage during the ~ms exposure period. At 28nm, subthreshold leakage through the comparator bias network would accumulate.

6. **EuRoC Dataset Vintage:** EuRoC (2016) uses 752×480 resolution at 20 fps—this is dated compared to modern AR/VR requirements (Section 1 cites Apple Vision Pro). The HD1K evaluation (2560×1080 @ 200 fps) is more relevant but only measures optical flow EPE, not full VIO trajectory error.