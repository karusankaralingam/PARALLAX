#!/usr/bin/env python3
"""
ablation_openai.py — Same A/B/C ablation as ablation.py, but routed through the
NVIDIA inference gateway (OpenAI-compatible Chat Completions API) instead of the
native Anthropic / Google SDKs. Lets you bill against INFERENCE_API_KEY and/or
swap in cheaper models.

It reuses the prompts, personas, template and response format from ablation.py
verbatim — only the transport layer (and model names) change. PDFs are sent
natively via the OpenAI `file`/`file_data` content part, which the gateway
accepts for BOTH the Claude and Gemini routes (verified), so figures are
preserved exactly as in the native pipeline.

Usage:
    python scripts/ablation_openai.py <pdf_path> <output_dir> [--study A|B|C|all]
                                      [--model MODEL] [--topic-model MODEL]
                                      [--max-tokens N]

Models (gateway names):
    azure/anthropic/claude-opus-4-5   (default — matches ablation.py fidelity)
    azure/openai/gpt-5.1
    nvidia/openai/gpt-oss-20b
    gcp/google/gemini-2.5-pro         (default topic/persona-builder model)
"""

import os, re, sys, base64, argparse
from pathlib import Path
from datetime import datetime

import httpx
from openai import OpenAI

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Reuse all prompts / personas / template from the native pipeline.
import ablation as A

# ---------------------------------------------------------------------------
# Gateway config
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent

# Load INFERENCE_API_KEY / INFERENCE_API_BASE. Order: process env, then the
# project .env (already loaded by `import ablation`), then an optional fallback
# .env pointed to by INFERENCE_ENV_PATH (e.g. the forge repo's axon-llm-decode/.env).
def _load_inference_env():
    if os.environ.get("INFERENCE_API_KEY"):
        return
    candidates = []
    if os.environ.get("INFERENCE_ENV_PATH"):
        candidates.append(Path(os.environ["INFERENCE_ENV_PATH"]))
    candidates.append(BASE / ".env")
    candidates.append(Path(
        r"c:\Users\karus\Documents\VS-Code\forge-updated-repo\main\axon-llm-decode\.env"))
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")   # strip surrounding quotes
                    if v:
                        os.environ.setdefault(k.strip(), v)
        if os.environ.get("INFERENCE_API_KEY"):
            return

_load_inference_env()

def _normalize_endpoint(base: str | None) -> str:
    base = (base or "").strip().strip('"').strip("'")
    if not base:
        return "https://inference-api.nvidia.com/v1"
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base

ENDPOINT = _normalize_endpoint(os.environ.get("INFERENCE_API_BASE"))
DEFAULT_MODEL       = "azure/anthropic/claude-opus-4-5"
DEFAULT_TOPIC_MODEL = "gcp/google/gemini-2.5-pro"

CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC, WRITE_TIMEOUT_SEC, POOL_TIMEOUT_SEC = 10.0, 600.0, 120.0, 30.0


def make_client() -> OpenAI:
    key = os.environ.get("INFERENCE_API_KEY")
    if not key:
        sys.exit("INFERENCE_API_KEY not set (checked env, project .env, INFERENCE_ENV_PATH).")
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT_SEC, read=READ_TIMEOUT_SEC,
                            write=WRITE_TIMEOUT_SEC, pool=POOL_TIMEOUT_SEC)
    return OpenAI(base_url=ENDPOINT, api_key=key, timeout=timeout, max_retries=3)

CLIENT = make_client()

# ---------------------------------------------------------------------------
# Transport: replaces ablation.call_claude / call_flash with gateway calls
# ---------------------------------------------------------------------------

def call_llm_pdf(system_prompt: str, user_prompt: str, pdf_b64: str,
                 model: str, max_tokens: int = 8000) -> str:
    """Chat-completions call with a PDF attached via the OpenAI file_data part."""
    resp = CLIENT.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "file", "file": {
                    "filename": "paper.pdf",
                    "file_data": f"data:application/pdf;base64,{pdf_b64}"}},
            ]},
        ],
    )
    return resp.choices[0].message.content


def call_llm_text(system_prompt: str, user_prompt: str, model: str,
                  max_tokens: int = 8000, temperature: float = 0.1) -> str:
    """Text-only chat-completions call (topic matching, persona building, synthesis)."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    resp = CLIENT.chat.completions.create(
        model=model, max_tokens=max_tokens, temperature=temperature, messages=messages,
    )
    return resp.choices[0].message.content

# ---------------------------------------------------------------------------
# Study C helpers (reuse ablation's prompt text, swap transport)
# ---------------------------------------------------------------------------

FALLBACK_TOPICS = ["Processor Microarchitecture", "Hardware Accelerator Design"]


def _canonicalize(line: str, topic_list: list[str]) -> str | None:
    """Map a (possibly truncated/decorated) model line to the closest real TOPICS entry."""
    cleaned = line.strip().lstrip('-*0123456789.) ').strip().strip('"').strip("'").lower()
    if not cleaned:
        return None
    # exact match first
    for t in topic_list:
        if t.lower() == cleaned:
            return t
    # containment either way (handles truncation like 'Processor' -> 'Processor Microarchitecture')
    best = None
    for t in topic_list:
        tl = t.lower()
        if cleaned in tl or tl in cleaned:
            # prefer the longest overlap
            if best is None or len(t) > len(best):
                best = t
    return best


def match_topics(paper_title: str, topic_model: str) -> list[str]:
    """Return exactly 2 distinct canonical topics (padded from FALLBACK_TOPICS if needed)."""
    topic_list = [l.strip() for l in A.TOPICS.splitlines() if l.strip()]
    if not topic_list:
        print("  WARN: TOPICS.TXT empty — using generic experts", end=' ')
        return FALLBACK_TOPICS[:]
    prompt = f"""\
