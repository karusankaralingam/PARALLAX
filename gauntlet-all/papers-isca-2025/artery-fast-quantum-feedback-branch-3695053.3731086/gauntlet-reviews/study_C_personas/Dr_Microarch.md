## Q1: Whiteboard Explanation

Let me reverse-engineer what ARTERY actually does at the hardware level.

**The Problem Being Solved:**
Quantum feedback (mid-circuit measurement followed by conditional operations) has a "latency wall" of ~660ns minimum: 500ns for readout + 160ns for FPGA processing (ADC→state classifier→decider→DAC). During this time, qubits decohere. The authors claim existing optimizations are already near the hardware limit (Section 2.2, Figure 2).

**The Core Mechanism:**
ARTERY borrows classical CPU branch prediction to speculatively execute quantum gates *before* the measurement result is known. Here's the actual datapath from Figure 7:

1. **State Classification Pipeline (top of Figure 7c):**
   - ADC captures readout pulse → Digital Down Converter → Buffer
   - Stream adapter windows the pulse into 30ns chunks
   - Demodulator computes I/Q coordinates via standard demodulation equations (Section 4)
   - Results pushed to a queue with depth = pulse_length/window_length

2. **The Prediction Logic:**
   - **Branch History Registers** (width k=6 by default): Store preliminary state classifications at each time window
   - **State Table** (BRAM, max 2^(k-3)(k+16) bytes): Pre-computed lookup mapping trajectory patterns → P_read_1
   - **Bayesian Predictor**: Multiplier + FIFO computing P_predict_1 = (P_history_1 × P_read_1) / [(P_history_1 × P_read_1) + (1-P_history_1)(1-P_read_1)]
   - Outputs prediction in **3 cycles** (Section 5.1)

3. **Pulse Preparation (bottom of Figure 7c):**
   - When P_predict exceeds threshold θ, **feedback trigger** fires
   - Branch decider queries **operation table** → fetches pulse address from **pulse library** (BRAM lookup table)
   - Decoder (Huffman + run-length) decompresses pulse → DAC

4. **Recovery Mechanism:**
   - If prediction wrong: apply inverse gate (quantum circuits are reversible), then apply correct branch
   - This is the key constraint—only works if post-measurement operations are reversible gates, NOT measurements (Case 4, Figure 3b)

**The Timing Dance:**
The "dynamic timing controller" (center of Figure 7c) replaces static scheduling with trigger-based execution. Instructions wait for feedback triggers rather than fixed timestamps (Figure 9a).

---

## Q2: The Key Insight

**The "Magic Trick":**
The singular clever insight is recognizing that quantum readout is a *continuous process* (not instantaneous like classical CPU branches), and you can make probabilistic predictions *during* the readout using partial IQ trajectory information, combined with historical shot statistics.

Specifically: they discretize the 2μs readout pulse into ~67 windows of 30ns each (Section 6.1), and at each window, they compute a preliminary state estimate by comparing the partial IQ trajectory against a pre-calibrated lookup table. This gives P_read_1 at intermediate times. Combined with P_history_1 (the empirical probability from prior shots of this same program), they get a Bayesian posterior that often exceeds confidence threshold θ well before readout completes.

**Why This Works for Quantum but Not Classical:**
Classical branch prediction uses *temporal correlation* between branches (recent history predicts next branch). Quantum measurement outcomes across different programs are statistically independent. However, *within a program*, the measurement statistics are reproducible across shots (Figure 4 shows prior and posterior shots have nearly identical distributions). The authors exploit this program-specific stationarity.

**The Structural Delta vs. Baseline:**
Standard feedback (QubiC, Figure 1) waits for: full readout (2μs) → demodulation → state classification → decision → pulse fetch → DAC. ARTERY adds a *parallel speculative path*: partial readout → trajectory-based prediction → speculative pulse execution, with the decision logic running concurrently with ongoing readout.

---

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Real Hardware Validation:** They use a self-developed 18-qubit Xmon processor with calibrated parameters (T1=110-140μs, single-qubit fidelity 99.94%, Section 6.1). This isn't simulation-only.

2. **Comprehensive Latency Breakdown:** Figure 2 honestly shows the latency wall components and acknowledges state-of-the-art is "close to hardware limit." They're not overselling the baseline.

3. **QEC Error Rate Comparison:** Figure 12(c) compares against Google's actual published QEC results [42], showing 2.02× improvement in logical error rate at cycle=25. This is a meaningful benchmark.

