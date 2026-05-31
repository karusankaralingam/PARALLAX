#!/usr/bin/env python3
"""
run_ablations.py -- Run ablation.py or evaluate_ablation.py for all students x papers.

Discovers PDFs from <student>/pdf_1/ and <student>/pdf_2/, writes output to
<student>/ablation_study_1/ and <student>/ablation_study_2/ respectively.

Ablation mode (default):
    python scripts/run_ablations.py
    python scripts/run_ablations.py --study B
    python scripts/run_ablations.py --paper 1

Eval modes:
    python scripts/run_ablations.py --eval pairwise
    python scripts/run_ablations.py --eval vs-gauntlet
    python scripts/run_ablations.py --eval pairwise --paper 1 --students ardubal,chithra

Common flags:
    --students ardubal,chithra   subset of students
    --paper 1|2                  single paper (default: both)
    --workers N                  parallel workers (default: 3)
"""

import sys, argparse, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations

BASE           = Path(__file__).parent.parent
ABLATION_PY    = Path(__file__).parent / 'ablation.py'
EVAL_PY        = Path(__file__).parent / 'evaluate_ablation.py'

STUDENTS = [
    'amittal26', 'ardubal', 'chithra', 'ian', 'kannakaranko',
    'naggarwal28', 'noah', 'rnjain', 'selagamsetty', 'vramadas', 'weichu',
]

STUDY_OUTPUTS = {
    'A': 'study_A.md',
    'B': 'study_B.md',
    'C': 'study_C_CONSOLIDATED.md',
}

PAIRWISE_OUTPUTS = {
    ('A', 'B'): 'eval_AvB.md',
    ('A', 'C'): 'eval_AvC.md',
    ('B', 'C'): 'eval_BvC.md',
}

VS_GAUNTLET_OUTPUTS = {
    'A': 'eval_study_A.md',
    'B': 'eval_study_B.md',
    'C': 'eval_study_C.md',
}

def human_vs_output(letter: str) -> str:
    return f'eval_human_vs_{letter.upper()}.md'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_pdf(student_dir: Path, paper_num: int) -> Path | None:
    pdf_dir = student_dir / f'pdf_{paper_num}'
    if not pdf_dir.is_dir():
        return None
    pdfs = list(pdf_dir.glob('*.pdf'))
    return pdfs[0] if pdfs else None


def missing_studies(output_dir: Path, requested: str) -> str:
    """Studies whose output file doesn't exist yet."""
    letters = ['A', 'B', 'C'] if requested == 'all' else [s.upper() for s in requested.split(',')]
    missing = [l for l in letters if not (output_dir / STUDY_OUTPUTS[l]).exists()]
    return ','.join(missing)


def missing_pairwise(output_dir: Path) -> list[tuple[str, str]]:
    """Pairs whose eval output file doesn't exist yet."""
    return [pair for pair, fname in PAIRWISE_OUTPUTS.items()
            if not (output_dir / fname).exists()]


def missing_vs_gauntlet(output_dir: Path) -> list[str]:
    """Studies whose vs-gauntlet eval output doesn't exist yet."""
    return [l for l, fname in VS_GAUNTLET_OUTPUTS.items()
            if not (output_dir / fname).exists()]


def has_human_vs(output_dir: Path, letter: str) -> bool:
    return (output_dir / human_vs_output(letter)).exists()

# ---------------------------------------------------------------------------
# Job builders
# ---------------------------------------------------------------------------

def build_ablation_jobs(students, papers, study) -> list[dict]:
    jobs = []
    for student in students:
        student_dir = BASE / student
        if not student_dir.is_dir():
            print(f"  WARN: {student}/ not found, skipping")
            continue
        for paper_num in papers:
            pdf = find_pdf(student_dir, paper_num)
            if pdf is None:
                print(f"  WARN: {student}/pdf_{paper_num}/ has no PDF, skipping")
                continue
            output_dir = student_dir / f'ablation_study_{paper_num}'
            needed = missing_studies(output_dir, study)
            if not needed:
                print(f"  SKIP {student}/paper{paper_num}: all studies already complete")
                continue
            jobs.append({
                'label':      f'{student}/paper{paper_num}',
                'output_dir': output_dir,
                'pdf':        pdf,
                'study':      needed,
                'mode':       'ablation',
            })
    return jobs


