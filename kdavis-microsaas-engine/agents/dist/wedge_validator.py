"""
DIST-P2 — Wedge Validator. Adversarial review of a DIST-P1 draft. Never
approves -- can only move a row to 'pending_review' (owner decides from
there) or leave/mark it in a way that signals rejection. The one write
this agent is structurally forbidden from making (status='approved') is
enforced at the database level regardless of this code (migration 029's
reject_direct_approval trigger + approve_positioning()'s owner-only
SECURITY DEFINER gate) -- this module doesn't rely on its own discipline
to hold that line.

Three-tier taxonomy (migration 038, 2026-08-31): the original 2-tier test
(structural pass / temporary fail) rejected every real product submitted
to it -- a filter that rejects everything is miscalibrated, not doing its
job. structural is a moat test; moats matter at $10K MRR and exit, not at
the ~33-40-customer $4K first bar. This widens the PASS condition to add
'execution' (they could close the gap and demonstrably have not -- the
buyer is underserved today, sourced) without touching the FAIL condition:
an unsourced or roadmap-imminent gap is still 'temporary' and still blocks
generation exactly as before. Q1-Q3 (the original structural test) are
unchanged; Q4 only ever moves a Q2-failing brief between execution and
temporary, it can never turn a temporary into a structural.

Fourth tier, 'invalid' (migration 039, 2026-08-31): proven necessary by
small-portfolio-hub v4, which passed Q1-Q4 (correctly structural on its own
terms) while resting on a wedge -- "tenants pay $0 with us" -- that a
substitute (Innago, TurboTenant) already offered as a configurable setting.
That's not "temporary" (they could ship it); they'd already shipped it. Q0
runs before Q1 and, when it fires, short-circuits straight to 'invalid' --
a distinct, more severe outcome than temporary, and one approve_positioning()
now blocks outright with no override path (unlike the margin_floor gate,
which allows an explicit override). Q0 firing is a fact already true today,
not a risk to weigh.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Optional

from core.llm_router import analyze_with_web_search
from core.supabase_client import get_supabase

AGENT_ID = "dist-p2-wedge-validator"

# 2 quarters, per A.1's mandatory re-review requirement on 'execution'-tier
# rows -- a bet that a substitute keeps not shipping needs a recheck date.
_EXECUTION_REVIEW_INTERVAL_DAYS = 182

SYSTEM_PROMPT = """You are an adversarial reviewer whose only job is to try to \
defeat a proposed product "wedge" (the thing substitute products structurally \
cannot do, or demonstrably have not done). You are given a DIST-P1 draft \
positioning brief as JSON. Attempt to break it by answering these questions, in \
order, with real web search -- do not answer from memory alone:

Q0 (ask FIRST, before Q1): Does any substitute in substitute_set ALREADY offer this \
-- including as a configurable option, a settings toggle, or a specific tier -- right \
now, today? Check each entry's own configurable_options field first (DIST-P1 is now \
required to research this), then verify independently with your own search rather \
than trust it blindly. This is a different question from Q1: Q1 asks whether a \
substitute COULD close the gap; Q0 asks whether one already HAS. If Q0 finds a real, \
sourced yes, the wedge is "invalid", not "temporary" -- stop, do not proceed to \
Q1-Q4, and name the substitute and cite the source. A wedge whose entire claim is \
"substitute X must impose cost Y on the buyer" is invalid, not merely beatable, once \
X can already be configured not to. Real example this caught, 2026-08-31: \
small-portfolio-hub's "tenants pay $0 with us" wedge was invalid because Innago and \
TurboTenant both already let a landlord absorb the tenant's ACH fee via account \
settings -- that wasn't "temporary" (something they could ship), it was already true.

1. Could any listed substitute close this gap in one normal roadmap quarter? If \
yes, this is NOT structural -- fall through to Q4 to determine execution vs \
temporary.
2. Does closing the gap genuinely break the substitute's own revenue model or force \
it to abandon a customer segment it currently serves? If yes, wedge_type is \
"structural" regardless of Q4 -- stop here, Q4 does not apply.
3. Is every claim in wedge_evidence backed by a real source_url, or merely \
asserted? Unsourced claims must be flagged, not silently accepted, regardless of \
which tier this resolves to.
4. Only asked when Q2 fails (the substitute COULD close the gap): is there sourced \
evidence the substitute has had the opportunity to close this gap and chosen not \
to? Concrete evidence only -- shipped-feature history, a public roadmap, stated \
positioning, or years in market without addressing it. Search for the substitute's \
actual changelog/roadmap/release notes; do not infer inaction from absence of a \
search result. ABSENCE OF EVIDENCE IS NOT EVIDENCE -- an unsourced or unresearched \
answer here MUST resolve to "temporary", never "execution". A substitute actively \
shipping comparable features on a normal cadence (e.g. ~6-week release cycles) is \
"temporary" even if today's snapshot doesn't have the exact feature yet -- they are \
actively closing it, not sitting on it.

