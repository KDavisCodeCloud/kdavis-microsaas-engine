"""
Derives a short product name from opportunity_pipeline.solution_concept.

solution_concept is a full descriptive sentence written by the research
swarm ("A contractor payment compliance tool that automatically collects
W-9s via a branded portal..."), not a short name — there is no dedicated
name field anywhere in the schema (confirmed against the live table).
Slugifying it directly crashed a real 2026-07-17 brief_generator run with
"File name too long" on the git branch ref. brief_generator uses this to
produce the name a human actually reads on the dashboard; scaffold_generator
instead just caps its own slug length as a cheaper safety net, since a
build-time git branch name doesn't need to be pretty, only short enough
to exist.
"""
from core.llm_router import analyze

NAME_SYSTEM_PROMPT = (
    "You name B2B micro-SaaS products. Given a one-paragraph product "
    "description, respond with a short product name: 2-4 words, Title "
    "Case, no punctuation, no quotes, no company suffix like 'Inc' or "
    "'LLC'. Your entire response must be that name and nothing else — no "
    "reasoning, no alternatives, no explanation, no second line."
)

_MAX_NAME_LENGTH = 60


def derive_product_name(solution_concept: str, llm_analyze=analyze) -> str:
    raw = llm_analyze(NAME_SYSTEM_PROMPT, solution_concept)
    # A real 2026-07-17 run showed Sonnet doesn't always follow "nothing
    # else" — it returned reasoning text after the name on later lines.
    # Take the first non-empty line as a hard guard regardless of prompt
    # compliance, and cap length so a verbose single-line reply can't
    # produce an unwieldy slug either.
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    name = first_line.strip('"').strip("'")
    if len(name) > _MAX_NAME_LENGTH:
        name = name[:_MAX_NAME_LENGTH].rsplit(" ", 1)[0]
    return name or "Untitled Product"