Here is a list of research topics:

{A.TOPICS}

Paper title: {paper_title}

Identify the 2 closest matching topics from the list above for this paper.
Return ONLY the 2 topic names, one per line, exactly as they appear in the list.
"""
    # gemini-2.5-pro is a reasoning model; give the answer ample headroom so it
    # isn't truncated after the reasoning budget (200 tokens was too small).
    response = call_llm_text("", prompt, topic_model, max_tokens=2048, temperature=0.1) or ""
    chosen = []
    for line in response.strip().splitlines():
        canon = _canonicalize(line, topic_list)
        if canon and canon not in chosen:
            chosen.append(canon)
    # pad to 2 distinct topics
    for fb in FALLBACK_TOPICS + topic_list:
        if len(chosen) >= 2:
            break
        if fb not in chosen:
            chosen.append(fb)
    return chosen[:2]


def build_topic_expert_persona(topic: str, topic_model: str) -> str:
    prompt = f"""\
You are creating a system prompt for an AI persona that will review computer architecture papers.

Here are two examples of high-quality domain-expert personas. Study their structure carefully:
they define a name, domain expertise, context, mission, tone & style, and key deconstruction \
zones that are specific to the domain.

=== EXAMPLE 1 ===
{A.EXPERT_1}

=== EXAMPLE 2 ===
{A.EXPERT_2}

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
    return call_llm_text("", prompt, topic_model, max_tokens=4000, temperature=0.4)


def synthesize(responses: dict, paper_title: str, model: str) -> str:
    persona_block = "\n\n".join(f"=== {name} ===\n{text}" for name, text in responses.items())
    system = ("You are a synthesis agent. You will receive 5 expert reviews of the same paper "
              "and produce a single high-quality consolidated review.")
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
    return call_llm_text(system, user, model, max_tokens=8000, temperature=0.3)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_ablation(pdf_path: Path, output_dir: Path, studies: list[str],
                 model: str, topic_model: str, max_tokens: int):
    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    paper_title = pdf_path.stem.replace("_", " ").replace("-", " ")

    print(f"Paper: {paper_title}")
    print(f"Output: {output_dir}")
    print(f"Model: {model}  |  Topic/persona model: {topic_model}  |  Endpoint: {ENDPOINT}")
    print(f"Encoding PDF ({pdf_path.stat().st_size // 1024} KB)...", end=" ")
    pdf_b64 = A.encode_pdf(pdf_path)
    print("done")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = lambda title: (f"# {title}\n**Paper:** {paper_title}  \n"
                            f"**Model:** {model} (via {ENDPOINT})  \n"
                            f"**Generated:** {timestamp}\n\n---\n\n")

    if "A" in studies:
        print("  Study A...", end=" ", flush=True)
        result = call_llm_pdf(A.STUDY_A_SYSTEM, A.STUDY_A_USER, pdf_b64, model, max_tokens)
        (output_dir / "study_A.md").write_text(header("Study A — Simple Directive") + result, encoding="utf-8")
        print("OK")

    if "B" in studies:
        print("  Study B...", end=" ", flush=True)
        result = call_llm_pdf(A.STUDY_B_SYSTEM, A.STUDY_B_USER, pdf_b64, model, max_tokens)
        (output_dir / "study_B.md").write_text(header("Study B — Rich Directive") + result, encoding="utf-8")
        print("OK")

    if "C" in studies:
        print("  Study C: matching topics...", end=" ", flush=True)
        topics = match_topics(paper_title, topic_model)
        print(f"matched: {topics}")

        personas = {
            "Dr Microarch":   A.DR_MICROARCH,
            "Prof Workloads": A.PROF_WORKLOADS,
            "Prof Simtools":  A.PROF_SIMTOOLS,
            f"Domain Expert ({topics[0]})": build_topic_expert_persona(topics[0], topic_model),
            f"Domain Expert ({topics[1]})": build_topic_expert_persona(topics[1], topic_model),
        }

        responses = {}
        for name, persona_system in personas.items():
            print(f"  Study C: {name}...", end=" ", flush=True)
            try:
                responses[name] = call_llm_pdf(persona_system, A.PERSONA_USER, pdf_b64, model, max_tokens)
                print("OK")
            except Exception as e:
                responses[name] = f"[ERROR: {e}]"
                print(f"FAILED: {e}")

        print("  Study C: synthesizing...", end=" ", flush=True)
        consolidated = synthesize(responses, paper_title, model)
        print("OK")

        personas_dir = output_dir / "study_C_personas"
        personas_dir.mkdir(exist_ok=True)
        for name, text in responses.items():
            safe = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
            (personas_dir / f"{safe}.md").write_text(text, encoding="utf-8")
        (output_dir / "study_C_CONSOLIDATED.md").write_text(
            header("Study C — Multi-Persona Synthesis") + consolidated, encoding="utf-8")
        print(f"  -> {len(responses)} persona files + CONSOLIDATED")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="A/B/C ablation via NVIDIA inference gateway")
    p.add_argument("pdf")
    p.add_argument("output_dir")
    p.add_argument("--study", default="all", help="A, B, C, or all (default: all)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Review model (default: {DEFAULT_MODEL})")
    p.add_argument("--topic-model", default=DEFAULT_TOPIC_MODEL,
                   help=f"Topic-match + persona-builder model (default: {DEFAULT_TOPIC_MODEL})")
    p.add_argument("--max-tokens", type=int, default=8000)
    args = p.parse_args()

    studies = ["A", "B", "C"] if args.study.lower() == "all" else [s.upper() for s in args.study.split(",")]
    run_ablation(Path(args.pdf), BASE / args.output_dir, studies,
                 args.model, args.topic_model, args.max_tokens)
