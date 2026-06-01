#!/usr/bin/env python3
"""
evaluate_ablation.py -- Blind Gemini evaluation of ablation studies.

Two modes:
  vs-gauntlet (default)  Compare each of Study A/B/C against CONSOLIDATED_REVIEW.
                         Output: eval_study_A.md, eval_study_B.md, eval_study_C.md

  pairwise               Compare studies against each other: A vs B, A vs C, B vs C.
                         Output: eval_AvB.md, eval_AvC.md, eval_BvC.md

Usage:
    python scripts/evaluate_ablation.py ardubal --ablation-dir ardubal/ablation_out_2
    python scripts/evaluate_ablation.py ardubal --ablation-dir ardubal/ablation_out_2 --mode pairwise
    python scripts/evaluate_ablation.py ardubal --ablation-dir ardubal/ablation_out_2 --mode pairwise --studies A,B
"""

import os, re, sys, random, argparse
from pathlib import Path
from datetime import datetime
from itertools import combinations

ENV_PATH = Path(__file__).parent.parent / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    sys.exit("Missing dependency: pip install google-genai")

BASE        = Path(__file__).parent.parent
RUBRIC_PATH = BASE / 'eval-rubric.md'
MODEL       = 'gemini-3.1-pro-preview'  # gemini-3-pro-preview retired (404); 3.1 is the live successor
RUNS        = [(0.2, 1), (0.3, 2), (0.3, 3)]

ABLATION_FILES = {
    'A': 'study_A.md',
    'B': 'study_B.md',
    'C': 'study_C_CONSOLIDATED.md',
}

DIMENSIONS = [
    "Mechanistic Accuracy",
    "Insight Depth",
    "Critical Rigor",
    "Breadth of Perspective",
    "Calibration",
    "Usefulness",
]

# ---------------------------------------------------------------------------
# Rubric
# ---------------------------------------------------------------------------

def extract_rubric_dimensions(rubric_text: str) -> str:
    lines = rubric_text.splitlines()
    start = next((i for i, l in enumerate(lines) if '### Dimension 1' in l), 0)
    end   = next((i for i, l in enumerate(lines) if '### Overall Preference' in l), len(lines))
    return '\n'.join(lines[start:end]).strip()


SCORE_SHEET = """\
## Score Sheet

Please score both analyses on each dimension (1-5), provide your overall preference,
and justify in 3-5 sentences.

| Dimension | Analysis A (1-5) | Analysis B (1-5) |
|-----------|:----------------:|:----------------:|
| 1. Mechanistic Accuracy | | |
| 2. Insight Depth | | |
| 3. Critical Rigor | | |
| 4. Breadth of Perspective | | |
| 5. Calibration | | |
| 6. Usefulness | | |

**Overall preference:** A clearly / A somewhat / Tie / B somewhat / B clearly

**Justification (3-5 sentences):**
"""

# ---------------------------------------------------------------------------
# Generic prompt / parse / summary (works for any two labels)
# ---------------------------------------------------------------------------

def build_prompt(text1: str, label1: str, text2: str, label2: str,
                 rubric_dims: str, paper_title: str, flip: bool):
    """flip=True swaps which text goes in slot A vs B."""
    a_label, b_label = (label2, label1) if flip else (label1, label2)
    a_text,  b_text  = (text2,  text1)  if flip else (text1,  text2)

    prompt = (
        "# Blind Evaluation Prompt\n\n"
        "You are an expert evaluator in computer architecture. "
        "You will read two analyses of the same research paper and score them on six dimensions "
        "using the rubric below. The analyses are labeled **Analysis A** and **Analysis B**. "
        "The order is randomized -- you do not know which analysis was produced by which method. "
        "**Judge only the content, not the source.**\n\n"
        "---\n\n"
        f"## Paper Being Evaluated\n\n**Title:** {paper_title}\n\n"
        "Judge the analyses solely on internal consistency, specificity, and reasoning quality.\n\n"
        "---\n\n"
        f"## Scoring Rubric\n\n{rubric_dims}\n\n"
        "---\n\n"
        f"## Analysis A\n\n{a_text}\n\n"
        "---\n\n"
        f"## Analysis B\n\n{b_text}\n\n"
        "---\n\n"
        + SCORE_SHEET
    )
    return prompt, a_label, b_label


