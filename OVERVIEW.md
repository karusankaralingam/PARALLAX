# PARALLAX — Project Overview

## What Is This?

PARALLAX is a study of LLM-based paper review quality, conducted in the context of a graduate computer architecture course. The core question: **how good are LLM-generated paper reviews, and what prompting choices determine quality?**

The setup: students in the course submit written reviews of assigned research papers using a structured five-question template. In parallel, we generated machine reviews of the same papers using three different prompting strategies (Studies A, B, C) and an existing automated review pipeline (the Gauntlet). We then ran blind evaluations — using Gemini 3 Pro as a judge — to compare every combination: study vs. study, study vs. Gauntlet, and LLM vs. human.

Everything runs on real papers from ISCA 2025 and HPCA 2026. The evaluations are blind (both reviews presented as "Analysis A / Analysis B" with randomized assignment across 3 independent runs at varying temperatures) using a consistent 6-dimension rubric.

---

## The Review Template

All reviews — human, Gauntlet, and ablation studies — are structured around five questions:

1. **Whiteboard explanation** — Walk through what they built and how it works
2. **Key insight** — The "aha" — not what they did, but why it works
3. **Evaluation critique** — Strongest and weakest aspect of the evaluation methodology
4. **What the authors didn't tell you** — Hidden assumptions, missing comparisons, unstated limitations
5. **Cross-domain connections** — Links to ideas outside this paper's scope

Studies A, B, and C answer Q1–Q4. The Gauntlet answers all five.

---

## The Three Ablation Studies

All three studies use **Claude claude-opus-4-5** with an 8000-token budget. The ablation varies only the prompting strategy, not the model.

**Study A — Simple Directive**  
System prompt: "You are a careful reader of computer architecture research papers." No persona, no explicit framing. The minimal baseline.

**Study B — Rich Persona**  
Same model. System prompt: a detailed persona — computer architect with deep expertise, explicit instruction to be technically rigorous, specific, and skeptical. No structural change — just a better-crafted persona.

**Study C — Multi-Persona Synthesis**  
Five specialist reviewers in sequence, each reading the full PDF independently:
- Dr. Microarch (microarchitecture expert)
- Prof. Workloads (workload characterization)
- Prof. Simtools (simulation methodology)
- Domain Expert 1 — topic-matched via Gemini Flash against a 93-topic taxonomy
- Domain Expert 2 — second topic-matched expert, persona generated dynamically

All five responses are fed to a Claude synthesis pass that merges consensus, flags disagreements, and produces a consolidated review.

---

## The Gauntlet Pipeline

An existing automated review system used in the course. Generates six deep expert reviews using fixed personas (Dr. Microarch, Prof. Workloads, Prof. Simtools, Chief Architect, and two topic-matched specialists) plus a SYNTHESIS.md.

The **CONSOLIDATED_REVIEW.md** is assembled by `consolidate.py` using rule-based section extraction:
- Q1 ← `dr_microarch_reader_review.md`
- Q2 ← `SYNTHESIS.md` section 3
- Q3 ← `prof_workloads_reader_review.md`
- Q4 ← `SYNTHESIS.md` section 4

No LLM synthesis pass — sections are extracted and concatenated by rule.

---

## Evaluation Methodology

**Evaluator:** Gemini 3 Pro (gemini-3-pro-preview)  
**Protocol:** Blind pairwise comparison. Both reviews presented as "Analysis A" and "Analysis B" with randomized assignment. Three independent runs at temperature 0.2, 0.3, 0.3.  
**Scoring rubric — 6 dimensions, 1–5 scale:**
1. Mechanistic Accuracy
2. Insight Depth
3. Critical Rigor
4. Breadth of Perspective
5. Calibration
6. Usefulness

**Dataset:** 11 students × 2 papers = 22 ablation data points. Human review comparisons: 8 students, paper 1 only.

---

## Results Summary

### Implied Ranking

```
Study C           5.0 / 5.0  (+0.63 above Gauntlet on average)
Gauntlet          ~4.3–4.7   (varies by paper; rule-based extraction)
Study B           ~4.4–4.9   (roughly peer to Gauntlet on most papers)
Study A           ~4.4–4.7   (consistently above human)
Human             2.4–3.5    (varies with domain familiarity)
```

### Key Quantitative Findings

| Comparison | Winner | Cases | Avg Delta |
|-----------|--------|:-----:|:---------:|
| A vs B (pairwise) | **B** | 19/22 | +0.46 |
| A vs C (pairwise) | **C** | 20/22 | +0.75 |
| B vs C (pairwise) | **C** | 16/22 | +0.27 |
| Human vs Gauntlet | **Gauntlet** | 8/8 | +1.6 |
| Study A vs Human | **Study A** | 9/9 | +1.5 |
| Study C vs Gauntlet | **Study C** | 15/17 | +0.63 |

The gap between any LLM approach and human reviews (~1.5–2.0 pts) is 2–3× larger than the gap between different LLM approaches (~0.3–0.75 pts).

