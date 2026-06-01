#!/usr/bin/env python3
"""
run_gauntlet_all.py -- Batch A/B/C ablation over the gauntlet-all paper corpus.

Layout:
    gauntlet-all/papers-hpca-2026/<paper-subdir>/<one>.pdf   (18 papers)
    gauntlet-all/papers-isca-2025/<paper-subdir>/<one>.pdf   (80 papers)

For each paper subdir, runs the ablation pipeline and writes results into
<paper-subdir>/gauntlet-reviews/  (study_A.md, study_B.md,
study_C_CONSOLIDATED.md, study_C_personas/).

Idempotent & resumable: a paper whose 3 study files all exist is skipped;
a partially-done paper re-runs only the missing studies. Safe to stop/restart.

Generation (default):
    python scripts/run_gauntlet_all.py --root hpca --workers 10
    python scripts/run_gauntlet_all.py --root isca
    python scripts/run_gauntlet_all.py --root all --dry-run

Pairwise evaluation (run later, after generation is spot-checked):
    python scripts/run_gauntlet_all.py --eval pairwise --root all

Common flags:
    --root hpca|isca|all     which corpus (default: all)
    --workers N              parallel papers (default: 10)
    --study A,B,C|all        which studies to (re)generate (default: all)
    --engine gateway|native  gateway=ablation_openai.py, native=ablation.py (default: gateway)
    --model / --topic-model  passthrough to the gateway engine
    --dry-run                list jobs and resolved paths, make no API calls
"""

import sys, os, argparse, subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows consoles default to cp1252; paper titles contain chars like 'μ'.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE           = Path(__file__).parent.parent
SCRIPTS_DIR    = Path(__file__).parent
ABLATION_PY    = SCRIPTS_DIR / 'ablation.py'           # native (Anthropic/Google SDK)
ABLATION_OAI_PY = SCRIPTS_DIR / 'ablation_openai.py'   # gateway (OpenAI SDK)

ROOTS = {
    'hpca': BASE / 'gauntlet-all' / 'papers-hpca-2026',
    'isca': BASE / 'gauntlet-all' / 'papers-isca-2025',
}

REVIEWS_DIRNAME = 'gauntlet-reviews'

STUDY_OUTPUTS = {
    'A': 'study_A.md',
    'B': 'study_B.md',
    'C': 'study_C_CONSOLIDATED.md',
}

PAIRWISE_OUTPUTS = ['eval_AvB.md', 'eval_AvC.md', 'eval_BvC.md']

DEFAULT_MODEL       = 'azure/anthropic/claude-opus-4-5'
DEFAULT_TOPIC_MODEL = 'gcp/google/gemini-2.5-pro'

# ---------------------------------------------------------------------------
# Discovery / idempotency
# ---------------------------------------------------------------------------

def selected_roots(root_arg: str) -> list[tuple[str, Path]]:
    keys = ['hpca', 'isca'] if root_arg == 'all' else [root_arg]
    out = []
    for k in keys:
        p = ROOTS[k]
        if not p.is_dir():
            print(f"  WARN: root {k} not found at {p}, skipping")
            continue
        out.append((k, p))
    return out


def find_pdf(subdir: Path) -> Path | None:
    pdfs = sorted(subdir.glob('*.pdf'))
    if len(pdfs) == 1:
        return pdfs[0]
    if len(pdfs) == 0:
        print(f"  WARN: no PDF in {subdir.name}/, skipping")
    else:
        print(f"  WARN: {len(pdfs)} PDFs in {subdir.name}/ (expected 1), skipping")
    return None


def missing_studies(reviews_dir: Path, requested: str) -> str:
    letters = ['A', 'B', 'C'] if requested == 'all' else [s.upper() for s in requested.split(',')]
    missing = [l for l in letters if not (reviews_dir / STUDY_OUTPUTS[l]).exists()]
    return ','.join(missing)


def iter_papers(roots: list[tuple[str, Path]]):
    """Yield (label, subdir, pdf, reviews_dir) for every paper subdir."""
    for root_key, root_path in roots:
        for subdir in sorted(p for p in root_path.iterdir() if p.is_dir()):
            pdf = find_pdf(subdir)
            if pdf is None:
                continue
            yield f'{root_key}/{subdir.name}', subdir, pdf, subdir / REVIEWS_DIRNAME

# ---------------------------------------------------------------------------
# Job builders
# ---------------------------------------------------------------------------

def build_gen_jobs(roots, study) -> list[dict]:
    jobs = []
    for label, subdir, pdf, reviews_dir in iter_papers(roots):
        needed = missing_studies(reviews_dir, study) if reviews_dir.exists() else (
            'A,B,C' if study == 'all' else ','.join(s.upper() for s in study.split(',')))
        if not needed:
            print(f"  SKIP {label}: all studies already complete")
            continue
        jobs.append({'label': label, 'pdf': pdf, 'reviews_dir': reviews_dir, 'study': needed})
    return jobs


