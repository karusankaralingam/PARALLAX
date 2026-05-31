# Blind Evaluation Prompt

You are an expert evaluator in computer architecture and quantum computing systems. You will read two analyses of the same research paper and score them on six dimensions using the rubric below. The analyses are labeled **Analysis A** and **Analysis B**. The order is randomized — you do not know which was written by a human researcher and which by an automated system. **Judge only the content, not the source.**

---

## Paper Being Evaluated

**Title:** Hardware-Aware Calibration Protocol for Quantum Computers
**Venue:** ISCA 2025
**Topic:** A calibration scheduling framework for IBM superconducting quantum processors that selects per-qubit-pair pulse waveforms based on physical parameters and parallelizes calibration via graph partitioning.

You do not need to have read the paper. Judge the analyses solely on internal consistency, specificity, and reasoning quality. Domain knowledge of quantum computing systems is helpful but not required.

---

## Scoring Rubric

Score each dimension from 1–5 for both Analysis A and Analysis B independently.

### Dimension 1: Mechanistic Accuracy
*Does the analysis correctly describe what was built? Could someone reconstruct the core mechanism from this description?*

| Score | Criteria |
|-------|----------|
| 5 | Precise and complete. All key structures, policies, and datapath modifications correctly described. No mischaracterizations. A reader unfamiliar with the paper could reconstruct the core mechanism. |
| 4 | Mostly accurate with minor omissions. Core mechanism correct but some secondary details missing or slightly imprecise. |
| 3 | Generally correct but incomplete. High-level idea is right but important implementation details missing or vague. |
| 2 | Partially correct with significant errors. Some aspects correct, others wrong or seriously mischaracterized. |
| 1 | Fundamentally wrong or superficial. Major misunderstandings, or merely restates the abstract. |

### Dimension 2: Insight Depth
*Does the analysis identify the core insight that makes the mechanism work — as distinct from merely describing it?*

The "insight" is the non-obvious reason *why* this approach works: the key observation, the structural property being exploited, the asymmetry the mechanism leverages.

| Score | Criteria |
|-------|----------|
| 5 | Identifies a core insight that is non-obvious, correctly stated, and distinct from the mechanism description. Changes how you think about the problem. |
| 4 | Identifies a meaningful insight but either partially obvious from the paper's framing or could be stated more precisely. |
| 3 | States something correct and relevant but doesn't go beyond what the paper itself explicitly claims. |
| 2 | Attempts an insight but it is trivial, vague, or not clearly distinct from the mechanism description. |
| 1 | No insight identified. Describes what was built but never addresses why it works. |

### Dimension 3: Critical Rigor
*Does the analysis identify genuine weaknesses in methodology, evaluation, or assumptions — not just surface-level complaints?*

Strong critique is specific: names the exact weakness, explains why it matters, ideally suggests what evidence would resolve the concern.

| Score | Criteria |
|-------|----------|
| 5 | Identifies multiple specific, substantive weaknesses with clear reasoning for why each matters. Distinguishes fundamental limitations from minor gaps. Fair — acknowledges strengths before identifying weaknesses. |
| 4 | Identifies at least one significant weakness with good reasoning. May miss secondary issues but primary critique is well-targeted. |
| 3 | Identifies real weaknesses but reasoning is generic or incomplete. Told *what* is weak but not fully convinced of *why it matters*. |
| 2 | Superficial or generic critique. No specificity about what is actually missing or why. |
| 1 | No meaningful critique, or critique that is factually wrong. |

### Dimension 4: Breadth of Perspective
*Does the analysis connect the work to ideas, techniques, or domains beyond the paper's own scope?*

| Score | Criteria |
|-------|----------|
| 5 | Makes surprising, valid connections to ideas outside the paper's scope that genuinely enrich understanding. Specific and technically grounded. |
| 4 | Makes at least one good cross-domain connection that goes beyond the paper's own related work section. |
| 3 | Mentions related work or adjacent fields but connections are obvious or already noted by the authors. |
| 2 | Stays entirely within the paper's own scope. No attempt to contextualize beyond what the paper says. |
| 1 | No connections to external ideas. Treats the paper as if it exists in isolation. |

### Dimension 5: Calibration
*Are the analysis's claims appropriately confident? Does it get the "size" of the contribution right?*

