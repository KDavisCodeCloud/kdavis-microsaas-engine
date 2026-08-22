"""
Shared JSON-from-narrated-LLM-response extractors.

A web-search-backed call (core.llm_router.analyze_with_web_search) narrates
its research before emitting the final JSON per its prompt's OUTPUT CONTRACT
— the model does not emit bare JSON at the very start of the response the
way a no-search prompt could get away with assuming. These extractors scan
for the LAST top-level balanced span of the requested bracket type and parse
it, so nested brackets inside the payload itself (source_evidence lists,
milestone_sequence arrays, existing_tool/no_saturation_checklist objects)
are never mistaken for the outer span's own close.

Moved here 2026-08-22 out of agents/orchestrator/agent.py (where
_extract_trailing_json_array originated) so new vertical intel agents can
reuse it without duplicating the scanner or reaching into the orchestrator
module. agents/aggregator/agent.py's own _extract_trailing_json (objects)
is a separate, still-in-place implementation — not moved here in this pass,
since nothing outside that module currently needs it.
"""
import json
import re


def coerce_mrr_number(value) -> float:
    """
    Best-effort coercion of a submitted conservative_mrr_potential to a
    plain float, tolerating the formatted-string shape Haiku sometimes
    returns despite the schema asking for a bare number (observed live,
    2026-08-22, on a real Trades-agent run: '$47,500/month at mature
    scale (1,000 customers x $59 ARPU)' instead of 47500.0). Silently
    passing that string through to node_write_pipeline's own
    `float(mrr)` would raise ValueError there, get caught, and fall back
    to 0.0 -- which then trips agents/aggregator/agent.py's
    _prefilter_reject as a false "below floor" kill, discarding a
    submission whose real number was fine. Strips a leading currency
    symbol and thousands separators, then reads the leading numeric run
    and ignores any trailing prose ("/month at mature scale...").

    Returns 0.0 (not a raise) if nothing numeric can be recovered --
    matching node_write_pipeline's own existing fallback behavior for a
    genuinely missing/invalid number, not a new failure mode.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    cleaned = value.strip().lstrip("$").replace(",", "")
    match = re.match(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else 0.0


def extract_trailing_json_array(text: str) -> list:
    """Finds the LAST top-level balanced [...] span in `text` and parses it.
    Returns [] if nothing parses (a loud failure belongs to the caller to
    decide on, not this helper — Dispatch's fallback path already treats an
    empty list as "no findings this run", not an error)."""
    spans = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append((start, i))

    for start, end in reversed(spans):
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue
    return []
