# PARALLAX Evaluation Rubric

## Blind Comparison of Paper Analyses

---

### Instructions for Evaluators

You will receive two analyses of the same computer architecture paper, labeled **Analysis A** and **Analysis B**. The order is randomized. You do not know which analysis was produced by a human researcher and which by an automated system. **Do not attempt to guess the source — judge only the content.**

Before scoring, read the original paper (or at minimum its abstract and introduction) so you can assess accuracy.

For each of the six dimensions below, assign a score from 1 to 5 for **both** Analysis A and Analysis B independently. Then provide an overall preference and a brief justification.

Take your time. There are no right answers — we want your honest expert judgment.

---

### Dimension 1: Mechanistic Accuracy

*Does the analysis correctly describe what was built? Could someone implement the mechanism from this description alone?*

| Score | Criteria |
|-------|----------|
| 5 | Precise and complete. All key structures, policies, and datapath modifications are correctly described. No mischaracterizations. A reader unfamiliar with the paper could reconstruct the core mechanism from this description. |
| 4 | Mostly accurate with minor omissions. The core mechanism is correct but some secondary details are missing or slightly imprecise. |
| 3 | Generally correct but incomplete. The high-level idea is right but important implementation details are missing or vague. A reader would need to consult the paper to understand the mechanism. |
| 2 | Partially correct with significant errors. Some aspects of the mechanism are described correctly but others are wrong or seriously mischaracterized. |
| 1 | Fundamentally wrong or superficial. Major misunderstandings of what was built, or merely restates the abstract without explaining the mechanism. |

---

### Dimension 2: Insight Depth

*Does the analysis identify the core insight that makes the mechanism work — as distinct from merely describing it?*

The "insight" is the non-obvious reason *why* this approach works: the key observation, the structural property being exploited, the asymmetry in the problem that the mechanism leverages. A good analysis separates this from the description of *what* was built.

| Score | Criteria |
|-------|----------|
| 5 | Identifies a core insight that is non-obvious, correctly stated, and distinct from the mechanism description. Changes how you think about the problem. |
| 4 | Identifies a meaningful insight but it is either partially obvious from the paper's own framing or could be stated more precisely. |
| 3 | States something that is correct and relevant but does not go beyond what the paper itself explicitly claims. Restates the authors' motivation rather than distilling a deeper principle. |
| 2 | Attempts to state an insight but it is either trivial, vague, or not clearly distinct from the mechanism description. |
| 1 | No insight identified. The analysis describes what was built but never addresses why it works. |

---

### Dimension 3: Critical Rigor

*Does the analysis identify genuine weaknesses in methodology, evaluation, or assumptions — not just surface-level complaints?*

Strong critique is specific: it names the exact weakness, explains why it matters, and ideally suggests what evidence would resolve the concern. Weak critique is generic ("needs more benchmarks") or amounts to asking for a different paper.

| Score | Criteria |
|-------|----------|
| 5 | Identifies multiple specific, substantive weaknesses with clear reasoning for why each matters. Distinguishes between fundamental limitations and minor gaps. Critique is fair — acknowledges what the evaluation does well before identifying what it misses. |
| 4 | Identifies at least one significant weakness with good reasoning. May miss secondary issues but the primary critique is well-targeted. |
| 3 | Identifies real weaknesses but the reasoning is generic or incomplete. The reader is told *what* is weak but not fully convinced of *why it matters*. |
| 2 | Critique is superficial, generic, or mostly amounts to "they should have done more." No specificity about what is actually missing or why. |
| 1 | No meaningful critique, or critique that is factually wrong (e.g., complaining about something the paper actually addressed). |

---

### Dimension 4: Breadth of Perspective

*Does the analysis connect the work to ideas, techniques, or domains beyond the paper's own scope?*

This includes: identifying related techniques in other subfields, noting isomorphisms with problems in other domains, suggesting applications the authors didn't consider, or placing the work in a broader intellectual context.

| Score | Criteria |
|-------|----------|
| 5 | Makes surprising, valid connections to ideas outside the paper's scope that would genuinely enrich a reader's understanding. Connections are specific and technically grounded, not hand-wavy. |
| 4 | Makes at least one good cross-domain connection that goes beyond the paper's own related work section. |
| 3 | Mentions related work or adjacent fields but the connections are obvious or already noted by the authors. |
| 2 | Stays entirely within the paper's own scope. No attempt to contextualize beyond what the paper itself says. |
| 1 | No connections to external ideas. The analysis treats the paper as if it exists in isolation. |