| Score | Criteria |
|-------|----------|
| 5 | Claims well-calibrated throughout. Confident where evidence is strong, hedged where speculating. Correctly sizes the contribution — neither breathless nor dismissive. |
| 4 | Mostly well-calibrated with occasional over- or under-confidence. Gets overall contribution size approximately right. |
| 3 | Generally reasonable but some systematic bias — consistently too generous or too harsh. Doesn't distinguish confident claims from speculation. |
| 2 | Noticeably miscalibrated. Either treats everything as a breakthrough or dismisses valid contributions. |
| 1 | Severely miscalibrated. Fundamentally misreads the significance of the work. |

### Dimension 6: Usefulness
*If you had 20 minutes before a meeting about this paper, would reading this analysis prepare you well?*

| Score | Criteria |
|-------|----------|
| 5 | Prepares you as well as or better than reading the paper itself under time pressure. You could discuss the mechanism, strengths, weaknesses, and broader significance. |
| 4 | Good preparation. You'd understand the core contribution and main limitations, though might miss some nuances. |
| 3 | Adequate preparation. Reasonable overview but might be caught off guard by pointed questions about methodology or mechanism details. |
| 2 | Weak preparation. Surface-level understanding, would be exposed quickly in discussion. |
| 1 | Would mislead you. Gives a wrong or seriously incomplete picture. |

---

## Analysis A

**Q1: Whiteboard Explanation**

The paper proposes a calibration protocol for the two-qubit echoed cross resonance gate on quantum computers. The protocol optimizes for high gate fidelity and reduced calibration time and consequently system downtime for calibration.

The protocol proceeds in four steps. First, the candidate waveforms that implement the target gates are identified. The different shapes offer different fidelities, gate duration, and calibration cost.

Second, depending on the hardware specific features of each qubit pair, such as coupling strength, topology, or hardware details like T1/T2 time, the optimal pulse for every pair is chosen. This step has a huge optimization — they cluster the qubit pairs based on the above features and pick representative pairs for every cluster. Thus the pulse optimization only has to be done for a fraction of the qubit pairs. The tradeoff here is that more clusters lead to better accuracy but fewer groups would mean a faster calibration process. They explore two clustering methods — one brute-force with number of clusters as a hyperparameter. The second method uses the topological position of the qubit pair to do clustering. For IBM machines, the heavy-hex topology offers a lot of symmetry and we naturally arrive at a cluster size of 12. They also specifically recognize that some qubits have difficult hardware parameters like low T2 time, and profile them separately.

Third, given the selected optimal pulses, the actual calibration is done in parallel by dividing up the connectivity graph into disjoint graphs with qubit pairs separated by at least 2 edges. Calibration is done in parallel on all the qubit pairs in each subgraph.

Fourth, they benchmark the method at gate level (84% reduction 2 qubit gate error), calibration level (7.9x faster calibration time), device level (2x quantum volume) and application level (3-8% error rate improvement across 8 benchmarks).

**Q2: The Key Insight**

They reduce calibration time by doing the pulse optimization on fewer, representative qubits and the calibration in parallel. They improve accuracy by being hardware-aware and keeping multiple waveforms with different properties as candidates.

**Q3: Evaluation Critique**

Strengths:

The hardware aware policy of choosing the optimal waveform works well because the non-defect qubit pairs that were picked and tuned as per this policy actually maintained their profile over the eight day window. The defect qubit pairs however did drift away from the selected pulse and had to be recalibrated. This shows that they did get the criteria to pick the defect qubit pairs right and for the "well-behaved" qubit pairs, the problem of drift was actually significantly mitigated, which can reduce the frequency of calibrations required on majority of qubits.

Weaknesses:
1. This protocol is very specifically tailored to the IBM heavy-hex superconducting architecture. There is no telling how well this calibration protocol would do for a different superconducting device topology or even a different hardware technology.
2. The application level benchmarking was opaque — all the circuits have fewer qubits than the target device, which makes mapping and routing the circuit a huge part of the error on the circuit. Since the protocol is sensitive to qubit pair location, there should have been control for the effect of mapping and routing as well. The results can look vastly different if say, a defect qubit pair is/is not included in the subgraph on which the circuit is being executed.
3. The evaluation configuration mentions that the gate error reported includes drift over a few hours post calibration. However, (1) we do not see how the measurements are spread out in the time post calibration (2) Later on an eight day window of "stable optimal pulse" without recalibration is described. It would be interesting to see drift in error rate over the full eight day window.

