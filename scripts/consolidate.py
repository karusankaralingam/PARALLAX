#!/usr/bin/env python3
"""
consolidate.py — Build CONSOLIDATED_REVIEW.md from a gauntlet_review_N directory.

Usage:
    python scripts/consolidate.py <student> <review_number>
    python scripts/consolidate.py ardubal 1
    python scripts/consolidate.py --all 1          # run for all student dirs

Mapping (matches template.md questions):
    Q1 <- dr_microarch_reader_review.md  (whiteboard + skeptic's check, no discussion Qs)
    Q2 <- SYNTHESIS.md                   (section 3: Magic Trick)
    Q3 <- prof_workloads_reader_review.md (sections 1-4, no discussion Qs)
    Q4 <- SYNTHESIS.md                   (section 4: Skeleton in the Closet)
    Q5 <- skipped
"""

import os, re, sys, argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUDENTS = [
    'amittal26','ardubal','chithra','ian','kannakaranko',
    'naggarwal28','noah','rnjain','selagamsetty','vramadas','weichu'
]

# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_section(text, start_patterns, stop_patterns):
    lines = text.split('\n')
    start_idx = None
    for i, line in enumerate(lines):
        if start_idx is None:
            for pat in start_patterns:
                if re.search(pat, line, re.IGNORECASE):
                    start_idx = i
                    break
        else:
            for pat in stop_patterns:
                if re.search(pat, line, re.IGNORECASE):
                    return '\n'.join(lines[start_idx:i]).strip()
    if start_idx is not None:
        return '\n'.join(lines[start_idx:]).strip()
    return None


def extract_microarch_q1(text):
    """Full dr_microarch content, stopping before Discussion Questions."""
    stop = [r'^#+.*Discussion Questions', r'^#+.*Summary']
    lines = text.split('\n')
    for i, line in enumerate(lines):
        for pat in stop:
            if re.search(pat, line, re.IGNORECASE):
                return '\n'.join(lines[:i]).strip()
    return text.strip()


def extract_prof_workloads_q3(text):
    """Prof workloads sections 1-4, stopping before section 5 or Discussion Qs."""
    stop = [r'^#+\s*5[\.\s]', r'^#+.*Discussion Questions',
            r'^#+.*Summary Verdict', r'^#+.*Verdict$']
    lines = text.split('\n')
    for i, line in enumerate(lines):
        for pat in stop:
            if re.search(pat, line, re.IGNORECASE):
                return '\n'.join(lines[:i]).strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Heading fix (run after assembly)
# ---------------------------------------------------------------------------

def fix_headings(text):
    """
    1. ## Qn: ... -> # Qn: ...
    2. Remove redundant subtitle heading immediately following each Q header
    3. Within Q1 content, upgrade stray # headers to ##
    4. Collapse consecutive --- separators into one
    """
    lines = text.split('\n')
    result = []
    i = 0
    current_q = None

    while i < len(lines):
        line = lines[i]

        m = re.match(r'^## (Q[1-4]:.+)$', line)
        if m:
            current_q = m.group(1)[:2]
            result.append('# ' + m.group(1))
            i += 1
            while i < len(lines) and lines[i].strip() == '':
                result.append(lines[i])
                i += 1
            if i < len(lines) and re.match(r'^#+\s', lines[i]):
                i += 1  # drop redundant heading
            continue

        if current_q == 'Q1' and re.match(r'^# ', line):
            result.append('## ' + line[2:])
            i += 1
            continue

        if line.strip() == '---':
            result.append(line)
            i += 1
            while i < len(lines) and lines[i].strip() == '---':
                i += 1
            continue

        result.append(line)
        i += 1

    return '\n'.join(result)


# ---------------------------------------------------------------------------
# Main consolidation
# ---------------------------------------------------------------------------

def consolidate(student, review_num):
    gr = os.path.join(BASE, student, f'gauntlet_review_{review_num}')
    if not os.path.isdir(gr):
        print(f"  SKIP {student}: {gr} not found")
        return

    synthesis_path = os.path.join(gr, 'SYNTHESIS.md')
    microarch_path = os.path.join(gr, 'dr_microarch_reader_review.md')
    workloads_path = os.path.join(gr, 'prof_workloads_reader_review.md')

    for p in [synthesis_path, microarch_path, workloads_path]:
        if not os.path.exists(p):
            print(f"  SKIP {student}: missing {os.path.basename(p)}")
            return

    synthesis = open(synthesis_path, encoding='utf-8').read()
    microarch = open(microarch_path, encoding='utf-8').read()
    workloads = open(workloads_path, encoding='utf-8').read()

    q1 = extract_microarch_q1(microarch)
    q2 = extract_section(synthesis,
            start_patterns=[r'3\.\s+The.*Magic Trick', r'Magic Trick'],
            stop_patterns=[r'^##\s+\d+\.', r'^##\s+[A-Z]'])
    q3 = extract_prof_workloads_q3(workloads)
    q4 = extract_section(synthesis,
            start_patterns=[r'4\.\s+The.*Skeleton', r'Skeleton in the Closet'],
            stop_patterns=[r'^##\s+\d+\.', r'^##\s+[A-Z]', r'^##\s+Final'])

    missing = [n for n, v in [('Q1',q1),('Q2',q2),('Q3',q3),('Q4',q4)] if not v]
    if missing:
        print(f"  WARN {student}: extraction failed for {missing}")

    raw = f"""# Consolidated Gauntlet Review

---

## Q1: Whiteboard Explanation

{q1 or '_[extraction failed]_'}

---

## Q2: The Key Insight

{q2 or '_[extraction failed]_'}

---

## Q3: Evaluation Critique

{q3 or '_[extraction failed]_'}

---

## Q4: What the Authors Didn't Tell You

{q4 or '_[extraction failed]_'}
"""
    fixed = fix_headings(raw)
    out_path = os.path.join(gr, 'CONSOLIDATED_REVIEW.md')
    open(out_path, 'w', encoding='utf-8').write(fixed)
    print(f"  OK: {student}/gauntlet_review_{review_num}/CONSOLIDATED_REVIEW.md")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build CONSOLIDATED_REVIEW.md from gauntlet_review_N')
    parser.add_argument('student', nargs='?', help='Student directory name')
    parser.add_argument('--review', dest='review_num', type=int, default=1, metavar='N', help='Review number (default: 1)')
    parser.add_argument('--all', action='store_true', help='Run for all known student directories')
    args = parser.parse_args()

    if args.all:
        print(f"Running for all students, review #{args.review_num}")
        for s in STUDENTS:
            consolidate(s, args.review_num)
    elif args.student:
        consolidate(args.student, args.review_num)
    else:
        parser.print_help()
