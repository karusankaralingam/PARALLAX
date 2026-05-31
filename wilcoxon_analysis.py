"""
Paired Wilcoxon signed-rank test on PARALLAX rubric evaluations.
Tests whether LLM analysis (B/study_C_CONSOLIDATED) scores significantly higher than human analysis (A).

Data: per-directory scores across 5 dimensions for Paper 1 and Paper 2.
Excludes: rnjain (opted out). All 10 remaining directories complete for both papers.
"""

from scipy.stats import wilcoxon
import statistics

DIMENSIONS = [
    "Mechanistic Accuracy",
    "Insight Depth",
    "Critical Rigor",
    "Calibration",
    "Usefulness",
]

# fmt: off
# Structure: { evaluator: [[A scores], [B scores]] }  — 5 values each, one per dimension
paper1 = {
    "amittal26":    ([4, 5, 4, 5, 4], [5, 5, 5, 4, 5]),
    "ardubal":      ([3, 3, 4, 3, 3], [5, 5, 5, 5, 5]),
    "chithra":      ([3, 3, 3, 3, 2], [5, 5, 5, 5, 5]),
    "ian":          ([3, 3, 3, 4, 3], [5, 5, 5, 3, 5]),
    "kannakaranko": ([4, 4, 5, 4, 4], [5, 5, 5, 4, 5]),
    "naggarwal28":  ([5, 5, 4, 4, 5], [5, 5, 5, 5, 5]),
    "noah":         ([4, 5, 4, 5, 4], [5, 3, 5, 5, 5]),
    "selagamsetty": ([3, 3, 3, 4, 2], [4, 4, 4, 4, 4]),
    "vramadas":     ([5, 4, 4, 5, 4], [3, 5, 5, 5, 3]),
    "weichu":       ([4, 4, 4, 5, 4], [5, 5, 5, 4, 5]),
}

paper2 = {
    "amittal26":    ([3, 3, 2, 2, 3], [5, 5, 5, 5, 5]),
    "ardubal":      ([4, 3, 3, 4, 4], [3, 4, 4, 4, 3]),
    "chithra":      ([5, 4, 3, 5, 4], [5, 5, 4, 5, 5]),
    "ian":          ([5, 5, 4, 5, 5], [4, 5, 5, 4, 5]),
    "kannakaranko": ([3, 2, 4, 4, 4], [5, 5, 5, 3, 4]),
    "naggarwal28":  ([4, 4, 4, 5, 4], [5, 5, 5, 4, 5]),
    "noah":         ([4, 5, 4, 5, 3], [5, 5, 5, 5, 5]),
    "selagamsetty": ([4, 3, 3, 5, 4], [5, 5, 5, 5, 5]),
    "vramadas":     ([3, 4, 4, 4, 4], [4, 3, 3, 4, 3]),
    "weichu":       ([3, 4, 4, 4, 3], [4, 5, 5, 5, 5]),
}
# fmt: on


def analyze(paper_data, label):
    evaluators = list(paper_data.keys())
    n = len(evaluators)

    # Per-evaluator total scores
    totals_a = [sum(paper_data[e][0]) for e in evaluators]
    totals_b = [sum(paper_data[e][1]) for e in evaluators]
    deltas = [b - a for a, b in zip(totals_a, totals_b)]

    print(f"\n{'='*60}")
    print(f"  {label}  (n={n} evaluators)")
    print(f"{'='*60}")

    print(f"\n{'Evaluator':<16} {'A total':>8} {'B total':>8} {'Delta (B-A)':>12}")
    print("-" * 46)
    for e, a, b, d in zip(evaluators, totals_a, totals_b, deltas):
        print(f"{e:<16} {a:>8} {b:>8} {d:>+12}")
    print("-" * 46)
    print(f"{'Mean':<16} {statistics.mean(totals_a):>8.2f} {statistics.mean(totals_b):>8.2f} {statistics.mean(deltas):>+12.2f}")

    # Wilcoxon on totals
    stat, p = wilcoxon(totals_b, totals_a, alternative="greater")
    print(f"\n  Wilcoxon signed-rank (total score, B > A):")
    print(f"    statistic = {stat:.1f},  p = {p:.4f}  {'***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else '(n.s.)'}")

    # Per-dimension breakdown
    print(f"\n  Per-dimension averages and Wilcoxon:\n")
    print(f"  {'Dimension':<24} {'Avg A':>6} {'Avg B':>6} {'Delta':>6}  p-value")
    print(f"  {'-'*58}")
    for i, dim in enumerate(DIMENSIONS):
        scores_a = [paper_data[e][0][i] for e in evaluators]
        scores_b = [paper_data[e][1][i] for e in evaluators]
        dim_deltas = [b - a for a, b in zip(scores_a, scores_b)]
        avg_a = statistics.mean(scores_a)
        avg_b = statistics.mean(scores_b)
        try:
            _, dp = wilcoxon(scores_b, scores_a, alternative="greater")
            sig = "***" if dp < 0.001 else "**" if dp < 0.01 else "*" if dp < 0.05 else "n.s."
            p_str = f"{dp:.4f} {sig}"
        except ValueError:
            p_str = "  n/a (no variation)"
        print(f"  {dim:<24} {avg_a:>6.2f} {avg_b:>6.2f} {avg_b - avg_a:>+6.2f}   {p_str}")


analyze(paper1, "PAPER 1 — Rethinking Prefetching")
analyze(paper2, "PAPER 2 — Precise Exceptions in Relaxed Architectures")

print("\n\nNote: p-values are one-sided (B > A). * p<0.05  ** p<0.01  *** p<0.001")