**Q4: What the Authors Didn't Tell You**

"Constraints from device software" reduce the expected calibration level speedup from 25x to 8x with no explanation. I also don't see much explanation of the baseline they use and comparison to ANY other calibration methods. Also the references to QEC and being below threshold do not hold much weight since they are talking about very low code distance (mentioned in the conclusion).

---

## Analysis B

**Q1: Whiteboard Explanation**

Here's the data flow stripped of jargon:

**The Problem Being Solved:**
Superconducting quantum computers use microwave pulses to manipulate qubits. The "Echoed Cross-Resonance" (ECR) gate is the standard two-qubit gate on IBM hardware. The issue is that IBM applies the *same* pulse waveform to all 144 qubit pairs on a 127-qubit chip, ignoring that each pair has different physical characteristics (frequency detuning, coupling strength, decoherence times).

**The Actual Mechanism:**

1. **Waveform Selection:**
   - For each qubit pair, three candidate pulse shapes exist:
     - **Echoed CR**: The default. Two CR pulses with opposite signs to cancel unwanted Hamiltonian terms. ~665ns duration.
     - **Multi-derivative DRAG**: Adds derivative correction terms to suppress leakage to the |2⟩ state. ~1.4× calibration cost.
     - **Direct CR**: Removes the "echo," requires explicit phase calibration. ~60-80% of Echoed CR duration, ~2.8× calibration cost.

2. **The Selection Policies:**
   - **Brute-force Clustering**: Group qubit pairs by (frequency detuning, coupling strength, anharmonicity) using Birch clustering. Calibrate all three waveforms on one representative per cluster. Generalize the winner.
   - **Topology-oriented**: Exploit heavy-hex lattice regularity — only 12 representative positions exist. Calibrate representatives, generalize.
   - **Hardware-oriented**: Add hard constraints — if frequency detuning is 148-160 MHz, multi-derivative DRAG fails (two-photon resonance). If T2 < 85.5 μs (half median), use Direct CR because shorter duration beats higher fidelity.

3. **Parallel Calibration:**
   - The coupling graph is partitioned into 5 calibration subgraphs where edges are at least distance-2 apart.
   - Allows calibrating up to 38 pairs simultaneously without crosstalk interference.

**The Skeptic's Check:**

1. **Calibration Cost is Still Enormous:** Table in Section 5.3 shows full calibration takes ~10 hours. The "8-25× speedup" is relative to sequential calibration of all three waveforms on all pairs — but IBM's default only calibrates Echoed CR. The protocol costs more than IBM's default in absolute terms.

2. **The "0.015 MHz Error Threshold" is Soft:** Section 5.1 admits: "If qubit pairs fail to meet this threshold after four calibration rounds, we increase the threshold to 0.3 MHz." That is a 20× relaxation. The headline numbers assume the tight threshold; the fallback is much weaker.

3. **IBM's Software Limits the Parallelism:** Section 5.3: "We encountered difficulties when attempting to calibrate the first four subsets simultaneously... we divided these subsets into smaller groups of 10 qubit pairs each." Theoretical 25× speedup becomes 7.9× because IBM's pulse control software cannot handle complex waveforms on >20 pairs simultaneously.

4. **Reprofiling Period is Unclear:** Section 5.5: "Eight days later, five out of eight qubit pairs experience changes in the optimal pulse waveform." This means profiling results drift on weekly timescales. No automated drift detection mechanism is proposed.

5. **The QEC Claim is Aspirational:** About 20% of qubit pairs reach below the QEC threshold — but only a distance-3 QEC is realizable on this topology, and distance-3 codes provide essentially no error suppression.

**The Delta vs. Baseline:**

| Aspect | IBM Default | This Paper |
|--------|-------------|------------|
| Waveform | Single (Echoed CR) for all pairs | 3 candidates, selected per-pair |
| Selection Logic | None | Clustering / Topology / Hardware rules |
| Calibration Parallelism | Implicit | Explicit graph partitioning (5 subgraphs) |
| Hardware Awareness | Frequency collision avoidance | + Detuning-dependent waveform + T2-aware duration |

**Q2: The Key Insight**

The entire paper relies on Figure 6. Multi-derivative DRAG works by adding correction terms proportional to the derivative of the pulse envelope to suppress leakage to the |2⟩ state. The recursive formula (Equation 2) targets three transitions simultaneously. But when the qubit-qubit frequency detuning approaches half the anharmonicity (~160 MHz for transmons), a two-photon transition becomes resonant, and the correction fails catastrophically.

