#!/usr/bin/env python3
"""
ablation.py — Three-study ablation on paper comprehension quality.

Study A: Simple directive + template → Claude Opus
Study B: Rich directive (computer architect persona) + template → Claude Opus
Study C: 5 specialist personas (Dr Microarch, Prof Workloads, Prof Simtools +
         2 topic-matched experts via Gemini Flash) → synthesis → CONSOLIDATED_REVIEW.md

Usage:
    python scripts/ablation.py <pdf_path> <output_dir> [--study A|B|C|all]

    python scripts/ablation.py \\
        gauntlet-reviews/isca2025/hardware-aware-calibration.../3695053.3731036.pdf \\
        ardubal/ablation
"""

import os, re, sys, base64, argparse
from pathlib import Path
from datetime import datetime

# Load .env
ENV_PATH = Path(__file__).parent.parent / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    sys.exit("Missing dependency: pip install google-genai")

BASE         = Path(__file__).parent.parent
SCRIPTS_DIR  = Path(__file__).parent
CLAUDE_MODEL = 'claude-opus-4-5'
FLASH_MODEL  = 'gemini-2.5-flash'

# ---------------------------------------------------------------------------
# Load static assets
# ---------------------------------------------------------------------------

def load_text(path: Path) -> str:
    if not path.exists():
        return ''
    text = path.read_text(encoding='utf-8-sig').strip()
    if path.name == 'TOPICS.TXT':
        text = '\n'.join(l for l in text.splitlines() if l.strip() and not l.strip().startswith('#'))
    return text


def strip_response_structure(persona: str) -> str:
    """Remove the **Response Structure:** section from a persona prompt.
    The user prompt supplies the output format (Q1-Q4), so the persona's
    own structure definition would conflict."""
    # Split at the Response Structure header (case-insensitive)
    cut = re.split(r'\*\*Response Structure[:\*]*', persona, flags=re.IGNORECASE)
    return cut[0].strip()

TEMPLATE        = load_text(BASE / 'template.md')
TOPICS          = load_text(SCRIPTS_DIR / 'TOPICS.TXT')
DR_MICROARCH    = strip_response_structure(load_text(SCRIPTS_DIR / 'dr_microarch_reader.md'))
PROF_WORKLOADS  = strip_response_structure(load_text(SCRIPTS_DIR / 'prof_workloads_reader.md'))
PROF_SIMTOOLS   = strip_response_structure(load_text(SCRIPTS_DIR / 'prof_simtools_reader.md'))
EXPERT_1        = strip_response_structure(load_text(SCRIPTS_DIR / 'expert_1.md'))
EXPERT_2        = strip_response_structure(load_text(SCRIPTS_DIR / 'expter_2.md'))

# template without Q5
TEMPLATE_Q1_Q4 = '\n'.join(
    line for line in TEMPLATE.splitlines()
    if not line.strip().startswith('5.')
)

RESPONSE_FORMAT = """\
Respond using EXACTLY this structure (Q1–Q4 only, skip Q5):

Q1: Whiteboard Explanation
[Your answer]

Q2: The Key Insight
[Your answer]

Q3: Evaluation Critique — Strengths and Weaknesses
[Your answer]

Q4: What the Authors Didn't Tell You
[Your answer]
"""

# ---------------------------------------------------------------------------
# PDF encoding
# ---------------------------------------------------------------------------

def encode_pdf(pdf_path: Path) -> str:
    return base64.standard_b64encode(pdf_path.read_bytes()).decode('utf-8')

# ---------------------------------------------------------------------------
# Claude call (PDF via document block)
# ---------------------------------------------------------------------------

def call_claude(system_prompt: str, user_prompt: str, pdf_b64: str,
                max_tokens: int = 8000) -> str:
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': pdf_b64,
                    },
                },
                {
                    'type': 'text',
                    'text': user_prompt,
                }
            ]
        }]
    )
    return response.content[0].text

# ---------------------------------------------------------------------------
# Gemini Flash call (text only)
# ---------------------------------------------------------------------------

def call_flash(prompt: str) -> str:
    api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
    client  = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=FLASH_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(temperature=0.1),
    )
    return response.text

# ---------------------------------------------------------------------------
# Study A — simple directive
# ---------------------------------------------------------------------------

STUDY_A_SYSTEM = "You are a careful reader of computer architecture research papers."

STUDY_A_USER = f"""\
You need to read this paper and provide responses below which capture comprehension of the paper.

{RESPONSE_FORMAT}

Output token budget: 8000.
"""

