# Evaluation Methodology Audit: "Hardware-aware Calibration Protocol for Quantum Computers"

*Adjusts glasses and pulls up the paper's figures*

Alright, let's dissect this evaluation section. The claims are impressive—1.84× reduction in two-qubit gate error, 8-25× calibration speedup, doubled Quantum Volume. But as I always say: **the devil is in the experimental design**.

---

## 1. Methodology Audit: What They Did

**Hardware Platform:**
- IBM Eagle r3 processors (127 qubits): `ibm_rensselaer`, `ibm_nazca`, `ibm_strasbourg`
- Heavy-hex topology
- Interleaved Randomized Benchmarking (IRB) for fidelity measurement

**Benchmark Suite:**
- Gate-level: IRB across all 144 qubit pairs
- Device-level: Quantum Volume, Error Per Layered Gate (EPLG)
- Application-level: OpenQASMBench (adder, ising, qpe, cat_state, ghz_state, qram, dnn)

**This is actually a reasonably comprehensive evaluation hierarchy.** They didn't just cherry-pick one metric—they went from individual gates up to full applications. Credit where due.

---

## 2. The 'Gotcha' Graphs: Where Things Get Interesting

### Figure 12 & 13: The Waveform Selection Results

Look carefully at Figure 12. Notice how the error bars on some qubit pairs span nearly an order of magnitude (e.g., pairs in the 10⁻³ to 10⁻² range). The paper reports "mean and standard deviation" from 5 repetitions, but:

> **Critical Question:** With only 5 repetitions and known drift in quantum systems (they mention "5× error drift within 20 hours"), how confident are we that the "optimal" waveform selection is stable?

They acknowledge this partially in Section 5.5 (Reprofiling Period): "eight days later, five out of eight qubit pairs experience changes in the optimal pulse waveform." **So 62.5% of their selections became invalid within 8 days.** This is buried in the text, not prominently displayed.

### Figure 14: The Normalized Comparison

*Here's where my eyebrow raises.*

The Y-axis shows metrics "normalized to optimal." But what is "optimal"? They define it as:
> "The optimal calibration cost is calculated based on calibrating all three possible waveforms for every qubit pair."

This is a **self-referential baseline**. They're comparing their method against a hypothetical exhaustive search that nobody would actually do. The real comparison should be against:
1. IBM's default calibration protocol
2. Other published calibration methods (e.g., Floquet, Snake optimizer)

They mention Snake optimizer [20] and Floquet [3] in Related Works but **never benchmark against them directly**. The excuse? "Orthogonal work." Convenient.

### Figure 15: Calibration Time Speedup

The 7.9× speedup claim comes with a massive asterisk:
> "We encountered difficulties when attempting to calibrate the either of the first four subsets simultaneously... we divided these subsets into smaller groups of 10 qubit pairs each."

So the "ideal parallelization" (25×) is **theoretical**, and the "real parallelization" (7.9×) is what they actually achieved due to IBM's software limitations. The gap between 7.9× and 25× is substantial—and it's a hardware/software constraint, not a fundamental limitation of their method.

**The honest headline should be:** "7.9× speedup achieved, 25× theoretically possible."

---

## 3. The Missing Data: What I Would Have Loved to See

### A. Sensitivity to Clustering Hyperparameters

They test clustering sizes n=3, 5, 7 (Figure 7), but:
- Why these specific values?
- What happens at n=10 or n=15?
- The topology-oriented approach fixes n=12 based on heavy-hex geometry—but what if the topology changes?

**Missing:** A systematic sweep showing the fidelity-vs-calibration-cost Pareto frontier as a function of cluster size.

### B. Cross-Device Generalization

They test on three IBM machines (rensselaer, nazca, strasbourg), all Eagle r3 processors. But:
- Do the profiling results transfer between machines?
- If I profile on `ibm_rensselaer`, can I use those waveform selections on `ibm_nazca`?

**Missing:** Transfer learning experiments across devices.

### C. The "Defect Qubit" Threshold

