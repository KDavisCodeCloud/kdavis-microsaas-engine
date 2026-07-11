"""
MKT-PR1 Proof Collector.

Drafts a case-study/testimonial request message for leads that replied
to cold outreach. Never sends anything — output is draft text a human
reviews and sends manually.

Schema note (checked before writing —
supabase/migrations/20260709000006_outreach_engine.sql):
mse_apollo_leads.status only supports
('pending', 'dm_sent', 'replied', 'converted') — there is no
'replied_positive' value, and no sentiment field exists anywhere in the
outreach schema to distinguish a positive reply from any other kind.
Treats every status='replied' lead as a case-study candidate rather than
silently filtering on a status string that will never match anything in
the live DB. A real positive/negative reply classifier would need a new
column this migration doesn't have — flagged, not invented here.
"""

import json
import logging
from typing import Any, Optional

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

AGENT_ID = "mkt-pr1"
MAX_DRAFT_CHARS = 800

_SYSTEM_PROMPT = """You are MKT-PR1, drafting a short case-study/testimonial request
message to a lead who replied to cold outreach. Return ONLY the message text — no
JSON, no preamble, no markdown fences.

Rules:
- Warm and specific — reference their company/role if given, not generic
- One clear ask: a short case study or testimonial
- Offer something of comparable value in return (early access, a discount, a
  feature request line) without inventing a specific number or program that
  doesn't exist
- No hard sell, no multiple asks
- Under 120 words"""


def _analyze(system: str, user: str, anthropic_client=None, max_tokens: int = 400) -> str:
    if anthropic_client is None:
        return llm_router.analyze(system, user, max_tokens=max_tokens)
    msg = anthropic_client.messages.create(
        model=llm_router.SONNET, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "proof_collection",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _draft_for_lead(lead: dict, anthropic_client=None) -> str:
    safe_lead = DataSanitizationShield.clean({
        "first_name": lead.get("first_name"),
        "company": lead.get("company"),
        "title": lead.get("title"),
    })
    user_prompt = f"Lead who replied to outreach:\n{json.dumps(safe_lead, indent=2)}\n\nDraft the case-study request now."
    raw = _analyze(_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client)
    return raw.strip()[:MAX_DRAFT_CHARS]


def run_pr1_proof_collector(
    product_id: str,
    supabase_client: Optional[Any] = None,
    anthropic_client: Optional[Any] = None,
) -> dict:
    """
    Drafts a case-study request for every replied lead on a product.
    Raises on any failure — never fails silently. Returns
    {"case_study_requests": [{"lead_id", "draft_message"}]}.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    try:
        result = (
            db.table("mse_apollo_leads")
            .select("*")
            .eq("product_id", product_id)
            .eq("status", "replied")
            .execute()
        )
        leads = result.data or []

        case_study_requests: list[dict] = []
        for lead in leads:
            draft_message = _draft_for_lead(lead, anthropic_client=anthropic_client)
            case_study_requests.append({"lead_id": lead["id"], "draft_message": draft_message})

        _write_audit(db, "win", product_id, {"case_study_requests": len(case_study_requests)})
        _emit_event(db, "proof_collection_completed", {
            "product_id": product_id, "case_study_requests": len(case_study_requests),
        })

        return {"case_study_requests": case_study_requests}

    except Exception as exc:
        _write_audit(db, "lose", product_id, {"error": str(exc)})
        raise RuntimeError(f"MKT-PR1 proof collector failed for product {product_id}: {exc}") from exc