def build_eval_jobs(students, papers, eval_mode) -> list[dict]:
    jobs = []
    for student in students:
        student_dir = BASE / student
        if not student_dir.is_dir():
            print(f"  WARN: {student}/ not found, skipping")
            continue
        for paper_num in papers:
            output_dir = student_dir / f'ablation_study_{paper_num}'
            if not output_dir.is_dir():
                print(f"  WARN: {student}/ablation_study_{paper_num}/ not found, skipping")
                continue

            if eval_mode == 'pairwise':
                pairs = missing_pairwise(output_dir)
                if not pairs:
                    print(f"  SKIP {student}/paper{paper_num}: all pairwise evals already complete")
                    continue
                jobs.append({
                    'label':      f'{student}/paper{paper_num} [pairwise]',
                    'student':    student,
                    'paper_num':  paper_num,
                    'output_dir': output_dir,
                    'mode':       'pairwise',
                    'studies':    ','.join(sorted({l for pair in pairs for l in pair})),
                })
            elif eval_mode.startswith('human-vs-'):
                letter = eval_mode.split('-')[-1].upper()
                if has_human_vs(output_dir, letter):
                    print(f"  SKIP {student}/paper{paper_num}: {human_vs_output(letter)} already exists")
                    continue
                jobs.append({
                    'label':      f'{student}/paper{paper_num} [{eval_mode}]',
                    'student':    student,
                    'paper_num':  paper_num,
                    'output_dir': output_dir,
                    'mode':       eval_mode,
                    'studies':    letter,
                })
            else:  # vs-gauntlet
                letters = missing_vs_gauntlet(output_dir)
                if not letters:
                    print(f"  SKIP {student}/paper{paper_num}: all vs-gauntlet evals already complete")
                    continue
                jobs.append({
                    'label':      f'{student}/paper{paper_num} [vs-gauntlet]',
                    'student':    student,
                    'paper_num':  paper_num,
                    'output_dir': output_dir,
                    'mode':       'vs-gauntlet',
                    'studies':    ','.join(letters),
                })
    return jobs

# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

def run_job(job: dict) -> tuple[str, int, str]:
    if job['mode'] == 'ablation':
        cmd = [
            sys.executable, str(ABLATION_PY),
            str(job['pdf']),
            str(job['output_dir']),
            '--study', job['study'],
        ]
    else:
        cmd = [
            sys.executable, str(EVAL_PY),
            job['student'],
            '--study-num', str(job['paper_num']),
            '--ablation-dir', str(job['output_dir']),
            '--mode', job['mode'],
            '--studies', job['studies'],
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout + (('\nSTDERR:\n' + result.stderr) if result.stderr.strip() else '')
    return job['label'], result.returncode, output

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Run ablations or evaluations for all students')
    parser.add_argument('--students', default=None,
                        help='Comma-separated student names (default: all)')
    parser.add_argument('--paper',   default=None, type=int, choices=[1, 2],
                        help='Which paper: 1 or 2 (default: both)')
    parser.add_argument('--study',   default='all',
                        help='Ablation studies: A, B, C, or all (default: all)')
    parser.add_argument('--eval',    default=None, choices=['pairwise', 'vs-gauntlet', 'human-vs-a', 'human-vs-b', 'human-vs-c'],
                        help='Run evaluations instead of ablations')
    parser.add_argument('--workers', default=3, type=int,
                        help='Parallel workers (default: 3)')
    args = parser.parse_args()

    students = [s.strip() for s in args.students.split(',')] if args.students else STUDENTS
    papers   = [args.paper] if args.paper else [1, 2]

    if args.eval:
        jobs = build_eval_jobs(students, papers, args.eval)
    else:
        jobs = build_ablation_jobs(students, papers, args.study)

    if not jobs:
        print("No jobs to run.")
        return

    mode_label = args.eval or 'ablation'
    print(f"Running {len(jobs)} jobs with {args.workers} workers (mode={mode_label})\n")
    for j in jobs:
        print(f"  {j['label']}")
    print()

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_job, job): job['label'] for job in jobs}
        for future in as_completed(futures):
            label, rc, output = future.result()
            status = 'OK' if rc == 0 else f'FAILED (rc={rc})'
            print(f"[{status}] {label}")
            print(output)
            results[label] = rc

    print("\n--- Summary ---")
    ok     = [l for l, rc in results.items() if rc == 0]
    failed = [l for l, rc in results.items() if rc != 0]
    print(f"  OK:     {len(ok)}")
    if failed:
        print(f"  FAILED: {len(failed)}")
        for l in failed:
            print(f"    - {l}")


if __name__ == '__main__':
    main()
