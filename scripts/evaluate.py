#!/usr/bin/env python3
"""
evaluate.py -- Run blind Gemini evaluation comparing student review vs Gauntlet CONSOLIDATED_REVIEW.

Usage:
    python scripts/evaluate.py <student> [--review N]

    python scripts/evaluate.py ardubal
    python scripts/evaluate.py ardubal --review 2

Output: <student>/evaluation_paper<N>.md  (3 runs: 1x temp=0.2, 2x temp=0.3)
"""

import os, re, sys, random, argparse, textwrap
from pathlib import Path
from datetime import datetime

# Load .env from project root
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
MODEL       = 'gemini-3-pro-preview'
RUNS        = [(0.2, 1), (0.3, 2), (0.3, 3)]   # (temperature, run_number)


# ---------------------------------------------------------------------------
# File finders
# ---------------------------------------------------------------------------

def find_student_review(student_dir: Path, review_num: int) -> Path:
    """Find the Nth human review .md (1-indexed, alpha order).
    Skips: template.md, gauntlet_review_*, evaluation_*, gemini-prompt-*
    """
    skip = re.compile(r'^(template|gauntlet_review|evaluation_|gemini-prompt)', re.IGNORECASE)
    candidates = sorted(p for p in student_dir.glob('*.md') if not skip.match(p.name))
    if not candidates:
        raise FileNotFoundError(f"No student review found in {student_dir}")
    if review_num > len(candidates):
        raise FileNotFoundError(
            f"Review #{review_num} requested but only {len(candidates)} found: "
            + ', '.join(p.name for p in candidates)
        )
    return candidates[review_num - 1]


def find_consolidated(student_dir: Path, review_num: int) -> Path:
    p = student_dir / f'gauntlet_review_{review_num}' / 'CONSOLIDATED_REVIEW.md'
    if not p.exists():
        raise FileNotFoundError(f"CONSOLIDATED_REVIEW.md not found at {p}")
    return p


def extract_rubric_dimensions(rubric_text: str) -> str:
    """Extract the six dimension sections from eval-rubric.md."""
    lines = rubric_text.splitlines()
    start = next((i for i, l in enumerate(lines) if '### Dimension 1' in l), 0)
    end   = next((i for i, l in enumerate(lines) if '### Overall Preference' in l), len(lines))
    return '\n'.join(lines[start:end]).strip()


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

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


