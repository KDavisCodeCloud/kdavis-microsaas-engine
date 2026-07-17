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
    "description, respond with ONLY a short product name: 2-4 words, "
    "Title Case, no punctuation, no quotes, no company suffix like "
    "'Inc' or 'LLC'. Nothing else in your response."
)


def derive_product_name(solution_concept: str, llm_analyze=analyze) -> str:
    name = llm_analyze(NAME_SYSTEM_PROMPT, solution_concept).strip().strip('"').strip("'")
    return name or "Untitled Product"