price_rationale check, independent of the above: is price_rationale argued against \
the CHEAPEST substitute in substitute_set, or against a more expensive one (the \
exact class of error this whole system exists to catch)? Flag it if wrong.

Return ONLY a single JSON object, no markdown fences:
{
  "q0": {"already_offered": bool, "substitute": str|null, "source_url": str|null, "verified_at": str|null},
  "corrected_wedge_type": "structural"|"execution"|"invalid"|"temporary",
  "downgrade_reason": str|null,
  "unsourced_claims": [str],
  "price_rationale_flag": str|null,
  "q4_evidence": [{"claim": str, "source_url": str, "verified_at": "YYYY-MM-DD"}],
  "verdict": "pending_review",
  "notes": "one paragraph, plain, for the review log -- state your Q0 answer first, then Q1-Q4 if Q0 didn't already resolve this"
}

q0.already_offered = true FORCES corrected_wedge_type = "invalid" regardless of what \
Q1-Q4 would otherwise conclude, and REQUIRES q0.substitute and q0.source_url to be \
non-null. q0.already_offered = false means state that explicitly (do not leave it \
null); q0.substitute and q0.source_url are null in that case.

q4_evidence MUST be a non-empty array with at least one real source_url whenever \
corrected_wedge_type is "execution", and MUST be an empty array otherwise -- do not \
populate it for structural, invalid, or temporary verdicts, and never claim \
"execution" without it.

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
    """Runs DIST-P2 against one draft/pending mse_positioning row. Writes
    findings into wedge_evidence.validation (+ wedge_evidence.q4_evidence
    for an execution verdict) and appends to review_log. Sets
    status='pending_review' -- never 'approved' (structurally impossible
    for this code path to do that even if it tried, see migration 029).

    Raises ValueError, and writes nothing, if the model returns an
    'execution' verdict with no sourced q4_evidence -- absence of evidence
    is not evidence, per A.2; a bare LLM claim of "execution" is not
    sufficient to unlock full surface generation. Returns the updated row
    on success."""
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

    raw = analyze_with_web_search(SYSTEM_PROMPT, user_prompt, max_uses=12, max_tokens=6000)
    findings = _extract_json(raw)

    q0 = findings.get("q0") or {}
    q0_fired = bool(q0.get("already_offered"))
    corrected_wedge_type = findings["corrected_wedge_type"]
    q4_evidence = findings.get("q4_evidence") or []

    if q0_fired:
        if not q0.get("substitute") or not q0.get("source_url"):
            raise ValueError(
                f"DIST-P2 rejected Q0 verdict for positioning {positioning_id!r}: "
                "already_offered=true requires both substitute and source_url. "
                "Nothing was written."
            )
        if corrected_wedge_type != "invalid":
            raise ValueError(
                f"DIST-P2 rejected findings for positioning {positioning_id!r}: "
                f"q0.already_offered=true but corrected_wedge_type={corrected_wedge_type!r}, "
                "must be 'invalid'. Nothing was written."
            )

    if corrected_wedge_type == "execution" and len(q4_evidence) == 0:
        raise ValueError(
            f"DIST-P2 rejected 'execution' verdict for positioning {positioning_id!r}: "
            "no sourced q4_evidence provided. Absence of evidence is not evidence -- "
            "this resolves to 'temporary', not 'execution'. Nothing was written."
        )

    wedge_evidence = dict(brief["wedge_evidence"] or {})
    wedge_evidence["validation"] = {
        "corrected_wedge_type": corrected_wedge_type,
        "downgrade_reason": findings.get("downgrade_reason"),
        "unsourced_claims": findings.get("unsourced_claims", []),
        "price_rationale_flag": findings.get("price_rationale_flag"),
        "reviewer": AGENT_ID,
    }
    wedge_evidence["q0"] = {
        "already_offered": q0_fired,
        "substitute": q0.get("substitute"),
        "source_url": q0.get("source_url"),
        "verified_at": q0.get("verified_at"),
    }
    if corrected_wedge_type == "execution":
        wedge_evidence["q4_evidence"] = q4_evidence
    else:
        wedge_evidence.pop("q4_evidence", None)

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
        "wedge_type": corrected_wedge_type,
        "wedge_evidence": wedge_evidence,
        "review_log": review_log,
        "status": "pending_review",
        "moat_risk": corrected_wedge_type != "structural",
        "wedge_review_status": "current",
        "next_review_due": (
            (date.today() + timedelta(days=_EXECUTION_REVIEW_INTERVAL_DAYS)).isoformat()
            if corrected_wedge_type == "execution"
            else None
        ),
    }
    result = db.table("mse_positioning").update(update).eq("id", positioning_id).execute()
    return result.data[0]