def run_study_a(pdf_b64: str) -> str:
    print("  Study A...", end=' ', flush=True)
    result = call_claude(STUDY_A_SYSTEM, STUDY_A_USER, pdf_b64)
    print("OK")
    return result

# ---------------------------------------------------------------------------
# Study B — rich directive
# ---------------------------------------------------------------------------

STUDY_B_SYSTEM = """\
You are a computer architect with deep expertise in hardware design, microarchitecture, \
and computer systems research. You have broad knowledge spanning processor design, \
memory systems, accelerators, interconnects, and emerging computing paradigms. \
You approach every paper with both technical rigor and healthy skepticism."""

STUDY_B_USER = f"""\
You are generating a comprehension report on this paper. This is part of a study to \
understand how well LLMs can do deep comprehension of computer architecture research. \
You need to produce the highest quality answers you can — be specific, precise, and \
technically rigorous. Do not hedge unnecessarily. Call out weak claims where you see them.

{RESPONSE_FORMAT}

Output token budget: 8000.
"""

def run_study_b(pdf_b64: str) -> str:
    print("  Study B...", end=' ', flush=True)
    result = call_claude(STUDY_B_SYSTEM, STUDY_B_USER, pdf_b64)
    print("OK")
    return result

# ---------------------------------------------------------------------------
# Study C — multi-persona + synthesis
# ---------------------------------------------------------------------------

PERSONA_USER = f"""\
Read the attached paper carefully and respond using the structure below.
Skip Q5. Be specific — cite figures, section numbers, and exact claims from the paper.

{RESPONSE_FORMAT}

Output token budget: 8000.
"""

def match_topics(paper_title: str, abstract_hint: str = '') -> list[str]:
    """Use Gemini Flash to find the 2 closest topics from TOPICS.TXT."""
    if not TOPICS:
        print("  WARN: TOPICS.TXT is empty — skipping topic matching, using generic experts")
        return []
    prompt = f"""\
Here is a list of research topics:

{TOPICS}

Paper title: {paper_title}
{('Abstract hint: ' + abstract_hint) if abstract_hint else ''}

Identify the 2 closest matching topics from the list above for this paper.
Return ONLY the 2 topic names, one per line, exactly as they appear in the list.
"""
    response = call_flash(prompt)
    topics = [line.strip() for line in response.strip().splitlines() if line.strip()]
    return topics[:2]


def build_topic_expert_persona(topic: str, expert_num: int) -> str:
    """Use Gemini Flash to generate a rich domain-expert persona for the given topic,
    using EXPERT_1 and EXPERT_2 as few-shot style examples."""
    prompt = f"""\
You are creating a system prompt for an AI persona that will review computer architecture papers.

Here are two examples of high-quality domain-expert personas. Study their structure carefully:
they define a name, domain expertise, context, mission, tone & style, and key deconstruction \
zones that are specific to the domain.

=== EXAMPLE 1 ===
{EXPERT_1}

=== EXAMPLE 2 ===
{EXPERT_2}

Now create a NEW persona in exactly the same style and depth for an expert in:
**{topic}**

Requirements:
- Give the expert a plausible name
- Make the Key Deconstruction Zones genuinely specific to {topic} — cite real techniques, \
real prior work, real failure modes that an expert in {topic} would know
- Match the incisive, skeptical-but-fair tone of the examples
- Do NOT include a Response Structure section (the output format is provided separately)
- Length: similar to the examples above
"""
    return call_flash(prompt)


def synthesize(responses: dict[str, str], paper_title: str) -> str:
    """Call Claude to synthesize 5 persona responses into CONSOLIDATED_REVIEW.md format."""
    persona_block = '\n\n'.join(
        f"=== {name} ===\n{text}" for name, text in responses.items()
    )
    system = """\
You are a synthesis agent. You will receive 5 expert reviews of the same paper \
and produce a single high-quality consolidated review."""

    user = f"""\
Paper: {paper_title}

Below are 5 expert reviews of this paper. Each covers the same 4 questions (Q1–Q4). \
Your task is to synthesize them into a single CONSOLIDATED_REVIEW.md with the structure below.

For each question:
- Identify where the experts AGREE (the consensus view)
- Identify where they DISAGREE or provide different angles (the Rashomon effect)
- Produce a single synthesized answer that is richer than any individual review
- Be specific: cite figures, numbers, section references where the reviews do so
- Do NOT average or flatten — preserve the sharpest insights even if only one expert raised them

Output structure (use exactly these headers):

# Q1: Whiteboard Explanation
[synthesized answer]

# Q2: The Key Insight
[synthesized answer]

# Q3: Evaluation Critique
[synthesized answer]

# Q4: What the Authors Didn't Tell You
[synthesized answer]

---

Here are the 5 reviews:

{persona_block}
"""
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        system=system,
        messages=[{'role': 'user', 'content': user}]
    )
    return response.content[0].text


