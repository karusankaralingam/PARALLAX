# PARALLAX Ablation — Findings on the Full Gauntlet Corpus (98 papers)

## What this is

This is a large-scale replication of the PARALLAX prompting ablation (Studies A, B, C)
run over the **entire gauntlet paper corpus** — 18 HPCA-2026 papers and 80 ISCA-2025
papers, 98 in total — rather than the original 11-student × 2-paper sample (22 points)
analyzed in [ANALYSIS.md](../ANALYSIS.md).

- **Reviews generated** by `claude-opus-4-5` via the NVIDIA inference gateway
  (`scripts/ablation_openai.py`), 8000-token budget, answering Q1–Q4.
- **Studies** are unchanged: **A** = simple directive, **B** = rich computer-architect
  persona, **C** = five independent specialist personas + a Claude synthesis pass.
- **Judge:** pairwise blind comparison by `gemini-3.1-pro-preview` (the live successor
  to the retired `gemini-3-pro-preview`), 3 runs per pair at temps 0.2/0.3/0.3 with
  randomized A/B slot assignment, scored on the same 6 dimensions.
- **Winner per paper** = sign of the 3-run mean score delta (|Δ| < 0.05 = Tie).

> Scope note: this corpus has no human reviews and no Gauntlet `CONSOLIDATED_REVIEW.md`
> (those were student-specific), so this report covers the **pairwise A/B/C comparison
> only** — not the human-vs-LLM or vs-Gauntlet axes from the original ANALYSIS.md.

---

## Headline

| Comparison | Cases | Winner | Win record | Mean \|Δ\| | Mean signed Δ |
|-----------|:-----:|:------:|:----------:|:--------:|:-------------:|
| A vs B | 98 | **B** | 87/98 (89%) | 0.66 | B +0.59 |
| A vs C | 98 | **C** | 97/98 (99%) | 1.04 | C +1.04 |
| B vs C | 98 | **C** | 94/98 (96%) | 0.62 | C +0.58 |

**Win breakdown**
- **A vs B** — A: 11, B: 87
- **A vs C** — A: 1, **C: 97**
- **B vs C** — B: 3, C: 94, Tie: 1

**Overall ranking: C > B > A**, holding across both venues and every margin — the same
ordering as the original study, but tighter and more decisive at 4.5× the sample size.

---

## The effects get *stronger* at scale

The single most important result: moving from 22 to 98 papers did not wash the effects
out — it sharpened them, especially the two comparisons that decide whether the Study C
pipeline is worth its extra cost.

| Comparison | Original (n=22) | This corpus (n=98) |
|-----------|:---------------:|:------------------:|
| A vs B | B 86%, Δ 0.46 | B 89%, Δ 0.66 |
| A vs C | C 91%, Δ 0.75 | **C 99%, Δ 1.04** |
| B vs C | C 73%, Δ 0.27 | **C 96%, Δ 0.62** |

- **A vs C is now nearly unanimous** (91% → 99%) and its margin is the largest in the
  dataset (Δ ≈ 1.0 of 5). The gap between the bare directive and the full pipeline is
  not a small-sample artifact.
- **B vs C is the headline change.** In the original study this was "real but noisy"
  (73%, Δ 0.27). At scale it becomes robust: **96%, Δ 0.62** — the win rate and the
  margin both roughly doubled. The synthesis pass, not just persona richness, is what
  separates C from B, and the larger corpus makes that separation unambiguous.

---

## Outliers

The conclusions are strong, but the tails are where the structure lives.

### A vs B — 11 papers where A "wins," but barely
Every one of the 11 A-over-B cases is a **narrow** margin (+0.10 to +0.60); five are the
minimum +0.10 (statistical ties in all but name). There is no paper where the simple
directive *decisively* beats the rich persona. The B-over-A wins, by contrast, run as
deep as −1.20 (PADE, Cassandra). So even the "losses" for B are soft, while its wins
are hard.

