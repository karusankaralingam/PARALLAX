# Architectural Deconstruction: Hardware-aware Calibration Protocol for Quantum Computers

## Q1: Whiteboard Explanation

Let me draw you the real hardware picture here, because the authors are solving a genuinely nasty problem that classical architects rarely think about.

**The Physical Setup:**
On IBM's superconducting quantum processors (127 qubits, Eagle r3), two-qubit gates are implemented via Cross-Resonance (CR) pulses. You drive qubit A at qubit B's resonance frequency, creating an entangling interaction. The problem? The Hamiltonian you get isn't clean:

```
H(Ω_CR, Ω_T) = ν_ZX·ZX + ν_ZY·ZY + ν_IX·IX + ν_IY·IY + ν_ZI·ZI  (Equation 1)
```

You *want* the ZX term. Everything else is garbage that kills your fidelity.

**The Waveform Zoo:**
The authors offer three pulse shapes, each with different hardware costs:

1. **Echoed CR** (baseline): Two CR pulses in opposite directions. The echo cancels some unwanted terms automatically. Cheap to calibrate (1.0× cost), ~665ns duration.

2. **Multi-derivative DRAG** (Section 4.1, Equation 2): This is where it gets interesting. The standard DRAG pulse adds a derivative term to suppress leakage to the |2⟩ state. Multi-derivative DRAG applies *recursive* corrections:
   ```
   Ω_CR^P = F_Δ21^(1) ∘ F_Δ10^(1) ∘ F_Δ20^(2)(Ω)
   ```
   This targets three transitions simultaneously (|0⟩↔|1⟩, |1⟩↔|2⟩, |0⟩↔|2⟩). Calibration cost: 1.4×. The waveform gets complex (Figure 4c,d shows the wiggly result).

3. **Direct CR**: No echo needed—directly calibrates the Stark shift on the control qubit (Figure 2a,b). Shorter duration (60-80% of echoed), but calibration cost: 2.45×.

**The Profiling Policies (Section 4.2):**
Here's the actual mechanism:

- **Brute-force Clustering**: Project each qubit pair into (frequency detuning, coupling strength, anharmonicity) space. Cluster with Birch algorithm. Calibrate representatives, generalize to cluster.

- **Topology-oriented**: Exploit the heavy-hex lattice periodicity (Figure 8). Qubits at equivalent positions in unit cells share similar properties. 12 clusters based on hexagonal geometry.

- **Hardware-oriented**: This is the "system knowledge" hack. Figure 6 shows multi-derivative DRAG only helps in a specific detuning range (roughly 40-240 MHz, but with a nasty spike at ~120 MHz from two-photon transitions). Outside this range? Don't bother—use echoed CR. Also: if T2 < 85.5μs, prefer Direct CR for shorter duration.

**Parallel Calibration (Section 4.3):**
The coupling graph is partitioned into 5 subgraphs (Figure 11) where edges in each subgraph have distance ≥2. Up to 38 qubit pairs calibrated simultaneously.

## Q2: The Key Insight

The "magic trick" is **policy-based waveform selection combined with topology-aware batching**.

But let me be more precise about what's actually clever here:

**Insight 1: Not all qubit pairs benefit from expensive calibration.**

Figure 6 is the money plot. Multi-derivative DRAG provides orders-of-magnitude improvement in transition error—but *only* when qubit-qubit detuning falls in a "sweet spot." The spike at ~120 MHz (half the anharmonicity) is a two-photon resonance that DRAG can't fix. The authors explicitly state (Section 4.2.4): "for qubit pairs that reside outside a specific frequency range, calibrating multi-derivative waveform takes much longer time than echoed CR pulse and fails to eliminate error terms to an ideal extent (0.015MHz)."

This is hardware-aware in the truest sense: different physical configurations demand different calibration strategies.

**Insight 2: Heavy-hex topology is not just for connectivity—it constrains frequency planning.**

Figure 8 shows the qubit frequency coloring. The heavy-hex was designed to avoid frequency collisions (Section 4.2.3). The authors exploit this: qubits at equivalent lattice positions have similar (detuning, coupling, anharmonicity) tuples. This turns an O(N²) profiling problem into O(12) representatives.

**Insight 3: Calibration parallelism is constrained by crosstalk, not just topology.**

The minimum distance-2 requirement (Section 4.3) isn't arbitrary—simultaneous CR drives on adjacent edges would interfere. The 5-subgraph partition for 127 qubits (Figure 11) gives 38 parallel calibrations, achieving 7.9× speedup under software constraints (potentially 25× without).

**The core realization**: Calibration is not a uniform process. Treating all qubit pairs identically wastes time on pairs that can't benefit (wrong detuning range) or uses suboptimal waveforms on pairs that need shorter pulses (low T2).

## Q3: Evaluation Critique — Strengths and Weaknesses

**Strengths:**

1. **Real hardware at scale**: They calibrated 144 qubit pairs on ibm_rensselaer, ibm_nazca, ibm_sherbrooke, and ibm_brisbane. This isn't simulation. Table 1 shows Quantum Volume doubled (128→256) and EPLG reduced 2.0-2.3×. That's meaningful.

2. **Comprehensive benchmarking hierarchy**: Gate-level (IRB, Figure 12), calibration-level (Figure 15), device-level (QV, EPLG, Table 1), and application-level (Table 2). The qram_n20 benchmark at 352 ECR gates is genuinely stressing the system.

3. **Error term convergence data**: They report 99% of qubit pairs achieve <0.3 MHz error terms within four calibration rounds (Section 5.1). This is important—calibration must converge.