def parse_run_scores(response_text: str, a_label: str, label1: str):
    """Returns {dim: (label1_score, label2_score)}."""
    scores = {}
    for dim in DIMENSIONS:
        pat = re.compile(r'\|\s*(?:\d+\.\s*)?' + re.escape(dim) + r'\s*\|\s*(\d)\s*\|\s*(\d)\s*\|',
                         re.IGNORECASE)
        m = pat.search(response_text)
        if m:
            a_score, b_score = int(m.group(1)), int(m.group(2))
            if a_label == label1:
                scores[dim] = (a_score, b_score)
            else:
                scores[dim] = (b_score, a_score)
    return scores


def parse_preference(response_text: str, a_label: str, label1: str, label2: str) -> str:
    """Returns preference string normalized to label1/label2 framing."""
    m = re.search(r'\*\*Overall preference:\*\*\s*([^\n]+)', response_text)
    if not m:
        return 'unknown'
    raw = m.group(1).strip().lower()
    if 'a clearly' in raw:
        winner, strength = a_label, 'clearly'
    elif 'a somewhat' in raw:
        winner, strength = a_label, 'somewhat'
    elif 'b clearly' in raw:
        winner  = label2 if a_label == label1 else label1
        strength = 'clearly'
    elif 'b somewhat' in raw:
        winner  = label2 if a_label == label1 else label1
        strength = 'somewhat'
    else:
        return 'Tie'
    return f"{winner} {strength}"


def build_summary(run_data: list, label1: str, label2: str) -> str:
    all_scores  = {}
    preferences = []

    for run_num, temp, a_label, response in run_data:
        if response.startswith('[ERROR'):
            continue
        scores = parse_run_scores(response, a_label, label1)
        pref   = parse_preference(response, a_label, label1, label2)
        preferences.append(f"Run {run_num} (temp={temp}): **{pref}**")
        for dim, pair in scores.items():
            all_scores.setdefault(dim, []).append(pair)

    lines = ["---", "## Summary Across 3 Runs", "", "### Overall Preferences", ""]
    for p in preferences:
        lines.append(f"- {p}")

    lines += ["", f"### Average Scores ({label1} vs {label2})", ""]
    lines.append(f"| Dimension | {label1} (avg) | {label2} (avg) | Delta |")
    lines.append("|-----------|:--------------:|:--------------:|:-----:|")

    totals1, totals2 = [], []
    for dim in DIMENSIONS:
        pairs = all_scores.get(dim, [])
        if not pairs:
            lines.append(f"| {dim} | -- | -- | -- |")
            continue
        avg1  = sum(s1 for s1, s2 in pairs) / len(pairs)
        avg2  = sum(s2 for s1, s2 in pairs) / len(pairs)
        delta = avg1 - avg2
        sign  = '+' if delta >= 0 else ''
        lines.append(f"| {dim} | {avg1:.1f} | {avg2:.1f} | {sign}{delta:.1f} |")
        totals1.append(avg1)
        totals2.append(avg2)

    if totals1:
        m1    = sum(totals1) / len(totals1)
        m2    = sum(totals2) / len(totals2)
        delta = m1 - m2
        sign  = '+' if delta >= 0 else ''
        lines.append(f"| **Overall mean** | **{m1:.1f}** | **{m2:.1f}** | **{sign}{delta:.1f}** |")

    lines.append("")
    return '\n'.join(lines)

# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def call_gemini(prompt: str, temperature: float) -> str:
    api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY not set in .env")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(temperature=temperature),
    )
    return response.text

# ---------------------------------------------------------------------------
# Core evaluation runner (generic)
# ---------------------------------------------------------------------------

