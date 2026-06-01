## Q1: Whiteboard Explanation

**The Core Problem:**
Imagine you're running a quantum computer. After measuring a qubit (called "readout"), you need to decide what gate to apply next based on that measurement result. This is "quantum feedback" - it's like an if-else statement, but for qubits.

**The Latency Wall (Figure 2):**
The problem is this takes ~2.15 μs total:
- **Readout pulse:** ~500 ns minimum (hardware physics limit - you can't go faster without killing qubit lifetime)
- **Classical processing on FPGA:** ~160 ns (ADC → state classification → pulse preparation → DAC)

During this entire time, your qubits are decohering (accumulating errors). The paper identifies a "660 ns latency wall" that hardware optimizations alone cannot break.

**ARTERY's Key Trick:**
Instead of waiting for the full readout to finish, ARTERY *predicts* which branch will be taken and *pre-executes* the quantum gates speculatively - just like branch prediction in classical CPUs.

**Two-Signal Predictor (Section 4, Figure 6):**
1. **P_history_1**: Historical probability from prior shots (e.g., "70% of past measurements gave |1⟩")
2. **P_read_1**: Real-time trajectory analysis - during the 2μs readout, sample IQ coordinates every ~50ns and compare the trajectory pattern to a pre-built lookup table

These combine via Bayesian inference:
```
P_predict_1 = (P_history_1 × P_read_1) / [P_history_1 × P_read_1 + (1-P_history_1) × (1-P_read_1)]
```

When P_predict exceeds threshold θ (e.g., 91%), pre-execute that branch. If wrong, apply the inverse gate (quantum gates are reversible) and then the correct gate.

---

## Q2: The Key Insight

**The Architectural Insight:**
The readout process in superconducting qubits is *continuous*, not discrete. Unlike classical branch prediction where you must wait for the full instruction to decode, quantum readout produces an evolving trajectory of IQ coordinates over ~2μs. ARTERY exploits this by treating partial readout information as early branch hints.

**The "Aha" Moment (Figure 4):**
The authors observed that readout trajectories for |0⟩ and |1⟩ diverge *early* in the readout process - often within 500-750ns. By combining this real-time trajectory information with the prior probability distribution from historical shots, they achieve >90% prediction accuracy well before readout completes.

**Why This Wasn't Obvious:**
Classical branch prediction assumes deterministic outcomes with temporal correlation (recent branches predict future branches). Quantum feedback breaks both assumptions:
- Qubits in superposition have inherent randomness
- Different quantum programs have independent branch distributions

ARTERY's innovation is recognizing that *within a single program's execution*, the historical shot distribution provides a strong Bayesian prior, while the trajectory provides the likelihood term. This hybrid approach works precisely because quantum programs run thousands of identical "shots."

---

## Q3: Evaluation Critique — Strengths and Weaknesses

### Strengths

**1. Hardware Platform Validity:**
The authors use a real 18-qubit superconducting processor (Section 6.1) with calibrated T1 times (110-140 μs), gate fidelities (99.94% single-qubit, 99.7% two-qubit), and 99.0% readout fidelity. This is not a simulation-only paper - the readout pulse dataset (4,000 pulses) comes from actual hardware.

**2. Comprehensive Baseline Selection:**
Table 1 compares against 4 legitimate baselines: QubiC [20] (Google's FPGA stack), HERQULES [31] (ML-based readout), Salathe et al. [48], and Reuer et al. [44] (RL-based). These represent the actual state-of-the-art, not strawmen.

**3. Multi-Dimensional Metrics:**
The paper reports both latency (Table 1: 2.07× speedup) AND fidelity (Figure 13: 1.24× improvement). This is crucial because reducing feedback latency should translate to reduced decoherence errors - and Figure 12(b) confirms this causality.

**4. Honest Scalability Analysis (Figure 12(d)):**
The paper explicitly identifies ARTERY's *upper bound* at code distance d=13, stating "For circuits with d>13, the cost of prediction errors will overwhelm the benefits of pre-execution." This self-imposed limitation adds credibility.

### Weaknesses

**1. The "Cherry-Pick" Concern - Benchmark Selection:**
The benchmark suite (QEC, QRW, RCNOT, DQT, RUS-QNN, Reset) is heavily biased toward algorithms where:
- Historical probabilities are highly skewed (QEC has P_history_1 < 1%, per Section 6.3)
- Branch circuits are simple single-qubit gates

Missing from evaluation: What happens when P_history ≈ 50%? Figure 14 shows "P_history only" achieves ~0.4-0.7 accuracy for DQT/RUS-QNN, but the combined predictor still requires waiting longer, resulting in higher latency. The paper doesn't adequately stress-test truly random branching scenarios.

**2. QEC Simulation Methodology Issues:**
Section 6.2 admits: "Since packages like Stim do not support feedback operations...we use Qiskit to construct and simulate a noisy d=3 surface code circuit" and "we replace the real-time decoder with a lookup table."

This is a significant methodological weakness:
- Qiskit simulation is orders of magnitude slower than Stim
- A lookup table decoder is NOT state-of-the-art (MWPM is standard)
- The comparison against "Google [42]" in Figure 12(c) is apples-to-oranges: Google's result is from *real hardware*, while ARTERY's is from *simulation*

**3. Y-Axis Scrutiny in Fidelity Plots:**
Look at Figure 13(b) - the Y-axis for RCNOT fidelity spans 96.5% to 99.5%. At depth=6, ARTERY shows ~97.5% vs ~97% for baselines. The *absolute* improvement is ~0.5%, though reported as "1.24× fidelity improvement" (misleading - this is improvement in *fidelity*, not *error rate*).

Similarly, Figure 13(d) for reset shows minimal differentiation at low qubit counts where most practical systems operate.

**4. Missing "Zero-Event" Analysis:**
The paper claims 2.07× speedup in "feedback latency" (Table 1), but how much of total algorithm runtime is actually feedback? For QEC, Section 1 states "feedback is taking more than 70% of time for readout and qubit repair" - but this is from *Google's* system, not ARTERY's. The paper never quantifies:
- What fraction of total circuit execution time is feedback?
- What's the end-to-end application speedup (not just feedback speedup)?

**5. Prediction Threshold Sensitivity:**
Figure 17 shows optimal threshold varies by benchmark (QEC wants ~95-97%, RCNOT wants ~91%). The paper sets a single threshold per benchmark using training data, but real systems would need online adaptation. No robustness analysis is provided for threshold misconfiguration.

**6. Hardware Resource Costs Omitted:**
Section 5 describes FPGA implementation with BRAM for state tables (max 2^(k-3)(k+16) bytes), decoders, etc. Table 2 shows DAC/FPGA improvements. However, there's no LUT/FF utilization comparison against baselines, no power analysis, and no discussion of whether this fits in existing quantum control systems without hardware changes.

---

## Q4: What the Authors Didn't Tell You

**1. The Recovery Penalty is Worse Than Stated:**
The paper mentions recovery requires "reversed quantum gates" (Section 3), but glosses over the *timing* implications. When prediction fails:
- You've already applied the wrong gate
- You must wait for readout to complete
- Apply inverse gate
- Apply correct gate

This means *misprediction penalty* = (wrong gate duration) + (inverse gate duration) + (correct gate duration) ≈ 3× gate time. For 30ns XY gates, that's ~90ns penalty. At 10% misprediction rate with multiple feedbacks, this compounds. The paper's "recovery" appears cheap in isolation but becomes expensive at scale.

**2. The "Trajectory Buffer" Memory Explosion:**
Section 5.1 mentions the trajectory buffer records k recent time points for table lookup. With k=6 registers (default), the state table has 2^6 = 64 entries. But the actual trajectory comparison requires storing IQ coordinates at each window (30ns intervals over 2μs = ~67 windows). The paper never addresses how trajectory history is managed or its memory footprint at scale.

**3. Inter-FPGA Communication is the Elephant in the Room:**
Section 5.2 describes a hierarchical backplane for multi-FPGA systems, claiming 48ns inter-FPGA latency via SerDes. But for a 100+ qubit system (Google Sycamore scale), feedback signals may need to traverse multiple hops. The paper only evaluates single-FPGA scenarios in detail; Table 2's "Latency" column showing decoder benefits assumes inter-FPGA transmission, but the actual multi-FPGA evaluation is thin.

**4. The "Pre-Execution on Ancilla" Trick Has Hidden Costs:**
Case 2 in Figure 3(b) suggests using ancilla qubits for pre-execution when the measured qubit is busy. But this:
- Requires available ancilla qubits (not always possible in dense QEC layouts)
- Requires additional SWAP operations to move state back
- Was never evaluated in the benchmarks

**5. Readout Pulse Duration is Fixed at 2μs:**
All experiments use 2μs readout (Section 6.1). This is unusually long compared to state-of-the-art (Google achieves 500ns readout [42]). With shorter readouts:
- The "early prediction window" shrinks proportionally
- P_read accuracy at early times would be worse
- ARTERY's advantage would diminish

The paper's impressive speedups are partly an artifact of the 2μs readout assumption. On systems with 500ns readout (Google), the hardware processing latency (160ns) becomes a larger fraction, and ARTERY's speculative window collapses.

**6. No Analysis of Correlated Errors:**
The paper assumes independent prediction across shots. But real quantum systems exhibit *correlated* errors - if one shot fails, neighboring shots are more likely to fail due to cosmic rays, TLS fluctuations, etc. The historical probability P_history would need temporal windowing, which is never discussed.

**7. The Fidelity "Improvement" Math is Misleading:**
Claiming "1.24× fidelity improvement" (Abstract, Section 6.3) when going from 80% to 82% fidelity is technically correct but obscures the more meaningful metric: *error rate reduction*. Going from 20% error to 18% error is only 10% improvement in error rate. The paper consistently frames results in the most favorable light.