4. **Policy accuracy metrics**: Figure 12 reports 88.9% optimal waveform selection for Brute-force Clustering (n=7), 93.8% for Topology-oriented. This quantifies the profiling effectiveness.

**Weaknesses:**

1. **Calibration time measurements are incomplete**. Figure 15 shows the parallelization speedup, but absolute times are vague. They mention "hours or days" for IBM's baseline, but their total protocol time is presented only as relative speedup. What's the actual wall-clock time to calibrate 127 qubits with Hardware-oriented Policy?

2. **Error drift is handwaved**. Section 4.2 states "qubit pairs typically reach an error level 5× their initial value within approximately 20 hours." But Figure 12 represents "average error over the hours following calibration" (Section 5.1). They don't show a drift curve. The "Reprofiling Period" discussion (Section 5.5) mentions 5/8 qubit pairs changed optimal waveform after 8 days, but this is anecdotal.

3. **The 0.015 MHz threshold is arbitrary**. Section 5.1: "We initially set an error threshold of 0.015 MHz... If qubit pairs fail to meet this threshold after four calibration rounds, we increase the threshold to 0.3 MHz." That's a 20× relaxation. How many pairs hit this fallback? How does this affect the reported fidelities?

4. **Application benchmarks are shallow circuits**. Table 2's deepest circuit is qram_n20 with depth 794, but its fidelity is already 0.32 (after calibration). The qpe_n9 at depth 339 shows 0.98 fidelity—that's the useful regime. The benchmarks don't stress error accumulation in the interesting range.

5. **Missing baseline comparison**. They compare to "IBM's default echoed CR" but don't compare to IBM's *actual* calibration protocol. IBM performs weekly full calibration on limited pairs and daily phase calibrations (Section 3.3). What's the fidelity gap between a freshly IBM-calibrated device and their protocol?

6. **Software limitations obscure true parallelism**. Section 5.3 admits: "IBM's current pulse control software has limited support for complex pulse shapes across multiple qubit pairs." They had to split subgraphs into groups of 10. The 25× potential speedup is theoretical; 7.9× is achieved. This is an implementation artifact, not a fundamental result.

## Q4: What the Authors Didn't Tell You

**The Waveform Complexity Tax:**

Figure 4 shows the multi-derivative DRAG waveform—it's significantly more complex than echoed CR. The authors mention (Section 4.1): "a preprocessing error is sometimes detected due to the overwhelmingly complicated waveform." What's the failure rate? They don't say. They also note the pulse must be "split into two parts to avoid overly complicated custom pulse shapes" (Section 5.1). This is a hardware constraint on Arbitrary Waveform Generator (AWG) sample memory and bandwidth that they gloss over.

**Memory Requirements:**

Multi-derivative DRAG pulses are "sent to the hardware as arrays of pulse amplitudes" while Echoed CR uses "symbolic functions in Qiskit" (Section 5.1). The difference is substantial: symbolic functions are parameterized templates; amplitude arrays consume AWG memory proportional to (sample_rate × duration). At 2.5 GSa/s and ~500ns pulses, that's ~1250 samples per pulse per qubit pair. For 38 parallel calibrations with multi-derivative DRAG, that's significant AWG bandwidth.

**The 0.3 MHz Fallback is Doing Heavy Lifting:**

The paper reports >99% success at 0.3 MHz (Section 5.1). But their claimed error threshold for QEC is 3×10⁻³ (Section 5.2, citing [5]). A 0.3 MHz error term in the Hamiltonian doesn't directly translate to gate error rate. They show minimum error rate of 1.3×10⁻³ for "about 20% qubit pairs" (Section 7). That means 80% of qubit pairs are *above* the QEC threshold even after calibration.

**The T2 Cutoff is Savage:**

Section 4.2.4 labels qubits with T2 < 85.5μs as "defect qubits." Looking at Figure 9b, roughly 15-20% of qubits on ibm_rensselaer fall below 150μs, with a tail extending below 50μs. The 85.5μs cutoff (half the 172μs median) isn't principled—it's a heuristic. They're essentially writing off the worst qubits.

**Crosstalk During Calibration:**

Section 4.3 requires distance-2 separation for parallel calibration. But calibration involves Hamiltonian tomography (Section 2.2), which applies CR pulses at varying durations. During these measurements, non-local ZZ coupling (the ν_ZI term in Equation 1) affects neighboring qubits. The authors assume this is negligible at distance-2, but don't measure it.

**What Happens to Non-Connected Edges?**

The heavy-hex topology has 144 edges for 127 qubits. But QEC codes (like surface codes) may need different connectivity. The authors mention "with the heavy-hex topology and qubit number, only a QEC with a distance less than 3 can be realized" (Section 7). This is a topology limitation, not a calibration limitation—but it constrains the utility of their results for fault-tolerant computing.

**The Real Bottleneck is Classical Processing:**

Equation in Section 5.3 gives: T_total = T_classical + T_profile + N_groups × T_CR. They claim T_classical is "negligible." But the Birch clustering, graph partitioning, and SciPy optimization all run on classical computers. For a 127-qubit device, these scale modestly. For 1000+ qubit devices, the classical algorithm complexity becomes relevant—especially if profiling must be repeated after drift.

**IBM's Pulse API is Disappearing:**

The artifact appendix (Section A.1) notes: "IBM currently suspends its support for pulse-level circuits." This is significant—the entire protocol requires pulse-level access that IBM is deprecating. The work may not be reproducible on future IBM hardware without significant API changes.