The insight: instead of blindly applying the fanciest pulse everywhere, use a lookup table based on physical parameters:
- If detuning ∈ [148, 160] MHz → Multi-derivative DRAG fails → use Echoed CR
- If T2 < 85.5 μs → use Direct CR (shorter duration)
- Otherwise → profile and pick the best

This is hardware-aware dispatch, not magic. The parallelization is graph coloring with distance-2 constraints — a solved problem applied to a new domain.

**Q3: Evaluation Critique**

*Methodology Audit:*

**Hardware Platform:** IBM Eagle r3 processors (127 qubits). Interleaved Randomized Benchmarking for fidelity measurement. Evaluation hierarchy: gate → device (Quantum Volume, EPLG) → application (OpenQASMBench).

**The 'Gotcha' Graphs:**

Figure 12 & 13: Only 5 repetitions per measurement with known drift (5× error drift within 20 hours). With this variance, optimal waveform selection may not be stable. Section 5.5 confirms: 62.5% of waveform selections became invalid within 8 days — buried in the text, not prominently displayed.

Figure 14: The baseline is "optimal" = calibrating all three waveforms for every qubit pair. This is self-referential. The real comparison should be against IBM's default protocol and other published methods (Floquet, Snake optimizer). These are cited in Related Work but never benchmarked against. The excuse: "orthogonal work."

Figure 15: The 7.9× speedup comes with a major asterisk — IBM's software cannot handle the full parallelization. Theoretical 25× vs. achieved 7.9×. The honest headline should be "7.9× achieved, 25× theoretically possible."

**The Missing Data:**

1. No systematic sweep of cluster size showing the fidelity-vs-calibration-cost Pareto frontier.
2. No cross-device transfer experiments — do selections on one IBM machine transfer to another?
3. Defect qubit threshold (T₂ < 85.5 μs = half median) is arbitrary with no sensitivity analysis.
4. Application-level improvements often fall within overlapping error bars. No p-values reported.

**Baseline Validity:**

IBM's calibration is not static — did they control for when IBM's calibration occurred relative to experiments? No comparison to Floquet, Snake optimizer, or NAPA. Largest circuit tested (qram_n20) uses only 20 of 127 qubits. No 50+ qubit experiments.

**Q4: What the Authors Didn't Tell You**

The fatal flaw is temporal stability. Section 5.5 reveals: "Eight days later, five out of eight qubit pairs experience changes in the optimal pulse waveform." That is a **62.5% invalidation rate** within 8 days. No automated drift detection mechanism is proposed, and the amortized cost of weekly reprofiling is never quantified.

Other skeletons:

1. **The "0.015 MHz threshold" is soft:** 20× relaxation to 0.3 MHz is applied when pairs fail. Headline numbers assume the tight threshold.

2. **IBM's software ate the speedup:** Theoretical 25× → achieved 7.9× due to IBM software limits. External constraint dominating real-world performance.

3. **The QEC claim is hollow:** Median gate error (4.4×10⁻³) is still above the surface code threshold (3×10⁻³). Only ~20% of pairs reach below threshold. Only distance-3 QEC is realizable on this topology — which provides essentially no error suppression. They never run a single QEC cycle.

4. **No comparison to competing methods:** Floquet calibration, Snake optimizer, and NAPA are cited but never benchmarked against.

5. **Application benchmarks are underwhelming:** qram_n20 achieves 32% fidelity after calibration. The largest improvement is for circuits already at 94% fidelity.

---

## Score Sheet

Please score both analyses on each dimension (1–5), provide your overall preference, and justify in 3–5 sentences.

| Dimension | Analysis A (1–5) | Analysis B (1–5) |
|-----------|:----------------:|:----------------:|
| 1. Mechanistic Accuracy | | |
| 2. Insight Depth | | |
| 3. Critical Rigor | | |
| 4. Breadth of Perspective | | |
| 5. Calibration | | |
| 6. Usefulness | | |

**Overall preference:** A clearly / A somewhat / Tie / B somewhat / B clearly

**Justification:**

What drove your preference? Which dimensions mattered most? Was there a specific moment in one analysis that stood out — an insight the other missed, an error that undermined trust, a connection that changed your understanding?