4. **Scalability Limit Analysis:** Figure 12(d) honestly shows ARTERY's benefit diminishes and hits zero at d>13 due to recovery overhead overwhelming prediction benefits. They don't hide the crossover point.

5. **Ablation Study:** Figure 14 separates contributions of P_history-only vs P_read-only prediction, showing both components are necessary.

**Weaknesses:**

1. **Cherry-Picked QEC Scenario:** The QEC evaluation uses d=3 surface code where P_history_1 < 1% (highly imbalanced). This is the *best-case* for historical prediction. Real QEC at higher distances with more errors would have less skewed distributions, reducing prediction accuracy.

2. **Simulation Gap for Logical Error Rates:** Section 6.2 admits "Due to limitations in Qiskit's syntax for feedback operations, we replace the real-time decoder with a lookup table." The 1.86× and 2.02× error rate improvements are from *Qiskit simulation*, not measured on the real 18-qubit chip.

3. **Recovery Overhead Not Fully Characterized:** Wrong predictions require: inverse gate + correct gate = 2× gate operations. For CZ gates (60ns each, Table 10), that's 120ns overhead. At 91% accuracy (Figure 17), 9% of shots pay this penalty. The latency numbers in Table 1 appear to be *average* latencies—the tail latency distribution matters for QEC decoding pipelines.

4. **Inter-FPGA Latency Buried:** The backplane communication adds 48ns per hop (Section 6.1). For multi-FPGA systems (Figure 8b shows 3-level hierarchy), the worst-case path could add 144ns+. Table 2 shows decoder latency of 13-21ns, but the combined inter-FPGA + decoding latency is only shown for specific benchmarks.

5. **Window Length Sensitivity:** Figure 16 shows 30ns is optimal, but prediction accuracy varies significantly (40-100%) across benchmarks at this setting. DQT at 0.1μs window has 2.1× longer latency than optimal—parameter tuning is critical.

---

## Q4: What the Authors Didn't Tell You

**Hidden Hardware Costs:**

1. **State Table Memory:** The state table requires 2^(k-3)(k+16) bytes per qubit readout line. With k=6, that's 2^3 × 22 = 176 bytes—small. But they mention "dynamically update the probabilities among different quantum feedback programs" (Section 4). How is this table rewritten between programs? What's the recalibration overhead when the quantum processor drifts?

2. **Huffman Table Storage:** Figure 10 shows pulse compression uses per-benchmark Huffman tables. Table 2 mentions "storage constraint of 1.4MB." For a generic quantum control system running arbitrary programs, how many Huffman tables must be stored? The paper states encoding happens "offline"—this means recompilation for new circuits.

3. **DAC Multiplexing Reality:** They claim 4→16 DACs per FPGA via compression. But Table 2 shows this varies by benchmark (4→25 for QEC, 4→19 for QRW). The "16 DACs" in Section 6.1 appears to be a middle-ground configuration, not the maximum benefit.

**Assumptions They Glossed Over:**

1. **Zero-Cost Trigger Propagation:** The "feedback trigger" mechanism (Figure 9b) assumes the trigger reaches all relevant FPGAs in time. For Case 2 (Figure 3b), the ancilla qubit q1' must be pre-prepared *before* prediction—this setup isn't free.

2. **Readout Correlation Ignored:** They assume P_read_1 from trajectory is independent of P_history_1. But in real quantum systems, readout errors are correlated with qubit state preparation errors. The Bayesian combination might double-count certain error sources.

3. **Recovery Gate Fidelity:** The "reversed quantum gates" for recovery assume perfect gate inversion. In practice, each additional gate introduces its own error (~0.06% for single-qubit, ~0.3% for two-qubit on their chip). At 9% misprediction rate with recovery requiring 2 extra gates, this adds ~0.05% error overhead per feedback.

**The Elephant in the Room:**

The paper's title says "Fast Quantum Feedback"—but the dominant speedup comes from *skipping* 500ns of readout by predicting early, not from faster classical processing. Their baseline (QubiC) already achieves 150ns classical processing (Figure 2). ARTERY's real contribution is converting 500ns of *blocking* readout into ~200-500ns of *speculative* execution. If readout latency improves (e.g., Walter et al.'s 88ns), ARTERY's benefit shrinks proportionally. They don't discuss this sensitivity.