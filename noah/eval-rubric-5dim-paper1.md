# PARALLAX Evaluation Rubric

## Blind Comparison of Paper Analyses

---

### Instructions for Evaluators

You will receive two analyses of the same computer architecture paper, labeled **Analysis A** and **Analysis B**. For the purpose of this rubric **Analysis A** is the human generated review by one of your peers. **Analysis B** is the LLM generated one.
 
Before scoring, read the original paper (or at minimum its abstract and introduction) so you can assess accuracy.

For each of the six dimensions below, assign a score from 1 to 5 for **both** Analysis A and Analysis B independently. Then provide an overall preference and a brief justification.

Take your time. There are no right answers — we want your honest expert judgment.

---

### Dimension 1: Mechanistic Accuracy

Analysis A, Human Generated: 4 - Mostly accurate with minor omissions. The core mechanism is correct but some secondary details are missing or slightly imprecise.

Analysis B, LLM Generated: 5 - Precise and complete. All key structures, policies, and datapath modifications are correctly described. No mischaracterizations. A reader unfamiliar with the paper could reconstruct the core mechanism from this description.

I think both review were accurate in their description of the paper, and described POLO in enough detail to glean the salient points of the design. The LLM review stressed reiterating key metrics from the article directly, which did well to concretely support their points. However, I felt like they were a bit extraneous, and did not help in determining "implementation" details where a reader could re-implement the design. The human review focused mostly on the evaluation of the design, which highlighted the thoroughness of the paper. Their analysis did correctly describe what was built, but not to the level of detail where the mechanism could be implemented from the description alone. For that reason, I ranked the LLM generated review higher for this dimension.

---

### Dimension 2: Insight Depth

Analysis A, Human Generated: 5 - Identifies a core insight that is non-obvious, correctly stated, and distinct from the mechanism description. Changes how you think about the problem.

Analysis B, LLM Generated: 3 - States something that is correct and relevant but does not go beyond what the paper itself explicitly claims. Restates the authors' motivation rather than distilling a deeper principle.

Much of the LLM generated response is simply parroted from the paper directly. As with the previous dimension, the LLM review overly relies on the quantitative metrics from the paper, and this style makes the response harder to parse. The insights the LLM review had were correct, but at a level of detail too fine to be useful. The human review was succinct, written directly, and clear in distinguishing the core insights from the mere descriptions present in the paper.

---

### Dimension 3: Critical Rigor

Analysis A, Human Generated: 4 - Identifies at least one significant weakness with good reasoning. May miss secondary issues but the primary critique is well-targeted.

Analysis B, LLM Generated: 5 - Identifies multiple specific, substantive weaknesses with clear reasoning for why each matters. Distinguishes between fundamental limitations and minor gaps. Critique is fair — acknowledges what the evaluation does well before identifying what it misses.

The weakness identified by the human review was in-depth and nuanced, but the description of the strength was a bit lackluster. The review highlights the comprehensiveness of the evaluation, which the LLM review also calls out, is the main strength of the paper. However, the context as to why those studies were needed, and their significance is missing. For those reasons, I ranked the LLM generated review higher. 

---

## Dimension 4 Calibration

Analysis A, Human Generated: 5 - Claims are well-calibrated throughout. Confident where the evidence is strong, appropriately hedged where it is speculating. Correctly sizes the contribution — neither breathless nor dismissive. Flags its own limitations.

Analysis B, LLM Generated: 5 - Claims are well-calibrated throughout. Confident where the evidence is strong, appropriately hedged where it is speculating. Correctly sizes the contribution — neither breathless nor dismissive. Flags its own limitations.

Both reviews do well to identify unstated limitations and hidden limitations. The LLM review is more numerous and more specific, but the human review provided a nuanced critique on the user study that the LLM review did not catch/identify. 

---

### Dimension 5 Usefulness

Analysis A, Human Generated: 4 - Good preparation. You would understand the core contribution and main limitations, though you might miss some nuances.

Analysis B, LLM Generated: 5 - Reading this analysis would prepare you as well as or better than reading the paper itself under time pressure. You would walk into the meeting able to discuss the mechanism, its strengths and weaknesses, and its broader significance.

To reiterate, I think the LLM review crowds their response with speciifc results or quantitative points, which ditracts from the overall readability of the review. However, the LLM response is more broad, covers more of the key features, and provides enough detail to make the response more useful. The human review reads a lot more smoothly, but it does feel like it's missing a level of detail where reading the review alone would provide enough information about the paper.

---

### Overall Preference

After scoring all five dimensions for both analyses, provide:

**Overall preference (circle one):**

- **B is somewhat better** — B has an edge but A is competitive

**Justification (2–4 sentences):**

To liken the responses to a computer architecture analogy, the human generated review is a lot like a domain specific accelerator. In the parts where the human response did well, it did exceedingly well, providing nuanced and targetted feedback that was well thought out and relevant. In contrast, the LLM review could be more likened to an optimized, general purpose CPU. It covers a broader spread of key points across all the dimensions, and provides reasonably high quality responses to understand the paper more thoroughly. While I prefer the writing style and presentation of the human review, the LLM review was more detailed.

---

### Summary Score Sheet

**Paper title:** Process Only Where You Look: Hardware and Algorithm Cooptimization for Efficient Gaze-Tracked Foveated Rendering in Virtual Reality

**Evaluator name:** Ranganath Selagamsetty

**Date:** May 17, 2026

| Dimension | Analysis A (1–5) | Analysis B (1–5) |
|-----------|:---:|:---:|
| 1. Mechanistic Accuracy | 4 | 5 |
| 2. Insight Depth | 5 | 3 |
| 3. Critical Rigor | 4 | 5 |
| 4. Calibration | 5 | 5 |
| 5. Usefulness | 4 | 5 |

**Overall preference:** B somewhat

**Justification:**

The LLM was more comprehensive, providing details that covered the full breadth of the work presented in the paper. The writing style of the LLM review, however, is not very reader friendly, over-emphasizing statistics and quantitative metrics as opposed to distilling the main points of the work.

---

### Evaluation Integrity

- Do **not** discuss your scores with other evaluators until all evaluations for your assigned paper are submitted.
- If you believe you can identify which analysis is human vs. automated, note this in your justification — but score based on content quality, not source.
- If you are familiar with the paper being evaluated, that is a feature, not a conflict — your domain knowledge makes you a better judge. But score the analyses on what they contain, not on what you independently know about the paper.
- Time estimate: 30–45 minutes per paper (including reading/skimming the original paper).