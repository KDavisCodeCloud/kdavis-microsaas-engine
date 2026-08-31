"""
DIST-P2 — Wedge Validator. Adversarial review of a DIST-P1 draft. Never
approves -- can only move a row to 'pending_review' (owner decides from
there) or leave/mark it in a way that signals rejection. The one write
this agent is structurally forbidden from making (status='approved') is
enforced at the database level regardless of this code (migration 029's
reject_direct_approval trigger + approve_positioning()'s owner-only
SECURITY DEFINER gate) -- this module doesn't rely on its own discipline
to hold that line.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from core.llm_router import analyze
from core.supabase_client import get_supabase

AGENT_ID = "dist-p2-wedge-validator"

SYSTEM_PROMPT = """You are an adversarial reviewer whose only job is to try to \
defeat a proposed product "wedge" (the thing substitute products structurally \
cannot do). You are given a DIST-P1 draft positioning brief as JSON. Attempt to \
break it:

1. Could any listed substitute close this gap in one normal roadmap quarter? If \
yes, the wedge is "temporary", not "structural" -- downgrade it regardless of what \
the draft claims.
2. Does closing the gap genuinely break the substitute's own revenue model or force \
it to abandon a customer segment it currently serves? Only then does "structural" \
hold.
3. Is every claim in wedge_evidence backed by a real source_url, or merely \
asserted? Unsourced claims must be flagged, not silently accepted.
4. Is price_rationale argued against the CHEAPEST substitute in substitute_set, or \
against a more expensive one (which is the exact class of error this whole system \
exists to catch)? Flag it if wrong.

Return ONLY a single JSON object, no markdown fences:
{
  "corrected_wedge_type": "structural"|"temporary",
  "downgrade_reason": str|null,
  "unsourced_claims": [str],
  "price_rationale_flag": str|null,
  "verdict": "pending_review",
  "notes": "one paragraph, plain, for the review log"
}

"verdict" must always be "pending_review" -- you are never able to approve or \
reject outright; the owner decides. Your job is to surface real problems, not to \
make the final call."""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in DIST-P2 output: {text[:300]}")
    return json.loads(match.group(0))


async def validate_wedge(positioning_id: str, supabase_client: Optional[Any] = None) -> dict:
    """Runs DIST-P2 against one draft mse_positioning row. Writes findings
    into wedge_evidence.validation and appends to review_log. Sets
    status='pending_review' -- never 'approved' (structurally impossible
    for this code path to do that even if it tried, see migration 029).
    Returns the updated row."""
    db = supabase_client or get_supabase()

    # Same maybe_single()-returns-bare-None quirk as positioning_researcher.py.
    row = db.table("mse_positioning").select("*").eq("id", positioning_id).maybe_single().execute()
    if row is None or not row.data:
        raise ValueError(f"No mse_positioning row for id {positioning_id!r}")
    brief = row.data

    user_prompt = json.dumps(
        {
            "icp": brief["icp"],
            "trigger_event": brief["trigger_event"],
            "substitute_set": brief["substitute_set"],
            "wedge": brief["wedge"],
            "wedge_type": brief["wedge_type"],
            "wedge_evidence": brief["wedge_evidence"],
            "price_rationale": brief["price_rationale"],
        },
        indent=2,
    )

    raw = analyze(SYSTEM_PROMPT, user_prompt, max_tokens=2000)
    findings = _extract_json(raw)

    wedge_evidence = dict(brief["wedge_evidence"] or {})
    wedge_evidence["validation"] = {
        "corrected_wedge_type": findings["corrected_wedge_type"],
        "downgrade_reason": findings.get("downgrade_reason"),
        "unsourced_claims": findings.get("unsourced_claims", []),
        "price_rationale_flag": findings.get("price_rationale_flag"),
        "reviewer": AGENT_ID,
    }

    review_log = list(brief.get("review_log") or [])
    review_log.append(
        {
            "date": None,
            "reviewer": "DIST-P2",
            "outcome": "pending_review",
            "notes": findings["notes"],
        }
    )

    update = {
        "wedge_type": findings["corrected_wedge_type"],
        "wedge_evidence": wedge_evidence,
        "review_log": review_log,
        "status": "pending_review",
        "moat_risk": findings["corrected_wedge_type"] == "temporary",
    }
    result = db.table("mse_positioning").update(update).eq("id", positioning_id).execute()
    return result.data[0]
