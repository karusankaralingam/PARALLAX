# Human-Preferred / Tie Cases — Deep Dive Analysis

This directory contains systematic analyses of the 5 papers in the PARALLAX dataset where the human review was preferred or tied with the LLM review (study_C_CONSOLIDATED). All other 15 cases had clear or somewhat LLM preference.

---

## Cases

| File | Paper | Verdict | Scores (A / B) | Primary Cause |
|------|-------|---------|:--------------:|---------------|
| [kannakaranko-paper2-deepdive.md](kannakaranko-paper2-deepdive.md) | MagiCache | **A clearly** | 17 / 22 | LLM factual overclaim: asserts all cache reads pay 60% timing penalty; only computing rows do |
| [vramadas-paper1-deepdive.md](vramadas-paper1-deepdive.md) | Prophet (temporal prefetching) | **A somewhat** | 22 / 21 | Human: precise 3-policy mechanism description; unique 56.3% power calculation; LLM: better critique but high-level on mechanism |
| [ian-paper2-deepdive.md](ian-paper2-deepdive.md) | LLBP-X (branch predictor) | **A somewhat** | 24 / 23 | Human: self-contained explanation without unexplained jargon; LLM: figure-dependent description requires paper co-reference |
| [vramadas-paper2-deepdive.md](vramadas-paper2-deepdive.md) | LightML (photonic accelerator) | **A somewhat** | 19 / 17 | LLM: comprehensive but poor prioritization, trivial weaknesses alongside critical ones; Human: focused depth-over-breadth |
| [ardubal-paper2-deepdive.md](ardubal-paper2-deepdive.md) | Qtenon (quantum-classical) | **Tie** | 18 / 18 | Human: covers all 7 contributions (hardware + software); LLM: better critical rigor, identifies outdated baseline |

---

## Cross-Case Patterns

### 1. Factual overclaim as a trust-breaker (kannakaranko/paper2)
One specific wrong assertive statement can flip an overall preference even when aggregate scores favor the LLM. Domain-expert evaluators notice these errors; non-expert evaluators might not.

### 2. Mechanism accessibility vs. comprehensiveness tradeoff (ian/paper2, vramadas/paper2)
LLM reviews tend to be comprehensive but require co-reference with paper figures. Human reviews that produce self-contained explanations are preferred for "20 minutes before a meeting" usefulness, even when less complete.

### 3. Prioritization matters more than coverage (vramadas/paper2)
For a paper with many possible concerns, an analysis that names the right concerns wins over one that names more concerns. The LLM generates valid but untriaged observations; the human selects the load-bearing ones.

### 4. Software contributions get underweighted in LLM reviews (ardubal/paper2)
When a paper has both hardware and software contributions, LLM reviews tend to analyze hardware mechanisms in depth and elide the software components. This creates a coverage gap for system papers with significant ISA or compiler contributions.

### 5. Human energy calculations as domain-specific arithmetic (vramadas/paper1)
The human reviewer computed 56.3% power increase from 1.6% energy increase at 35% speedup — a check not present in the LLM review. Domain-expert arithmetic that catches misleading framing in the paper is a signal of genuine engagement that the LLM's more formulaic analysis pattern misses.