def run_comparison(text1: str, label1: str, text2: str, label2: str,
                   rubric_dims: str, paper_title: str,
                   out_path: Path, heading: str):
    results = [
        f"# {heading}",
        f"**Paper:** {paper_title}",
        f"**Model:** {MODEL}",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    run_data = []
    for temp, run_num in RUNS:
        flip = random.choice([True, False])
        prompt, a_label, b_label = build_prompt(
            text1, label1, text2, label2, rubric_dims, paper_title, flip
        )
        print(f"  {label1} vs {label2} — Run {run_num}/3 (temp={temp}, A={a_label}, B={b_label})...",
              end=' ', flush=True)
        try:
            response = call_gemini(prompt, temp)
            print("OK")
        except Exception as e:
            response = f"[ERROR: {e}]"
            print(f"FAILED: {e}")

        run_data.append((run_num, temp, a_label, response))
        results += [
            "---",
            f"## Run {run_num} -- temperature={temp}  |  A={a_label}, B={b_label}",
            "",
            response,
            "",
        ]

    results.append(build_summary(run_data, label1, label2))
    out_path.write_text('\n'.join(results), encoding='utf-8')
    print(f"  Saved -> {out_path.relative_to(BASE)}")

# ---------------------------------------------------------------------------
# Mode: vs-gauntlet
# ---------------------------------------------------------------------------

def run_vs_gauntlet(ablation_dir: Path, studies: list[str],
                    gauntlet_text: str, rubric_dims: str, paper_title: str):
    for letter in studies:
        ablation_file = ablation_dir / ABLATION_FILES[letter]
        if not ablation_file.exists():
            print(f"  SKIP Study {letter}: {ablation_file.name} not found in {ablation_dir.name}")
            continue
        out_path = ablation_dir / f'eval_study_{letter}.md'
        if out_path.exists():
            print(f"  SKIP Study {letter}: {out_path.name} already exists")
            continue
        ablation_text = ablation_file.read_text(encoding='utf-8')
        run_comparison(
            text1=ablation_text,  label1=f'Study {letter}',
            text2=gauntlet_text,  label2='Gauntlet',
            rubric_dims=rubric_dims,
            paper_title=paper_title,
            out_path=ablation_dir / f'eval_study_{letter}.md',
            heading=f'Ablation Evaluation -- Study {letter} vs Gauntlet',
        )

# ---------------------------------------------------------------------------
# Mode: human-vs-c
# ---------------------------------------------------------------------------

def find_human_review(student_dir: Path, review_num: int) -> Path | None:
    skip = re.compile(r'^(template|gauntlet_review|evaluation_|gemini-prompt|ablation)', re.IGNORECASE)
    candidates = sorted(p for p in student_dir.glob('*.md') if not skip.match(p.name))
    if not candidates or review_num > len(candidates):
        return None
    return candidates[review_num - 1]


def run_human_vs_study(student_dir: Path, ablation_dir: Path, study_num: int,
                       study_letter: str, rubric_dims: str, paper_title: str):
    out_path = ablation_dir / f'eval_human_vs_{study_letter}.md'
    if out_path.exists():
        print(f"  SKIP human-vs-{study_letter}: {out_path.name} already exists")
        return

    study_file = ablation_dir / ABLATION_FILES[study_letter]
    if not study_file.exists():
        print(f"  SKIP human-vs-{study_letter}: {study_file.name} not found")
        return

    human_path = find_human_review(student_dir, study_num)
    if human_path is None:
        print(f"  SKIP human-vs-{study_letter}: no human review #{study_num} found in {student_dir.name}/")
        return

    print(f"  Human review: {human_path.name}")
    run_comparison(
        text1=human_path.read_text(encoding='utf-8'),   label1='Human',
        text2=study_file.read_text(encoding='utf-8'),   label2=f'Study {study_letter}',
        rubric_dims=rubric_dims,
        paper_title=paper_title,
        out_path=out_path,
        heading=f'Evaluation -- Human Review vs Study {study_letter}',
    )

# ---------------------------------------------------------------------------
# Mode: pairwise
# ---------------------------------------------------------------------------

def run_pairwise(ablation_dir: Path, studies: list[str],
                 rubric_dims: str, paper_title: str):
    texts = {}
    for letter in studies:
        f = ablation_dir / ABLATION_FILES[letter]
        if f.exists():
            texts[letter] = f.read_text(encoding='utf-8')
        else:
            print(f"  SKIP Study {letter}: {f.name} not found in {ablation_dir.name}")

    for l1, l2 in combinations(sorted(texts.keys()), 2):
        out_path = ablation_dir / f'eval_{l1}v{l2}.md'
        if out_path.exists():
            print(f"  SKIP {l1}v{l2}: {out_path.name} already exists")
            continue
        run_comparison(
            text1=texts[l1],  label1=f'Study {l1}',
            text2=texts[l2],  label2=f'Study {l2}',
            rubric_dims=rubric_dims,
            paper_title=paper_title,
            out_path=out_path,
            heading=f'Ablation Evaluation -- Study {l1} vs Study {l2}',
        )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_ablation_evaluation(student: str, study_num: int, studies: list[str],
                            ablation_dir_override: str | None, mode: str):
    student_dir = BASE / student
    if not student_dir.is_dir():
        sys.exit(f"Student directory not found: {student_dir}")

    if ablation_dir_override:
        ablation_dir = Path(ablation_dir_override)
        if not ablation_dir.is_absolute():
            ablation_dir = BASE / ablation_dir
    else:
        ablation_dir = student_dir / f'ablation_study_{study_num}'

    if not ablation_dir.is_dir():
        sys.exit(f"Ablation directory not found: {ablation_dir}")

    rubric_text = RUBRIC_PATH.read_text(encoding='utf-8')
    rubric_dims = extract_rubric_dimensions(rubric_text)

    # Derive paper title from study headers
    paper_title = student
    for letter in ('A', 'B', 'C'):
        candidate = ablation_dir / ABLATION_FILES[letter]
        if candidate.exists():
            for line in candidate.read_text(encoding='utf-8').splitlines()[:5]:
                m = re.search(r'\*\*Paper:\*\*\s*(.+)', line)
                if m:
                    paper_title = m.group(1).strip()
                    break
            break

    print(f"Mode: {mode} | {student} | Paper: {paper_title}")
    print(f"Ablation dir: {ablation_dir.relative_to(BASE)}")

    if mode == 'pairwise':
        run_pairwise(ablation_dir, studies, rubric_dims, paper_title)
    elif mode.startswith('human-vs-'):
        letter = mode.split('-')[-1].upper()
        run_human_vs_study(student_dir, ablation_dir, study_num, letter, rubric_dims, paper_title)
    else:
        gauntlet_path = student_dir / f'gauntlet_review_{study_num}' / 'CONSOLIDATED_REVIEW.md'
        if not gauntlet_path.exists():
            sys.exit(f"CONSOLIDATED_REVIEW.md not found: {gauntlet_path}")
        gauntlet_text = gauntlet_path.read_text(encoding='utf-8')
        run_vs_gauntlet(ablation_dir, studies, gauntlet_text, rubric_dims, paper_title)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Blind Gemini evaluation of ablation studies'
    )
    parser.add_argument('student',       help='Student directory name (e.g. ardubal)')
    parser.add_argument('--study-num',   type=int, default=1, metavar='N',
                        help='Study number matching gauntlet_review_N (default: 1)')
    parser.add_argument('--studies',     default='A,B,C',
                        help='Comma-separated studies: A, B, C (default: A,B,C)')
    parser.add_argument('--ablation-dir', default=None, metavar='DIR',
                        help='Path to ablation output dir (default: <student>/ablation_study_N). '
                             'Relative paths resolved from project root.')
    parser.add_argument('--mode',        default='vs-gauntlet',
                        choices=['vs-gauntlet', 'pairwise', 'human-vs-a', 'human-vs-b', 'human-vs-c'],
                        help='vs-gauntlet: each study vs CONSOLIDATED_REVIEW (default). '
                             'pairwise: A vs B, A vs C, B vs C. '
                             'human-vs-a/b/c: student human review vs Study A, B, or C.')
    args = parser.parse_args()

    studies = [s.strip().upper() for s in args.studies.split(',')]
    run_ablation_evaluation(args.student, args.study_num, studies, args.ablation_dir, args.mode)
