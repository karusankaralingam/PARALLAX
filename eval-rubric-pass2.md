# PARALLAX Evaluation Rubric — Pass 2: Factual Accuracy Audit

## Purpose

This pass answers a question that blind judges cannot: **what did the Gauntlet synthesis get factually wrong?**

You are the original analyst for this paper — you read it deeply and in your area of expertise. Your job here is not to re-evaluate the richness or usefulness of the Gauntlet synthesis (that was Pass 1). Your job is to read the Gauntlet synthesis critically and flag specific factual errors.

This is not a general quality assessment. Do not comment on style, structure, or what you would have written differently. Flag only things that are **factually wrong or unsupported by the paper**.

---

## Instructions

Read the Gauntlet synthesis for your paper. For each factual error you find, create an annotation using the format below. Aim to be exhaustive — if you find ten errors, flag all ten. If you find none, say so explicitly.

You do not need to re-read the full paper before doing this. You wrote the human analysis recently and know the paper well. If you are uncertain whether something is an error, note the uncertainty in your annotation (see Confidence field below).

---

## Error Categories

Classify each error into one of three categories:

### Category A: Mechanism Error
The synthesis incorrectly describes what was built — wrong hardware structures, wrong policy, wrong datapath, wrong algorithm. This includes errors of omission where a critical component is missing from the description in a way that would mislead a reader about how the mechanism works.

**Examples:**
- "The synthesis says the prefetcher uses a bloom filter; the paper uses a saturating counter table."
- "The synthesis describes the coherence protocol as MESI; the paper uses a custom directory protocol."
- "The synthesis omits that the mechanism only applies to read-only data, which is the key constraint."

### Category B: Result Mischaracterization
The synthesis states a number, comparison, or performance claim incorrectly. This includes wrong speedup figures, mislabeled baselines, incorrect benchmark names, or overstated conclusions that go beyond what the paper's results actually show.

**Examples:**
- "The synthesis claims 3.2× speedup; the paper reports 2.1× on the geometric mean."
- "The synthesis says the comparison is against a store-and-forward baseline; the paper compares against a cut-through baseline."
- "The synthesis says the result holds across all workloads; the paper shows it degrades on memory-intensive benchmarks."

### Category C: Hallucinated Content
The synthesis makes a claim — about the mechanism, the evaluation, the authors' claims, or related work — that has no basis in the paper. The paper simply does not say this.

**Examples:**
- "The synthesis claims the authors evaluated on real silicon; the paper is simulation-only."
- "The synthesis attributes a specific idea to a cited prior work; the paper makes no such attribution."
- "The synthesis mentions a power analysis; the paper contains no power numbers."

---

## Severity Rating

For each flagged error, rate its severity:

| Rating | Meaning |
|--------|---------|
| **High** | Would materially mislead a reader about what the paper contributes or how the mechanism works. A researcher relying on this analysis might make wrong decisions about whether to build on the work. |
| **Medium** | Incorrect but unlikely to seriously mislead. A reader would have a slightly wrong picture but the core contribution is still communicated accurately. |
| **Low** | Minor inaccuracy — wrong number, slightly imprecise description — that a careful reader would notice but that does not affect understanding of the mechanism or its significance. |

---

## Annotation Format

For each error, provide:

```
## Error [N]

**Category:** A / B / C  
**Severity:** High / Medium / Low  
**Location in synthesis:** [Section name or quote the relevant sentence]  
**What the synthesis says:** [Exact quote or close paraphrase]  
**What the paper actually says:** [Correct information, with page/section reference if helpful]  
**Confidence:** Certain / Likely / Uncertain  
**Notes:** [Optional — use if the error is subtle or context-dependent]
```

Use `Confidence: Uncertain` if you are not fully sure this is an error — e.g., if the paper is ambiguous, if you are working from memory, or if the synthesis might be using a different framing that is technically defensible. Do not suppress uncertain cases — flag them and let us adjudicate.

---

## What NOT to Flag

- **Omissions that don't mislead.** The synthesis will not cover everything in the paper. Do not flag missing content unless the omission creates a false impression of what was built or claimed.
- **Matters of interpretation.** If the synthesis frames the contribution differently than you would, but the framing is defensible, do not flag it. Flag errors, not disagreements.
- **Stylistic or structural choices.** The synthesis has a distinctive format. Do not flag the format.
- **Things the synthesis appropriately marks as uncertain.** If the synthesis says "the paper does not make this explicit, but..." and you agree that is an accurate characterization, it is not an error.

---

## Summary Sheet

After completing your annotations, fill in the summary:

**Paper title:** _______________________________________________

**Analyst name:** ______________________________________________

**Date:** ______________________________________________________

**Total errors flagged:**

| Category | High | Medium | Low | Total |
|----------|:----:|:------:|:---:|:-----:|
| A: Mechanism Error | | | | |
| B: Result Mischaracterization | | | | |
| C: Hallucinated Content | | | | |
| **Total** | | | | |

**Overall accuracy assessment (circle one):**

- **No significant errors** — The synthesis is factually reliable. Minor inaccuracies only.
- **Minor errors** — A few inaccuracies, none high-severity. The synthesis gives an accurate overall picture.
- **Moderate errors** — At least one high-severity error, or several medium-severity errors. A reader relying solely on this analysis would have a partially wrong picture.
- **Significant errors** — Multiple high-severity errors. The synthesis would meaningfully mislead a reader about the mechanism or its significance.

**One-sentence summary of the most important error (if any):**

_______________________________________________________________

---

## Integrity Note

You wrote the human analysis for this paper. It is natural to read the Gauntlet synthesis with a critical eye. Try to flag errors because they are errors, not because the synthesis differs from your analysis. If the Gauntlet synthesis identifies something correct that your analysis missed, that is not an error — note it in the summary if you like, but do not flag it as a problem.

Time estimate: 20–30 minutes per paper.