They define defect qubits as T₂ < 85.5 μs (half the median). This is arbitrary:
> "Qubits with a decoherence time (e.g., T₂) shorter than 85.5 μs (half of the median value) are labeled as defect qubits."

**Missing:** Sensitivity analysis on this threshold. What if it's 0.4× or 0.6× the median?

### D. Application-Level Error Bars

Table 2 shows error ranges "based on a statistical significance of 95%," but:
- The fidelity improvements are often within the error bars (e.g., adder_n4: 0.87±0.004 vs 0.90±0.004)
- For qram_n20, the fidelity is 0.26 vs 0.32—but with ±0.032 and ±0.023 error bars, these could overlap

**Missing:** Statistical significance tests (p-values) for the application-level improvements.

---

## 4. The Baseline Validity Check

### Is the Baseline State-of-the-Art?

The baseline is "IBM's default ECR pulse configuration." This is reasonable for a practical comparison, but:

1. **IBM's calibration is not static.** They mention IBM does "weekly full calibration" and "daily phase calibrations." Did they control for when IBM's calibration occurred relative to their experiments?

2. **No comparison to academic SOTA.** Methods like:
   - Floquet calibration [3]
   - Snake optimizer [20]
   - NAPA [28]
   
   ...are mentioned but not benchmarked against.

### The "Cherry-Pick" Check

The application benchmarks (Table 2) are from OpenQASMBench, which is standard. However:
- The largest circuit (qram_n20) has only 20 qubits on a 127-qubit machine
- No benchmarks approach the full 127-qubit scale
- The "hard" cases (qram_n20 with 0.26 fidelity) show the method is still far from practical utility

**Question:** Why not test on 50+ qubit circuits? The machine has 127 qubits.

---

## 5. The "Zero-Event" Reality Check

The paper optimizes for **two-qubit gate fidelity**. But in real datacenter/cloud quantum workloads:

1. **Queue times dominate.** If calibration takes 1 hour but queue time is 4 hours, the 7.9× calibration speedup is less impactful than it appears.

2. **Drift during execution.** They show 5× error drift in 20 hours. For long-running variational algorithms (VQE, QAOA), the calibration might be stale by the time the job runs.

3. **Error correction overhead.** They claim some qubits reach below the QEC threshold (3×10⁻³), but:
   > "With the heavy-hex topology and qubit number, only a QEC with a distance less than 3 can be realized..."
   
   Distance-3 codes are barely useful. The practical QEC benefit is speculative.

---

## Discussion Questions for the Student

1. **On Reproducibility:** The artifact appendix notes that "premium quantum hardware require access tokens and IBM currently suspends its support for pulse-level circuits." How would you evaluate this work if you can't access the same hardware?

2. **On Generalization:** The profiling policies are designed for heavy-hex topology. If Google's Sycamore (grid topology) or IonQ's trapped-ion systems (all-to-all connectivity) were used, would the topology-oriented representative policy still work?

3. **On the 8-Day Drift:** If 62.5% of waveform selections change within 8 days, what's the practical deployment model? Daily reprofiling? Continuous online learning?

4. **On the QEC Claims:** The paper positions itself as advancing "fault-tolerant quantum computing," but the actual QEC experiments are absent. Is this overselling?

---

## Summary Verdict

**Strengths:**
- Multi-level evaluation (gate → device → application)
- Real hardware experiments at 127-qubit scale
- Honest acknowledgment of software limitations (7.9× vs 25× speedup)

**Weaknesses:**
- No direct comparison to other calibration methods (Floquet, Snake)
- Temporal stability of profiling results is concerning (62.5% drift in 8 days)
- Application benchmarks don't scale to full device size
- Statistical significance of improvements is questionable for some benchmarks

**The Bottom Line:** This is solid systems work with practical impact, but the evaluation would be stronger with head-to-head comparisons against competing methods and longer-term stability studies. The QEC claims are aspirational rather than demonstrated.

*Closes laptop*

Now, who wants to discuss why they didn't test on a 100-qubit circuit?