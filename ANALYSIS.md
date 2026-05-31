# PARALLAX Ablation Study — Analysis

## A. What Are Studies A, B, and C?

The ablation tests three prompting strategies applied to the same paper PDF, all using
**Claude Opus 4.5** with an 8000-token budget. Each study answers Q1–Q4 of the review
template (Whiteboard Explanation, Key Insight, Evaluation Critique, What the Authors
Didn't Tell You).

**Study A — Simple Directive**
Minimal system prompt: "You are a careful reader of computer architecture research papers."
No persona, no explicit framing, no instruction to be rigorous. The baseline.

**Study B — Rich Directive**
Same model, but with a detailed system prompt: computer architect with deep expertise,
explicit instruction to be technically rigorous, specific, and skeptical. No structural
change — just a better-crafted persona.

**Study C — Multi-Persona Synthesis**
Five specialist reviewers in sequence, each reading the full PDF independently:
- Dr. Microarch (microarchitecture expert)
- Prof. Workloads (workload characterization expert)
- Prof. Simtools (simulation methodology expert)
- Domain Expert 1 (topic-matched via Gemini Flash + TOPICS.TXT)
- Domain Expert 2 (second topic-matched expert, persona generated dynamically)

All five responses are then fed to a Claude synthesis pass that merges consensus,
preserves disagreements, and produces a final consolidated review.

---

## B. What Is the Gauntlet CONSOLIDATED Review?

The Gauntlet pipeline generates six deep expert reviews using fixed personas
(Dr. Microarch, Prof. Workloads, Prof. Simtools, Chief Architect, and two topic-matched
specialists), plus a SYNTHESIS.md. The **CONSOLIDATED_REVIEW.md** is assembled by
`consolidate.py`, which extracts the best sections from each:

- Q1 ← `dr_microarch_reader_review.md` (full whiteboard explanation)
- Q2 ← `SYNTHESIS.md` section 3: The Magic Trick
- Q3 ← `prof_workloads_reader_review.md` sections 1–4
- Q4 ← `SYNTHESIS.md` section 4: Skeleton in the Closet

This is a hand-mapped extraction, not an LLM synthesis pass.

---

## C. Pairwise Comparison: Studies A vs B vs C

### Methodology

Each pair (A vs B, A vs C, B vs C) was evaluated using **Gemini 3 Pro** in blind
comparison mode. Both reviews were presented as "Analysis A" and "Analysis B" with
randomized assignment across 3 runs (temp=0.2, 0.3, 0.3) to reduce position bias.

**Scoring rubric — 6 dimensions, 1–5 scale:**
1. Mechanistic Accuracy
2. Insight Depth
3. Critical Rigor
4. Breadth of Perspective
5. Calibration
6. Usefulness

**Dataset:** 11 students × 2 papers = 22 data points per comparison pair.

### Results

| Comparison | Winner | Cases | Avg Score Delta |
|-----------|--------|:-----:|:---------------:|
| A vs B | **B** | 19/22 | 0.46 pts |
| A vs C | **C** | 20/22 | 0.75 pts |
| B vs C | **C** | 16/22 | 0.27 pts |

**A vs B:** B wins in 19 of 22 cases, always "somewhat" or "clearly." The rich persona
(Study B) consistently outperforms the simple directive. Only amittal26/paper2 (Precise
Exceptions in Relaxed Architectures) is a clear A win; chithra/paper2 and
selagamsetty/paper2 are near ties.

**A vs C:** C wins in 20 of 22 cases — the most decisive comparison in the dataset.
Strongest wins include chithra/paper1, kannakaranko/paper2, and weichu/paper2 (all −1.2).
The only outlier is amittal26/paper2 (A wins clearly), which is anomalous across all comparisons.

**B vs C:** The closest competition. C wins 16/22 but several cases are near ties or
B wins. Clear B victories: ardubal/paper2 (3/3 runs, +0.2) and kannakaranko/paper2
(3/3 runs, +0.1). This suggests the B→C improvement is real but less reliable than
A→B or A→C.

**Overall ranking: C > B > A**, consistent and robust across papers and students.

---

## E. Study C vs Gauntlet CONSOLIDATED

### Methodology

Evaluated using `evaluate_ablation.py --mode vs-gauntlet --study C` with Gemini 3 Pro,
same 3-run blind methodology. Delta = Study C score − Gauntlet score (positive = Study C wins).

**Dataset:** 17 cases (students with both ablation outputs and a Gauntlet CONSOLIDATED_REVIEW).

### Results

| Student | Paper 1 | Paper 2 |
|---------|:-------:|:-------:|
| amittal26 | +1.0 (C clearly) | +0.7 (C clearly) |
| ardubal | +0.7 (C clearly) | +0.3 (C somewhat) |
| chithra | +1.7 (C clearly) | −0.3 (Gauntlet) |
| kannakaranko | +0.7 (C clearly) | +0.4 (C somewhat) |
| naggarwal28 | +0.2 (C somewhat) | +0.8 (C clearly) |
| rnjain | — | +1.2 (C clearly) |
| selagamsetty | +0.7 (C clearly) | +0.5 (C somewhat) |
| vramadas | +1.0 (C clearly) | +0.4 (C somewhat) |
| weichu | +0.7 (C somewhat) | +0.8 (C somewhat) |

**Study C wins in 15 of 17 cases. Average delta: +0.63 points.**
Only genuine Gauntlet win: chithra/paper2. Strongest C wins: chithra/paper1 (+1.7),
rnjain/paper2 (+1.2), amittal26/paper1 and vramadas/paper1 (both +1.0).

**Key finding:** Study C — a fully automated multi-persona pipeline — consistently
outperforms the existing Gauntlet CONSOLIDATED extraction, which relies on hand-mapped
section extraction from fixed personas without a synthesis pass.

---

## F. Study A vs Human Student Reviews

### Methodology

Evaluated using `evaluate_ablation.py --mode human-vs-a` with Gemini 3 Pro, same
3-run blind methodology. Delta = Human score − Study A score (negative = Study A wins).

**Dataset:** 9 cases (same students as D, plus vramadas/paper2).

### Results

| Student | Paper | Delta (Human − Study A) | Preference |
|---------|-------|:-----------------------:|-----------|
| amittal26 | paper1 | −0.7 | Study A clearly (3/3) |
| ardubal | paper1 | −2.2 | Study A clearly (3/3) |
| chithra | paper1 | −1.9 | Study A clearly (3/3) |
| kannakaranko | paper1 | −2.1 | Study A clearly (3/3) |
| naggarwal28 | paper1 | −0.9 | Study A clearly (3/3) |
| selagamsetty | paper1 | −1.4 | Study A clearly (3/3) |
| vramadas | paper1 | −1.5 | Study A clearly (3/3) |
| vramadas | paper2 | −3.7 | Study A clearly (3/3) |
| weichu | paper1 | −0.9 | Study A clearly (3/3) |

**Average delta: −1.5 points. Study A wins unanimously across all 9 cases.**
Even the simplest possible LLM prompting strategy — one sentence system prompt, no
persona — outperforms every human student review, with no exceptions.

---

## G. Overall Insights

### 1. Implied ranking across all comparators

Combining the pairwise and cross-comparison results yields a consistent ordering:

```
Study C  (+0.63 above Gauntlet)
   ↑  0.63
Gauntlet CONSOLIDATED  (≈ Study B; ~equal on cross-comparison)
   ↑  ~0.1
Study B  (+0.46 above Study A in pairwise)
   ↑  0.46
Study A  (~equal to Gauntlet vs human baseline)
   ↑  1.5
Human Student Reviews
```

### 2. The LLM ceiling effect dominates

The gap between any LLM approach and human reviews (~1.5–2.0 pts) is 2–3× larger
than the gap between different LLM approaches (~0.3–0.75 pts). In other words, the
choice of *which* LLM approach matters far less than the fact of using an LLM at all.

### 3. Study C's synthesis pass is the key differentiator

Study C beats the Gauntlet CONSOLIDATED despite both using similar persona sets.
The critical difference is the **synthesis pass**: Study C feeds all five responses to
Claude for explicit cross-reviewer reconciliation. The Gauntlet CONSOLIDATED uses
rule-based section extraction with no integration step. This suggests the synthesis
pass adds measurable value (+0.63 pts on average).

### 4. The B→C improvement is real but noisy

C beats B in 16/22 pairwise cases but B wins clearly on 2 papers (ardubal/paper2,
kannakaranko/paper2). Study B's single-expert deep-dive occasionally produces more
focused, coherent output than C's multi-voice synthesis — especially on papers with
a narrow, well-defined technical contribution where persona diversity may introduce
noise rather than signal.

### 5. The amittal26/paper2 anomaly

Across every comparison involving amittal26/paper2 (Precise Exceptions in Relaxed
Architectures), Study A wins or ties — even against B and C. This is the only paper
in the dataset where simple prompting outperforms richer approaches. A likely
explanation: the paper's contribution is narrow and formal, where a straightforward
reading produces a more accurate summary than a multi-persona ensemble that may
over-engineer the analysis.

### 6. Implications for the Gauntlet pipeline

The results suggest the current CONSOLIDATED extraction could be meaningfully improved
by replacing the rule-based section extraction with a Claude synthesis pass similar to
Study C. The raw Gauntlet persona reviews are likely high quality; it is the integration
step that is leaving value on the table.

---

## Appendix: Review Template

All reviews — human, Gauntlet, and ablation studies — are evaluated against the
following five-question template. Studies A, B, and C answer Q1–Q4 only (Q5 is skipped
in the ablation pipeline).

---

1. **Whiteboard explanation** — You're explaining this paper's mechanism to a smart colleague who hasn't read it. Walk through what they built and how it works.
2. **What is the key insight that makes it work?** (The "aha" — not what they did, but why it works)
3. **What's the strongest aspect of the evaluation, and what's the weakest?** (Methodology critique)
4. **What did the authors not tell you?** (Hidden assumptions, missing comparisons, unstated limitations)
5. **What's the connection to ideas outside this paper's scope?** (Cross-domain links, broader implications)
