# Dr. Sim's Tooling Analysis: Hardware-aware Calibration Protocol for Quantum Computers

*adjusts glasses and pulls up the methodology section*

Alright, let's talk about what's actually happening under the hood here. This paper is doing something that's simultaneously impressive and concerning from a simulation/validation perspective.

---

## 1. Tooling Breakdown

**The Good News:** This isn't paperware. They're running on *real* IBM Eagle r3 processors (ibm_rensselaer, ibm_nazca, ibm_strasbourg) with 127 qubits. That's actual silicon—well, actual superconducting transmons at 15 millikelvin. No cycle-accurate simulator pretending to be a quantum computer here.

**The Infrastructure Stack:**
- **Qiskit Pulse** for low-level waveform control
- **SciPy** for Hamiltonian tomography fitting and parameter optimization
- Custom pulse arrays sent directly to hardware (not symbolic functions for the fancy DRAG pulses)

**The Catch:** They had to split multi-derivative DRAG pulses into two parts because IBM's control electronics couldn't handle the waveform complexity. This is a *real* hardware limitation that simulation would have completely missed. Quote from Section 4.1:

> "During calibration experiments on real quantum hardware, a preprocessing error is sometimes detected due to the overwhelmingly complicated waveform."

This is the kind of thing that makes me love and hate real-system experiments. You discover constraints that no simulator would have predicted.

---

## 2. The Modeling Risk: Where's the Simulation Validation?

Here's where I get nervous. The multi-derivative DRAG theory comes from numerical simulations (reference [27]), and they're applying it to real hardware. Look at Figure 6:

> "Through numerical simulations, previous work has proved that multi-derivative DRAG waveform achieves the most significant improvement in a certain range of qubit-qubit detuning."

**The Question:** Did they validate that the simulation's predicted "sweet spot" (148-160 MHz detuning) actually matches the real hardware behavior? They *use* this range in their Hardware-oriented Policy, but I don't see a direct comparison between simulated and measured transition errors across the detuning spectrum.

They're essentially trusting that the theoretical model:
$$\Omega^P_{CR} = F^{(1)}_{\Delta_{21}} \circ F^{(1)}_{\Delta_{10}} \circ F^{(2)}_{\Delta_{20}}(\Omega)$$

...accurately captures the physics of their specific transmon implementations. The anharmonicity values, the coupling strengths, the higher-level leakage—these are all approximated in the theory.

---

## 3. The "Impossible Physics" Check

Let me sanity-check their numbers:

**Pulse Durations:**
- ECR gate: ~665 ns (stated in Section 4.2.4)
- Direct CR: 60-80% of ECR duration → ~400-530 ns

For a 5 GHz transmon with ~300 MHz anharmonicity, these timescales are reasonable. The ZX interaction strength scales as $J/(2\Delta_{12})$ where J is the coupling (~3-5 MHz typically) and $\Delta_{12}$ is the detuning. For their detuning distribution (Figure 6 bottom), most pairs are 40-200 MHz, giving ZX rates of ~10-50 kHz. A π/4 rotation (for CNOT) at 25 kHz takes ~10 μs... wait.

*rechecks*

Ah, they're driving harder. The CR drive amplitude Ω(t) in Equation 3 can be pushed to get faster gates, but you pay in leakage. The multi-derivative DRAG is supposed to suppress that leakage. The 665 ns number implies they're driving at ~MHz rates, which is aggressive but achievable with good pulse shaping.

**Decoherence Limits:**
- Median T1: 269 μs, T2: 172 μs
- Worst T2: ~20 μs (Section 4.2.4)

With 665 ns gates, you get ~258 sequential ECR gates before T2 kills you. But they note some qubits have T2 < 60 μs, giving only ~90 gates. This is why their Hardware-oriented Policy pushes Direct CR for "defect qubits"—shorter gates matter when you're racing decoherence.

**The Suspicious Number:** They claim 1.3×10⁻³ minimum two-qubit gate error. That's *really* good for a 127-qubit processor. IBM's published numbers for similar devices hover around 1-2% for ECR gates. Either their calibration is genuinely excellent, or there's some selection bias in which qubit pairs they're highlighting.

---

## 4. Artifact Availability: The Reproducibility Question

**Good:** They have a Zenodo archive (DOI: 10.5281/zenodo.15104875) with Jupyter notebooks and Python scripts.

**Bad:** From Appendix A.1:
> "Since certain results presented in this work utilized premium quantum hardware that require access tokens and IBM currently suspends its support for pulse-level circuits, this artifact provides a small scale simulation to demonstrate our workflow."

So you can't actually reproduce their main results. You can run their *workflow* on a simulator, but the whole point of this paper is that real hardware behaves differently than simulation. The batch_exp.py script exists for "users who have access to other quantum hardware platforms with pulse-level access"—but good luck getting that access.

**The Timing Problem:** They mention IBM does hourly and daily calibrations (Section 1), and their own reprofiling study (Section 5.5) shows optimal waveforms can change within 8 days. This means their results are inherently non-reproducible—even if you had access to the same hardware, the qubit properties have drifted.

---

## Discussion Question for the Student

Here's what I want you to think about:

**They claim their calibration reduces EPLG (Error Per Layered Gate) by 2.0-2.3× (Table 1). But EPLG is measured using Simultaneous Direct Randomized Benchmarking, which itself requires calibrated gates to work properly.**

How would you design an *independent* validation that their calibrated gates are actually better, without relying on benchmarking protocols that assume the gates are already reasonably calibrated? 

Consider: What happens if their calibration is overfitting to the specific random sequences used in Interleaved Randomized Benchmarking? Could you construct a microbenchmark—maybe a simple algorithm with a known output—that would catch this?

---

*The simulation is doomed to succeed. The real hardware is doomed to drift. Pick your poison.*