For detailed pairwise results, student-by-student breakdowns, and qualitative analysis: see **[ANALYSIS.md](ANALYSIS.md)**.

---

## Three Deep Dives

To understand *why* the scores differ — not just that they do — we conducted paper-specific analyses for three papers chosen to represent different points in the design space:

### [XOR Cache (chithra/paper1)](deep_dive_xor_cache.md)
**"The paper where Study C did outstandingly" — Study C vs Gauntlet: +1.7 pts**

A complex coherence/compression paper with ~10 independent analytical threads. Demonstrates all three LLM advantage mechanisms simultaneously. Key finding: the Gauntlet's dr_microarch persona dismissed the "catalyzing compression" framing as "marketing language" and then produced a mechanistically incorrect Q1. The synthesis pass is what turns five reviews into something better than any single review — the critical differentiator between Study C and the Gauntlet.

### [Magellan Prefetcher (naggarwal28/paper1)](deep_dive_magellan.md)
**"The closest C vs Gauntlet result" — Study C vs Gauntlet: +0.2 pts**

A simpler, narrower paper in a mature subdomain. Confirms that multi-persona synthesis adds less value when the analytical surface area is small. New finding: Study A hallucinated a plausible-but-wrong quantitative claim about ROB size proportionality — suggesting the rich persona directive matters *more* on familiar-domain papers where the model is prone to overconfident extrapolation. Human review improves to 3.5/5.0 when the paper is in-domain.

### [Quantum Calibration (ardubal/paper1)](deep_dive_quantum_calibration.md)
**"Out-of-domain paper" — largest human gap in the dataset: −2.6 pts**

A quantum computing paper in a computer architecture course. Reveals a third axis of LLM advantage: *external knowledge injection* — information about the ecosystem (IBM API deprecation, FPGA waveform memory limits, alternative calibration methods) that the model brings from training data and a human student simply doesn't have. Human Breadth of Perspective: 1.3/5.0 (worst in dataset). Study C topic-matched experts effectively function as domain specialists for fields outside the course scope.

---

## Cross-Cutting Insights

**1. The LLM ceiling effect dominates:**  
The human-LLM gap is 2–3× the LLM-LLM gap. The choice of prompting strategy matters far less than the choice to use an LLM at all.

**2. Three distinct LLM advantage mechanisms:**  
- *Working memory* (XOR Cache): LLM holds all paper threads simultaneously during generation; humans lose earlier sections before writing Q4  
- *Domain-adjacent breadth* (Magellan): multi-persona structure deploys specialized perspectives on each subdomain  
- *External knowledge injection* (Quantum): topic-matched experts bring ecosystem knowledge not present in the paper

**3. The synthesis pass is the critical architecture choice:**  
Study C beats the Gauntlet despite both using similar persona sets. The difference is the synthesis step. The Gauntlet's rule-based extraction produces five correct-but-unintegrated sections. Study C's synthesis pass reconciles disagreements, catches reviewer errors, and produces a coherent cross-perspective analysis.

**4. The Gauntlet's persona calibration is its Achilles heel:**  
The cynical dr_microarch persona — "strip away the marketing language" — backfires on papers where the framing is accurate (XOR Cache's "catalyst" framing is the key mechanism, not marketing). When the persona misreads the paper's framing, downstream sections are contaminated.

**5. Domain familiarity, not paper complexity, determines the human gap:**  
Human performance improves significantly on simple in-domain papers (Magellan: 3.5/5.0) but stays low on out-of-domain papers regardless of complexity (Quantum: 2.4/5.0). LLMs don't have this domain boundary.

---

## Code and Scripts

All evaluation scripts, persona prompts, and orchestration code are in `scripts/`. See **[USAGE.md](USAGE.md)** for full documentation of each script, CLI interface, directory structure, and model choices.

Key scripts:
- `scripts/ablation.py` — Runs Studies A, B, C on a paper PDF
- `scripts/evaluate_ablation.py` — Blind pairwise and vs-Gauntlet evaluations
- `scripts/run_ablations.py` — Parallel orchestrator across all students and papers
- `scripts/evaluate.py` — Human vs Gauntlet evaluation
- `scripts/consolidate.py` — Assembles Gauntlet CONSOLIDATED_REVIEW

---

## Dataset

**Students:** amittal26, ardubal, chithra, ian, kannakaranko, naggarwal28, noah, rnjain, selagamsetty, vramadas, weichu (11 total)  
**Papers:** ISCA 2025 and HPCA 2026 proceedings; each student reviewed 2 papers  
**Human reviews:** Available for 8 students on paper 1; paper 2 reviews in progress  

Paper topics span: cache compression (XOR Cache), prefetching (Magellan, Prophet, POLO), memory processing units (MPU), quantum calibration, LLM serving (WindServe), GPU UVM, intermittent computing, FHE acceleration, ray tracing co-design.
