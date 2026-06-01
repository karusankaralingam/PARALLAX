#!/usr/bin/env python3
"""
summarize_gauntlet_ablations.py -- Aggregate the pairwise A/B/C eval results
across the whole gauntlet-all corpus into one summary table (mirrors ANALYSIS.md C).

Reads every <paper-subdir>/gauntlet-reviews/eval_{AvB,AvC,BvC}.md produced by
`run_gauntlet_all.py --eval pairwise`, parses each file's
"## Summary Across 3 Runs" block (the per-run preference bullets and the
"Overall mean" delta row), and writes gauntlet-all/ABLATION_SUMMARY.md.

Usage:
    python scripts/summarize_gauntlet_ablations.py --root all
    python scripts/summarize_gauntlet_ablations.py --root hpca
"""

import re, argparse
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
ROOTS = {
    'hpca': BASE / 'gauntlet-all' / 'papers-hpca-2026',
    'isca': BASE / 'gauntlet-all' / 'papers-isca-2025',
}
REVIEWS_DIRNAME = 'gauntlet-reviews'
OUT_PATH = BASE / 'gauntlet-all' / 'ABLATION_SUMMARY.md'

# comparison -> (eval filename, label1, label2). delta in the file is label1 - label2.
COMPARISONS = {
    'A vs B': ('eval_AvB.md', 'A', 'B'),
    'A vs C': ('eval_AvC.md', 'A', 'C'),
    'B vs C': ('eval_BvC.md', 'B', 'C'),
}

TIE_THRESHOLD = 0.05  # |mean delta| below this counts as a tie

MEAN_RE = re.compile(
    r'\|\s*\*\*Overall mean\*\*\s*\|\s*\*\*([\d.]+)\*\*\s*\|\s*\*\*([\d.]+)\*\*\s*\|\s*\*\*([+-]?[\d.]+)\*\*\s*\|')
PREF_RE = re.compile(r'-\s*Run\s*\d+\s*\(temp=[\d.]+\):\s*\*\*(.+?)\*\*')


def parse_eval(path: Path):
    """Return dict {mean_delta: float|None, prefs: [str,...]} or None if unparseable."""
    text = path.read_text(encoding='utf-8')
    m = MEAN_RE.search(text)
    mean_delta = float(m.group(3)) if m else None
    prefs = [p.strip() for p in PREF_RE.findall(text)]
    if mean_delta is None and not prefs:
        return None
    return {'mean_delta': mean_delta, 'prefs': prefs}


def winner_from_delta(delta: float | None, label1: str, label2: str) -> str:
    if delta is None:
        return '?'
    if delta > TIE_THRESHOLD:
        return label1
    if delta < -TIE_THRESHOLD:
        return label2
    return 'Tie'


def iter_papers(root_keys):
    for k in root_keys:
        root = ROOTS[k]
        if not root.is_dir():
            continue
        for subdir in sorted(p for p in root.iterdir() if p.is_dir()):
            rd = subdir / REVIEWS_DIRNAME
            if rd.is_dir():
                yield k, subdir.name, rd


def main():
    ap = argparse.ArgumentParser(description='Aggregate pairwise A/B/C eval results')
    ap.add_argument('--root', default='all', choices=['hpca', 'isca', 'all'])
    args = ap.parse_args()
    root_keys = ['hpca', 'isca'] if args.root == 'all' else [args.root]

    # per-paper rows + per-comparison aggregates
    rows = []  # (root, paper, {comp: (winner, delta_toward_label1)})
    agg = {comp: {'wins': {}, 'deltas': [], 'n': 0} for comp in COMPARISONS}

    papers = list(iter_papers(root_keys))
    for root_key, paper, rd in papers:
        row = {'root': root_key, 'paper': paper, 'cells': {}}
        for comp, (fname, l1, l2) in COMPARISONS.items():
            f = rd / fname
            if not f.exists():
                row['cells'][comp] = None
                continue
            parsed = parse_eval(f)
            if parsed is None:
                row['cells'][comp] = None
                continue
            delta = parsed['mean_delta']
            win = winner_from_delta(delta, l1, l2)
            row['cells'][comp] = (win, delta)
            agg[comp]['n'] += 1
            agg[comp]['wins'][win] = agg[comp]['wins'].get(win, 0) + 1
            if delta is not None:
                agg[comp]['deltas'].append(delta)
        rows.append(row)

    # ---- build markdown ----
    out = []
    out.append('# Gauntlet-All Ablation — Pairwise A/B/C Summary')
    out.append('')
    out.append(f'**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M")}  ')
    out.append(f'**Corpus:** {", ".join(root_keys)}  ')
    out.append(f'**Papers with a gauntlet-reviews/ dir:** {len(papers)}  ')
    out.append('Winner per paper = sign of the 3-run mean score delta '
               f'(|Δ| < {TIE_THRESHOLD} = Tie). Delta is reported toward the first label.')
    out.append('')
    out.append('## Headline')
    out.append('')
    out.append('| Comparison | Cases scored | Winner | Win record | Mean |Δ| |')
    out.append('|------------|:------------:|:------:|:----------:|:--------:|')
    for comp, (fname, l1, l2) in COMPARISONS.items():
        a = agg[comp]
        n = a['n']
        if n == 0:
            out.append(f'| {comp} | 0 | — | — | — |')
            continue
        # overall winner = label with most paper-wins (ignoring ties/?)
        ranked = sorted(((c, w) for c, w in a['wins'].items() if c in (l1, l2)),
                        key=lambda kv: -kv[1])
        winner = ranked[0][0] if ranked else '—'
        win_ct = a['wins'].get(winner, 0)
        mean_abs = (sum(abs(d) for d in a['deltas']) / len(a['deltas'])) if a['deltas'] else 0.0
        out.append(f'| {comp} | {n} | **{winner}** | {win_ct}/{n} | {mean_abs:.2f} |')
    out.append('')

    # per-comparison win breakdown (incl ties / unscored)
    out.append('### Win breakdown')
    out.append('')
    for comp in COMPARISONS:
        a = agg[comp]
        if a['n'] == 0:
            continue
        parts = ', '.join(f'{k}: {v}' for k, v in sorted(a['wins'].items()))
        out.append(f'- **{comp}** ({a["n"]} scored): {parts}')
    out.append('')

    # per-paper detail
    out.append('## Per-paper detail')
    out.append('')
    out.append('Each cell = winner (mean Δ toward first label).')
    out.append('')
    out.append('| Corpus | Paper | A vs B | A vs C | B vs C |')
    out.append('|--------|-------|:------:|:------:|:------:|')
    def cell(c):
        if c is None:
            return '—'
        win, delta = c
        return f'{win} ({delta:+.2f})' if delta is not None else win
    for r in sorted(rows, key=lambda r: (r['root'], r['paper'])):
        paper_disp = r['paper'] if len(r['paper']) <= 50 else r['paper'][:47] + '…'
        out.append(f"| {r['root']} | {paper_disp} | "
                   f"{cell(r['cells']['A vs B'])} | {cell(r['cells']['A vs C'])} | "
                   f"{cell(r['cells']['B vs C'])} |")
    out.append('')

    OUT_PATH.write_text('\n'.join(out), encoding='utf-8')
    scored = sum(1 for r in rows if any(r['cells'].values()))
    print(f"Wrote {OUT_PATH.relative_to(BASE)}  ({scored}/{len(papers)} papers with eval data)")


if __name__ == '__main__':
    main()