def run_study_c(pdf_b64: str, paper_title: str) -> tuple[dict, str]:
    """Returns (persona_responses, consolidated_text)."""
    # Topic matching
    print("  Study C: matching topics...", end=' ', flush=True)
    topics = match_topics(paper_title)
    if topics:
        print(f"matched: {topics}")
    else:
        topics = ['Computer Architecture Systems', 'Hardware Accelerator Design']

    # Build personas
    personas = {
        'Dr Microarch':   DR_MICROARCH,
        'Prof Workloads': PROF_WORKLOADS,
        'Prof Simtools':  PROF_SIMTOOLS,
        f'Domain Expert ({topics[0]})': build_topic_expert_persona(topics[0], 1),
        f'Domain Expert ({topics[1]})': build_topic_expert_persona(topics[1], 2),
    }

    # Run all 5
    responses = {}
    for name, persona_system in personas.items():
        print(f"  Study C: {name}...", end=' ', flush=True)
        try:
            responses[name] = call_claude(persona_system, PERSONA_USER, pdf_b64)
            print("OK")
        except Exception as e:
            responses[name] = f"[ERROR: {e}]"
            print(f"FAILED: {e}")

    # Synthesize
    print("  Study C: synthesizing...", end=' ', flush=True)
    consolidated = synthesize(responses, paper_title)
    print("OK")

    return responses, consolidated

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_ablation(pdf_path: Path, output_dir: Path, studies: list[str]):
    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    paper_title = pdf_path.stem.replace('_', ' ').replace('-', ' ')

    print(f"Paper: {paper_title}")
    print(f"Output: {output_dir}")
    print(f"Encoding PDF ({pdf_path.stat().st_size // 1024} KB)...", end=' ')
    pdf_b64 = encode_pdf(pdf_path)
    print("done")

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    if 'A' in studies:
        result = run_study_a(pdf_b64)
        out = output_dir / 'study_A.md'
        out.write_text(
            f"# Study A — Simple Directive\n**Paper:** {paper_title}  \n"
            f"**Model:** {CLAUDE_MODEL}  \n**Generated:** {timestamp}\n\n---\n\n{result}",
            encoding='utf-8'
        )
        print(f"  -> {out.relative_to(BASE)}")

    if 'B' in studies:
        result = run_study_b(pdf_b64)
        out = output_dir / 'study_B.md'
        out.write_text(
            f"# Study B — Rich Directive\n**Paper:** {paper_title}  \n"
            f"**Model:** {CLAUDE_MODEL}  \n**Generated:** {timestamp}\n\n---\n\n{result}",
            encoding='utf-8'
        )
        print(f"  -> {out.relative_to(BASE)}")

    if 'C' in studies:
        responses, consolidated = run_study_c(pdf_b64, paper_title)
        # Save individual persona responses
        personas_dir = output_dir / 'study_C_personas'
        personas_dir.mkdir(exist_ok=True)
        for name, text in responses.items():
            safe_name = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
            (personas_dir / f'{safe_name}.md').write_text(text, encoding='utf-8')
        # Save consolidated
        out = output_dir / 'study_C_CONSOLIDATED.md'
        out.write_text(
            f"# Study C — Multi-Persona Synthesis\n**Paper:** {paper_title}  \n"
            f"**Model:** {CLAUDE_MODEL}  \n**Generated:** {timestamp}\n\n---\n\n{consolidated}",
            encoding='utf-8'
        )
        print(f"  -> {out.relative_to(BASE)}")
        print(f"  -> {(personas_dir).relative_to(BASE)}/ ({len(responses)} files)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Ablation study: A/B/C paper comprehension')
    parser.add_argument('pdf',        help='Path to paper PDF')
    parser.add_argument('output_dir', help='Directory to write results into')
    parser.add_argument('--study',    default='all',
                        help='Which studies to run: A, B, C, or all (default: all)')
    args = parser.parse_args()

    studies = ['A', 'B', 'C'] if args.study.lower() == 'all' else [s.upper() for s in args.study.split(',')]
    run_ablation(Path(args.pdf), BASE / args.output_dir, studies)