---

### Dimension 5: Calibration

*Are the analysis's claims appropriately confident? Does it get the "size" of the contribution right?*

Well-calibrated analysis distinguishes between what it knows confidently and what it is speculating about. It neither oversells the paper's contribution nor dismisses it unfairly. It flags its own uncertainty when reasoning about areas outside its expertise.

| Score | Criteria |
|-------|----------|
| 5 | Claims are well-calibrated throughout. Confident where the evidence is strong, appropriately hedged where it is speculating. Correctly sizes the contribution — neither breathless nor dismissive. Flags its own limitations. |
| 4 | Mostly well-calibrated with occasional over- or under-confidence. Gets the overall contribution size approximately right. |
| 3 | Generally reasonable but with some systematic bias — either consistently too generous or too harsh. Does not distinguish between confident claims and speculation. |
| 2 | Noticeably miscalibrated. Either treats everything as a major breakthrough or dismisses valid contributions. Assertions are made without hedging even on uncertain points. |
| 1 | Severely miscalibrated. Fundamentally misreads the significance of the work, or is uniformly overconfident/dismissive in ways that would mislead a reader. |

---

### Dimension 6: Usefulness

*If you had 20 minutes before a meeting about this paper, would reading this analysis prepare you well?*

This is the integrative dimension. An analysis can score well on individual dimensions but still feel disjointed, poorly organized, or hard to extract value from. Conversely, an analysis might have gaps on specific dimensions but still be the one you'd want to read before a meeting.

| Score | Criteria |
|-------|----------|
| 5 | Reading this analysis would prepare you as well as or better than reading the paper itself under time pressure. You would walk into the meeting able to discuss the mechanism, its strengths and weaknesses, and its broader significance. |
| 4 | Good preparation. You would understand the core contribution and main limitations, though you might miss some nuances. |
| 3 | Adequate preparation. You would have a reasonable overview but might be caught off guard by pointed questions about methodology or mechanism details. |
| 2 | Weak preparation. You would have a surface-level understanding but would be exposed quickly in discussion. |
| 1 | Would mislead you. Reading this analysis would give you a wrong or seriously incomplete picture of the paper. |

---

### Overall Preference

After scoring all six dimensions for both analyses, provide:

**Overall preference (circle one):**

- **A is clearly better** — A is substantially stronger across multiple dimensions
- **A is somewhat better** — A has an edge but B is competitive
- **Tie** — Both are comparable in overall quality (use sparingly)
- **B is somewhat better** — B has an edge but A is competitive
- **B is clearly better** — B is substantially stronger across multiple dimensions

**Justification (2–4 sentences):**

What drove your preference? Which dimensions mattered most? Was there a specific moment in one analysis that stood out — an insight the other missed, an error that undermined trust, a connection that changed your understanding?

---

### Summary Score Sheet

**Paper title:** _______________________________________________

**Evaluator name:** ____________________________________________

**Date:** ______________________________________________________

| Dimension | Analysis A (1–5) | Analysis B (1–5) |
|-----------|:---:|:---:|
| 1. Mechanistic Accuracy | | |
| 2. Insight Depth | | |
| 3. Critical Rigor | | |
| 4. Breadth of Perspective | | |
| 5. Calibration | | |
| 6. Usefulness | | |

**Overall preference:** A clearly / A somewhat / Tie / B somewhat / B clearly

**Justification:**

\_______________________________________________________________

\_______________________________________________________________

\_______________________________________________________________

\_______________________________________________________________

---

### Evaluation Integrity

- Do **not** discuss your scores with other evaluators until all evaluations for your assigned paper are submitted.
- If you believe you can identify which analysis is human vs. automated, note this in your justification — but score based on content quality, not source.
- If you are familiar with the paper being evaluated, that is a feature, not a conflict — your domain knowledge makes you a better judge. But score the analyses on what they contain, not on what you independently know about the paper.
- Time estimate: 30–45 minutes per paper (including reading/skimming the original paper).