def build_prompt(human_text: str, gauntlet_text: str, rubric_dims: str,
                 paper_title: str, flip: bool):
    """Assemble the blind evaluation prompt. flip=True swaps A/B assignment."""
    a_label = 'Gauntlet' if flip else 'Human'
    b_label = 'Human'    if flip else 'Gauntlet'
    a_text  = gauntlet_text if flip else human_text
    b_text  = human_text   if flip else gauntlet_text

    prompt = (
        "# Blind Evaluation Prompt\n\n"
        "You are an expert evaluator in computer architecture. "
        "You will read two analyses of the same research paper and score them on six dimensions "
        "using the rubric below. The analyses are labeled **Analysis A** and **Analysis B**. "
        "The order is randomized -- you do not know which was written by a human researcher "
        "and which by an automated system. **Judge only the content, not the source.**\n\n"
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
# Summary
# ---------------------------------------------------------------------------

DIMENSIONS = [
    "Mechanistic Accuracy",
    "Insight Depth",
    "Critical Rigor",
    "Breadth of Perspective",
    "Calibration",
    "Usefulness",
]

def parse_run_scores(response_text: str, a_label: str):
    """Extract per-dimension scores from a run response. Returns {dim: (gauntlet, human)}."""
    scores = {}
    for dim in DIMENSIONS:
        pat = re.compile(r'\|\s*(?:\d+\.\s*)?' + re.escape(dim) + r'\s*\|\s*(\d)\s*\|\s*(\d)\s*\|', re.IGNORECASE)
        m = pat.search(response_text)
        if m:
            a_score, b_score = int(m.group(1)), int(m.group(2))
            if a_label == 'Gauntlet':
                scores[dim] = (a_score, b_score)   # (gauntlet, human)
            else:
                scores[dim] = (b_score, a_score)
    return scores


def parse_preference(response_text: str, a_label: str) -> str:
    """Extract overall preference, normalized to Gauntlet/Human framing."""
    m = re.search(r'\*\*Overall preference:\*\*\s*([^\n]+)', response_text)
    if not m:
        return 'unknown'
    raw = m.group(1).strip().lower()
    # Determine winner from raw text
    if 'a clearly' in raw:
        winner, strength = a_label, 'clearly'
    elif 'a somewhat' in raw:
        winner, strength = a_label, 'somewhat'
    elif 'b clearly' in raw:
        winner, strength = ('Human' if a_label == 'Gauntlet' else 'Gauntlet'), 'clearly'
    elif 'b somewhat' in raw:
        winner, strength = ('Human' if a_label == 'Gauntlet' else 'Gauntlet'), 'somewhat'
    else:
        return 'Tie'
    return f"{winner} {strength}"


def build_summary(run_data: list) -> str:
    """
    run_data: list of (run_num, temp, a_label, response_text)
    Returns a markdown summary section.
    """
    all_scores = {}   # dim -> list of (gauntlet, human)
    preferences = []

    for run_num, temp, a_label, response in run_data:
        if response.startswith('[ERROR'):
            continue
        scores = parse_run_scores(response, a_label)
        pref   = parse_preference(response, a_label)
        preferences.append(f"Run {run_num} (temp={temp}): **{pref}**")
        for dim, (g, h) in scores.items():
            all_scores.setdefault(dim, []).append((g, h))

    lines = [
        "---",
        "## Summary Across 3 Runs",
        "",
        "### Overall Preferences",
        "",
    ]
    for p in preferences:
        lines.append(f"- {p}")

    lines += ["", "### Average Scores (Gauntlet vs Human)", ""]
    lines.append("| Dimension | Gauntlet (avg) | Human (avg) | Delta |")
    lines.append("|-----------|:--------------:|:-----------:|:-----:|")

    gauntlet_totals, human_totals = [], []
    for dim in DIMENSIONS:
        pairs = all_scores.get(dim, [])
        if not pairs:
            lines.append(f"| {dim} | -- | -- | -- |")
            continue
        g_avg = sum(g for g, h in pairs) / len(pairs)
        h_avg = sum(h for g, h in pairs) / len(pairs)
        delta = g_avg - h_avg
        sign  = '+' if delta >= 0 else ''
        lines.append(f"| {dim} | {g_avg:.1f} | {h_avg:.1f} | {sign}{delta:.1f} |")
        gauntlet_totals.append(g_avg)
        human_totals.append(h_avg)

    if gauntlet_totals:
        g_mean = sum(gauntlet_totals) / len(gauntlet_totals)
        h_mean = sum(human_totals) / len(human_totals)
        delta  = g_mean - h_mean
        sign   = '+' if delta >= 0 else ''
        lines += [
            f"| **Overall mean** | **{g_mean:.1f}** | **{h_mean:.1f}** | **{sign}{delta:.1f}** |",
        ]

    lines.append("")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_evaluation(student: str, review_num: int):
    student_dir = BASE / student
    if not student_dir.is_dir():
        sys.exit(f"Student directory not found: {student_dir}")

    human_path    = find_student_review(student_dir, review_num)
    gauntlet_path = find_consolidated(student_dir, review_num)
    rubric_text   = RUBRIC_PATH.read_text(encoding='utf-8')

    human_text    = human_path.read_text(encoding='utf-8')
    gauntlet_text = gauntlet_path.read_text(encoding='utf-8')
    rubric_dims   = extract_rubric_dimensions(rubric_text)
    paper_title   = human_path.stem.replace('_', ' ').replace('-', ' ').title()

    out_path = student_dir / f'evaluation_paper{review_num}.md'
    results  = [
        f"# Evaluation Results -- {student} / Paper {review_num}",
        f"**Paper:** {paper_title}",
        f"**Model:** {MODEL}",
        f"**Human review:** {human_path.name}",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    run_data = []
    for temp, run_num in RUNS:
        flip = random.choice([True, False])
        prompt, a_label, b_label = build_prompt(
            human_text, gauntlet_text, rubric_dims, paper_title, flip
        )

        print(f"  Run {run_num}/3 (temp={temp}, A={a_label}, B={b_label})...", end=' ', flush=True)
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

    results.append(build_summary(run_data))
    out_path.write_text('\n'.join(results), encoding='utf-8')
    print(f"  Saved -> {out_path.relative_to(BASE)}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Blind Gemini evaluation of student vs Gauntlet review'
    )
    parser.add_argument('student', help='Student directory name (e.g. ardubal)')
    parser.add_argument('--review', type=int, default=1, metavar='N',
                        help='Paper/review number (default: 1)')
    args = parser.parse_args()

    print(f"Evaluating {args.student} / paper {args.review} ...")
    run_evaluation(args.student, args.review)