### The MemSOS anomaly — the new `amittal26/paper2`
**`hpca/…MemSOS — OS-Guided Selective Memory Mirroring`** is the only paper in the entire
corpus where Study A is competitive on *both* fronts: it is an A-win in A-vs-B (+0.40)
**and** the lone A-win in A-vs-C (+0.10). This is the large-corpus echo of the
`amittal26/paper2` (Precise Exceptions) anomaly in the original study. The likely
explanation is the same: MemSOS has a **narrow, mechanism-focused contribution** (OS-guided
selective mirroring) where a straightforward single-pass reading captures the idea
cleanly, and the multi-persona ensemble's breadth adds noise rather than signal. When the
paper's "magic trick" is singular and self-contained, more voices don't help.

### A vs C — essentially total (97/98)
Apart from MemSOS, Study C beats Study A on every paper, with the strongest margins on
dense, multi-faceted systems papers: **zkSpeed** (−1.70), **ArtMem** (−1.60),
**RAP** (−1.60). These are exactly the papers where multiple specialist lenses + synthesis
pay off — lots of independent angles to reconcile.

### B vs C — the 4 papers where B holds its ground
Three B-wins and one tie. The standout is **`infinimind — Learning-Optimized BCI`**
(B +1.20), the single largest B-over-C margin in the corpus, alongside **Focus** (+0.20),
**FlexNeRFer** (+0.40), and a tie on **ANSMET**. This matches the original study's
insight #4: on papers with a **narrow, well-defined technical contribution**
(a brain-computer-interface accelerator; a single-dataflow NeRF engine), Study B's
focused single-expert deep-dive can out-coher C's multi-voice synthesis, where persona
diversity introduces dilution instead of depth. C's edge is widest on *broad* papers and
narrowest-to-negative on *specialized* ones.

---

## Interpretation

1. **The synthesis pass is the differentiator, confirmed.** B and C draw on overlapping
   persona expertise; the thing C adds is explicit cross-reviewer reconciliation. The
   B-vs-C result (96%, Δ 0.62) isolates that step's value, and at scale it is clearly
   positive — substantially more so than the original sample suggested.

2. **The "more voices" advantage is contribution-shaped.** C dominates on broad,
   multi-mechanism papers and is weakest (occasionally net-negative) on narrow, single-trick
   papers. The MemSOS / infinimind / FlexNeRFer cases all point the same way: persona
   diversity is an asset proportional to how many genuinely distinct angles a paper admits.

3. **Prompt strategy matters, and it scales.** Unlike the original analysis — where the
   LLM-vs-human gap dwarfed the gaps between LLM approaches — here we can only speak to the
   inter-approach gaps, and those are real, consistent, and ordered C > B > A across 98
   papers and two venues.

4. **Implication for the Gauntlet pipeline** (unchanged from ANALYSIS.md, now better
   supported): the rule-based `CONSOLIDATED_REVIEW.md` extraction likely leaves value on
   the table relative to a Study-C-style synthesis pass. The 96% B<C result is the
   strongest evidence yet that the integration step — not the personas — is where the
   quality comes from.

---

## Provenance

- Reviews: `scripts/run_gauntlet_all.py --root all` → `ablation_openai.py`
  (`azure/anthropic/claude-opus-4-5`, NVIDIA gateway). 98/98 complete, 0 failures.
- Evals: `scripts/run_gauntlet_all.py --eval pairwise --root all`
  (`gemini-3.1-pro-preview`, 3 blind runs/pair). 98/98 complete.
- Aggregate table + per-paper detail: [ABLATION_SUMMARY.md](ABLATION_SUMMARY.md)
  (`scripts/summarize_gauntlet_ablations.py`).
- Each paper's raw reviews and per-run eval transcripts live in its
  `gauntlet-reviews/` directory (`study_{A,B,C}*.md`, `eval_{AvB,AvC,BvC}.md`).