def build_eval_jobs(roots) -> list[dict]:
    jobs = []
    for label, subdir, pdf, reviews_dir in iter_papers(roots):
        if not reviews_dir.is_dir():
            print(f"  SKIP {label}: no {REVIEWS_DIRNAME}/ yet")
            continue
        have_all_studies = all((reviews_dir / f).exists() for f in STUDY_OUTPUTS.values())
        if not have_all_studies:
            print(f"  SKIP {label}: not all A/B/C present, cannot pairwise-eval")
            continue
        if all((reviews_dir / f).exists() for f in PAIRWISE_OUTPUTS):
            print(f"  SKIP {label}: all pairwise evals already complete")
            continue
        jobs.append({'label': label, 'reviews_dir': reviews_dir})
    return jobs

# ---------------------------------------------------------------------------
# Job runners
# ---------------------------------------------------------------------------

def run_gen_job(job: dict, engine: str, model: str, topic_model: str) -> tuple[str, int, str]:
    script = ABLATION_OAI_PY if engine == 'gateway' else ABLATION_PY
    cmd = [sys.executable, str(script), str(job['pdf']), str(job['reviews_dir']),
           '--study', job['study']]
    if engine == 'gateway':
        cmd += ['--model', model, '--topic-model', topic_model]
    child_env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', env=child_env)
    output = result.stdout + (('\nSTDERR:\n' + result.stderr) if result.stderr.strip() else '')
    return job['label'], result.returncode, output


def run_eval_job(job: dict) -> tuple[str, int, str]:
    """In-process pairwise eval via evaluate_ablation's generic core."""
    import re, io, contextlib
    import evaluate_ablation as E
    reviews_dir = job['reviews_dir']
    rubric_dims = E.extract_rubric_dimensions(E.RUBRIC_PATH.read_text(encoding='utf-8'))
    # paper title from the study_A.md header
    paper_title = job['label']
    a_md = reviews_dir / STUDY_OUTPUTS['A']
    if a_md.exists():
        for line in a_md.read_text(encoding='utf-8').splitlines()[:6]:
            m = re.search(r'\*\*Paper:\*\*\s*(.+)', line)
            if m:
                paper_title = m.group(1).strip()
                break
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            E.run_pairwise(reviews_dir, ['A', 'B', 'C'], rubric_dims, paper_title)
        return job['label'], 0, buf.getvalue()
    except Exception as e:
        return job['label'], 1, buf.getvalue() + f"\nERROR: {e!r}"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description='Batch A/B/C ablation over gauntlet-all')
    p.add_argument('--root', default='all', choices=['hpca', 'isca', 'all'],
                   help='Which corpus (default: all)')
    p.add_argument('--workers', type=int, default=10, help='Parallel papers (default: 10)')
    p.add_argument('--study', default='all', help='Studies: A, B, C, or all (default: all)')
    p.add_argument('--engine', default='gateway', choices=['gateway', 'native'],
                   help='gateway=ablation_openai.py, native=ablation.py (default: gateway)')
    p.add_argument('--model', default=DEFAULT_MODEL, help=f'Gateway review model (default: {DEFAULT_MODEL})')
    p.add_argument('--topic-model', default=DEFAULT_TOPIC_MODEL,
                   help=f'Gateway topic/persona model (default: {DEFAULT_TOPIC_MODEL})')
    p.add_argument('--eval', default=None, choices=['pairwise'],
                   help='Run pairwise A/B/C eval instead of generation')
    p.add_argument('--dry-run', action='store_true', help='List jobs only, no API calls')
    args = p.parse_args()

    roots = selected_roots(args.root)
    if not roots:
        print("No valid roots."); return

    mode = 'eval-pairwise' if args.eval else 'generate'
    jobs = build_eval_jobs(roots) if args.eval else build_gen_jobs(roots, args.study)

    if not jobs:
        print("\nNo jobs to run (everything up to date)."); return

    print(f"\n{len(jobs)} jobs | mode={mode} | engine={args.engine} | workers={args.workers}")
    if mode == 'generate' and args.engine == 'gateway':
        print(f"model={args.model} | topic-model={args.topic_model}")
    for j in jobs:
        extra = f"  [study {j['study']}]" if 'study' in j else ''
        print(f"  - {j['label']}{extra}")

    if args.dry_run:
        print("\n[dry-run] no API calls made.")
        return

    print()
    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        if args.eval:
            futures = {pool.submit(run_eval_job, j): j['label'] for j in jobs}
        else:
            futures = {pool.submit(run_gen_job, j, args.engine, args.model, args.topic_model): j['label']
                       for j in jobs}
        for fut in as_completed(futures):
            label, rc, output = fut.result()
            status = 'OK' if rc == 0 else f'FAILED (rc={rc})'
            print(f"[{status}] {label}")
            if rc != 0:
                print(output)
            results[label] = rc

    ok     = [l for l, rc in results.items() if rc == 0]
    failed = [l for l, rc in results.items() if rc != 0]
    print(f"\n--- Summary ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ---")
    print(f"  OK:     {len(ok)}")
    print(f"  FAILED: {len(failed)}")
    for l in failed:
        print(f"    - {l}")
    if failed:
        print("\n  (Re-run the same command to retry only the failed/missing papers.)")


if __name__ == '__main__':
    main()
