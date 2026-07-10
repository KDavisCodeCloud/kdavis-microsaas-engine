"""
MKT-O3 Email Sequence Loader.

Writes a 3-email nurture drip sequence for a product's campaign using pain
language, proof signals, and content angles from research_report. Unlike
MKT-O2 (one personalized DM sequence per lead), email is a single bulk
sequence for the whole list — personalization is via {{first_name}} /
{{company}} merge tags, not per-lead generation. select_channels() in
MKT-ORCH always includes "email", so this agent fires on every campaign.

Every sequence lands in mse_email_sequences with status='pending_hitl' —
this agent never sends anything, matching MKT-O2's precedent (and
digest_generator.py's existing precedent: Python generates content, a
human-approved sender handles actual delivery via Resend). A human
approves in the HITL queue; approval unlocks the separate sender, which
doesn't exist yet.
"""

import json
from typing import Any, Optional

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-o3"

SEQUENCE_LENGTH = 3
SUBJECT_MAX_CHARS = 100
BODY_MAX_CHARS = 1500

_SYSTEM_PROMPT = f"""You are MKT-O3, writing a {SEQUENCE_LENGTH}-email nurture drip sequence for one
product's marketing campaign. Return ONLY a single JSON object — no prose, no markdown fences —
matching exactly this schema:

{{
  "emails": [
    {{"subject": str, "body": str, "send_offset_days": int}}
  ]
}}

emails must contain exactly {SEQUENCE_LENGTH} items, ordered by send sequence.
subject = max {SUBJECT_MAX_CHARS} chars. body = max {BODY_MAX_CHARS} chars, plain text (no HTML).
send_offset_days = days after the sequence starts that this email goes out (email 1 is always 0).

Rules, non-negotiable:
- Frame around "make more money" + a specific dollar amount — never "save time"
- Name a specific pain from the research below, not a generic problem
- Email 1 leads with the pain signal. Email 2 leads with a proof signal (real signal from
  research, not fabricated). Email 3 leads with the specific dollar value prop and a low-friction
  call to action (one question, not a meeting ask)
- Use {{{{first_name}}}} and {{{{company}}}} as merge tags where personalization would help — do
  not invent a specific person's name or company
- No hype words, no "I noticed you...", no generic flattery"""


def _analyze(system: str, user: str, anthropic_client=None, max_tokens: int = 2048) -> str:
    if anthropic_client is None:
        return llm_router.analyze(system, user, max_tokens=max_tokens)
    msg = anthropic_client.messages.create(
        model=llm_router.SONNET, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    return raw


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "email_sequence_load",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _generate_sequence(research_report: dict, anthropic_client=None) -> list[dict]:
    research_context = DataSanitizationShield.clean({
        "pain_language": research_report.get("pain_language", []),
        "proof_signals": research_report.get("proof_signals", []),
        "content_angles": research_report.get("content_angles", []),
    })
    user_prompt = f"Research context:\n{json.dumps(research_context, indent=2)}\n\nWrite the {SEQUENCE_LENGTH}-email sequence now."
    raw = _analyze(_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client)
    parsed = json.loads(_strip_fences(raw))
    if not isinstance(parsed, dict) or "emails" not in parsed:
        raise ValueError(f"MKT-O3 expected {{emails: [...]}}, got: {raw[:200]}")

    emails = parsed["emails"]
    if not isinstance(emails, list) or len(emails) != SEQUENCE_LENGTH:
        raise ValueError(f"MKT-O3 expected exactly {SEQUENCE_LENGTH} emails, got {len(emails) if isinstance(emails, list) else type(emails).__name__}")

    return [
        {
            "subject": str(email["subject"])[:SUBJECT_MAX_CHARS],
            "body": str(email["body"])[:BODY_MAX_CHARS],
            "send_offset_days": int(email.get("send_offset_days") or 0),
        }
        for email in emails
    ]


def run_o3_email_sequence_loader(
    product_id: str,
    research_report: dict,
    campaign_build_id: str,
    supabase_client: Optional[Any] = None,
    anthropic_client: Optional[Any] = None,
) -> dict:
    """
    Writes a 3-email nurture sequence for the campaign's product. Never
    sends anything — every row lands with status='pending_hitl'. Raises on
    any failure — never fails silently. Returns {status, emails_written}.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    _emit_event(db, "email_sequence_load_started", {
        "product_id": product_id, "campaign_build_id": campaign_build_id,
    })

    rows: list[dict] = []
    try:
        emails = _generate_sequence(research_report, anthropic_client=anthropic_client)
        rows = [
            {
                "product_id": product_id,
                "campaign_build_id": campaign_build_id,
                "sequence_order": i + 1,
                "send_offset_days": email["send_offset_days"],
                "subject": email["subject"],
                "body": email["body"],
            }
            for i, email in enumerate(emails)
        ]

        insert_result = db.table("mse_email_sequences").insert(rows).execute()
        if not insert_result.data:
            raise RuntimeError("Insert into mse_email_sequences returned no data")

        db.table("campaign_builds").update(
            {"email_sequence_status": "ready_for_hitl"}
        ).eq("id", campaign_build_id).execute()

    except Exception as exc:
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "error": str(exc), "emails_written": len(rows),
        })
        db.table("campaign_builds").update({"email_sequence_status": "failed"}).eq("id", campaign_build_id).execute()
        raise RuntimeError(f"MKT-O3 email sequence load failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {
        "campaign_build_id": campaign_build_id, "emails_written": len(rows),
    })
    _emit_event(db, "email_sequence_load_completed", {
        "product_id": product_id, "campaign_build_id": campaign_build_id, "emails_written": len(rows),
    })

    return {"status": "ready_for_hitl", "emails_written": len(rows)}


def run(research_report: dict, campaign_build: dict) -> dict:
    """
    Adapter for MKT-ORCH's dynamic dispatch. mkt_orch_campaign_orchestrator.py's
    _DOWNSTREAM_AGENTS list references module path
    agents.marketing.mkt_o3_email_sequence_loader — this file's name matches
    exactly, so auto-fan-out finds it (unlike the current MKT-O2 module path
    mismatch flagged in mkt_o2_cold_dm_writer.py).
    """
    return run_o3_email_sequence_loader(
        product_id=campaign_build["product_id"],
        research_report=research_report,
        campaign_build_id=campaign_build["id"],
    )
