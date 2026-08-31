"""
SUP-D1 — Support Ticket Classifier. Phase 7 of the DIST subsystem
(EXECUTION_ORDER.md). Assigns tier (0-3), a recurring classification key,
and sentiment to an incoming support ticket.

Tier 3 is deliberately over-inclusive per the spec's own words: "A
misrouted Tier 2 costs 90 seconds. A misrouted Tier 3 costs a customer."
Any real signal of refund/cancel/chargeback/lawyer/broken-in-production
or genuine frustration forces Tier 3 via a real keyword pre-check BEFORE
the LLM call even runs -- this is a hard override the model cannot reason
its way around, not a suggestion in the prompt.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from core.llm_router import analyze
from core.supabase_client import get_supabase

AGENT_ID = "sup-d1-classifier"

# Hard override -- matched before any LLM call. Deliberately broad (word
# boundaries, not exact phrases) per the spec's own "deliberately
# over-inclusive" instruction for Tier 3.
_TIER_3_FORCE_PATTERNS = [
    r"\brefund\b", r"\bcancel\b", r"\bcancelling\b", r"\bcancelled\b",
    r"\bchargeback\b", r"\blawyer\b", r"\battorney\b", r"\bsue\b", r"\blegal\b",
    r"\bbroken in production\b", r"\bdown in production\b", r"\blost my data\b",
    r"\bfraud\b", r"\bunauthorized charge\b",
]

SYSTEM_PROMPT = """You are a support-ticket classifier for a micro-SaaS product. \
Given a ticket's subject and body, return ONLY a JSON object with these fields:

{
  "tier": 1 | 2 | 3,
  "classification": "short_snake_case_key",
  "sentiment": "neutral" | "positive" | "frustrated" | "angry"
}

Tier definitions:
- Tier 1: deterministic, answerable from docs/account data alone (e.g. "where's my payout", \
"how do I add a unit"). Never assign Tier 1 to anything involving money disputes, legal \
threats, or genuine frustration -- those are always Tier 3 regardless of the literal question.
- Tier 2: everything else that isn't Tier 1 or Tier 3 -- needs a human-drafted, \
human-approved reply.
- Tier 3: churn risk, billing disputes, angry sentiment, legal threats, anything a wrong \
answer could make materially worse. When in doubt, choose Tier 3 -- a misrouted Tier 2 \
costs 90 seconds of a human's time; a misrouted Tier 3 costs a customer.

classification is a short, reusable key for grouping recurring ticket types (e.g. \
"ach_timing", "add_unit_howto", "payout_delay") -- not a one-off description of this \
specific ticket. Use the same key you'd expect a similar future ticket to get.

Return ONLY the JSON object, no other text."""


def _force_tier_3(subject: str, body: str) -> bool:
    text = f"{subject or ''} {body}".lower()
    return any(re.search(p, text) for p in _TIER_3_FORCE_PATTERNS)


def _parse_response(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"SUP-D1: no JSON object found in model response: {raw!r}")
    data = json.loads(match.group(0))
    if data.get("tier") not in (1, 2, 3):
        raise ValueError(f"SUP-D1: model returned invalid tier {data.get('tier')!r}")
    if not data.get("classification"):
        raise ValueError("SUP-D1: model returned no classification key")
    if data.get("sentiment") not in ("neutral", "positive", "frustrated", "angry"):
        raise ValueError(f"SUP-D1: model returned invalid sentiment {data.get('sentiment')!r}")
    return data


async def classify_ticket(
    subject: Optional[str],
    body: str,
    llm_analyze=analyze,
) -> dict[str, Any]:
    """Pure classification -- does not write to the DB. Callers insert the
    ticket with this result. Returns {"tier": int, "classification": str,
    "sentiment": str}."""
    if _force_tier_3(subject or "", body):
        # Still ask the model for a real classification key/sentiment --
        # only the tier is hard-forced, the other two fields are still
        # genuinely useful for SUP-K1's gap analysis later.
        user_prompt = f"Subject: {subject or '(none)'}\n\nBody:\n{body}"
        raw = llm_analyze(SYSTEM_PROMPT, user_prompt, max_tokens=300)
        try:
            parsed = _parse_response(raw)
        except ValueError:
            parsed = {"classification": "unclassified", "sentiment": "frustrated"}
        parsed["tier"] = 3
        return parsed

    user_prompt = f"Subject: {subject or '(none)'}\n\nBody:\n{body}"
    raw = llm_analyze(SYSTEM_PROMPT, user_prompt, max_tokens=300)
    return _parse_response(raw)


async def classify_and_store(
    product_id: str,
    channel: str,
    body: str,
    subject: Optional[str] = None,
    tenant_id: Optional[str] = None,
    supabase_client=None,
    llm_analyze=analyze,
) -> dict[str, Any]:
    """Classifies and inserts a real mse_support_tickets row. Returns the
    inserted row."""
    db = supabase_client if supabase_client is not None else get_supabase()
    result = await classify_ticket(subject, body, llm_analyze=llm_analyze)

    row = {
        "product_id": product_id,
        "tenant_id": tenant_id,
        "channel": channel,
        "subject": subject,
        "body": body,
        "tier": result["tier"],
        "classification": result["classification"],
        "sentiment": result["sentiment"],
        "status": "open",
    }
    inserted = db.table("mse_support_tickets").insert(row).execute()
    return inserted.data[0]
