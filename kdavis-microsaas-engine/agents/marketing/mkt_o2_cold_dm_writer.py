"""
MKT-O2 Cold DM Sequence Writer.

Writes a 2-touch cold DM sequence per lead using exact pain language from
research_report. Framing: "make more money" + a dollar amount — never "save
time." Every sequence lands in mse_dm_sequences with status='pending_hitl'
— this agent never sends anything. A human approves in the HITL queue;
approval unlocks the separate sender, which doesn't exist yet.
"""

import json
from typing import Any, Optional

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-o2"

TOUCH_1_MAX_CHARS = 300
TOUCH_2_MAX_CHARS = 500

_SYSTEM_PROMPT = f"""You are MKT-O2, writing a 2-touch cold outreach DM sequence for one lead.
Return ONLY a single JSON object — no prose, no markdown fences — matching exactly this schema:

{{
  "touch_1": str,
  "touch_2": str
}}

touch_1 = connection request note or cold DM opener, max {TOUCH_1_MAX_CHARS} chars.
touch_2 = follow-up sent 3 days later, max {TOUCH_2_MAX_CHARS} chars.

Rules, non-negotiable:
- Frame around "make more money" + a specific dollar amount — never "save time"
- Name a specific pain from the research below, not a generic problem
- End with a low-friction call to action (one question, not a meeting ask)
- No hype words, no "I noticed you...", no generic flattery
- touch_1 leads with the pain signal; touch_2 leads with the specific dollar value prop"""


def _analyze(system: str, user: str, anthropic_client=None, max_tokens: int = 1024) -> str:
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
        "action": "dm_sequence_write",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _write_dm_for_lead(lead: dict, research_context: dict, anthropic_client=None) -> dict:
    safe_lead = DataSanitizationShield.clean({
        "first_name": lead.get("first_name"),
        "title": lead.get("title"),
        "company": lead.get("company"),
    })
    user_prompt = (
        f"Lead:\n{json.dumps(safe_lead, indent=2)}\n\n"
        f"Pain language and proof signals from research:\n{json.dumps(research_context, indent=2)}\n\n"
        "Write the 2-touch sequence now."
    )
    raw = _analyze(_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client)
    parsed = json.loads(_strip_fences(raw))
    if not isinstance(parsed, dict) or "touch_1" not in parsed or "touch_2" not in parsed:
        raise ValueError(f"MKT-O2 expected {{touch_1, touch_2}}, got: {raw[:200]}")

    return {
        "touch_1": str(parsed["touch_1"])[:TOUCH_1_MAX_CHARS],
        "touch_2": str(parsed["touch_2"])[:TOUCH_2_MAX_CHARS],
    }


def run_o2_cold_dm_writer(
    product_id: str,
    research_report: dict,
    leads: list[dict],
    campaign_build_id: str,
    supabase_client: Optional[Any] = None,
    anthropic_client: Optional[Any] = None,
) -> dict:
    """
    Writes a 2-touch cold DM sequence for each lead. Never sends anything —
    every row lands with status='pending_hitl'. Raises on any failure —
    never fails silently. Returns {status, sequences_written}.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    _emit_event(db, "dm_sequence_write_started", {
        "product_id": product_id, "campaign_build_id": campaign_build_id, "lead_count": len(leads),
    })

    research_context = DataSanitizationShield.clean({
        "pain_language": research_report.get("pain_language", []),
        "proof_signals": research_report.get("proof_signals", []),
    })

    rows: list[dict] = []
    try:
        for lead in leads:
            sequence = _write_dm_for_lead(lead, research_context, anthropic_client=anthropic_client)
            rows.append({
                "lead_id": lead["id"],
                "product_id": product_id,
                "campaign_build_id": campaign_build_id,
                "touch_1": sequence["touch_1"],
                "touch_2": sequence["touch_2"],
            })

        if rows:
            insert_result = db.table("mse_dm_sequences").insert(rows).execute()
            if not insert_result.data:
                raise RuntimeError("Insert into mse_dm_sequences returned no data")

        db.table("campaign_builds").update(
            {"dm_sequence_status": "ready_for_hitl"}
        ).eq("id", campaign_build_id).execute()

    except Exception as exc:
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "error": str(exc), "sequences_written": len(rows),
        })
        db.table("campaign_builds").update({"dm_sequence_status": "failed"}).eq("id", campaign_build_id).execute()
        raise RuntimeError(f"MKT-O2 DM sequence write failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {
        "campaign_build_id": campaign_build_id, "sequences_written": len(rows),
    })
    _emit_event(db, "dm_sequence_write_completed", {
        "product_id": product_id, "campaign_build_id": campaign_build_id, "sequences_written": len(rows),
    })

    return {"status": "ready_for_hitl", "sequences_written": len(rows)}


def run(research_report: dict, campaign_build: dict) -> dict:
    """
    Adapter for MKT-ORCH's dynamic dispatch. NOTE: mkt_orch_campaign_orchestrator.py's
    _DOWNSTREAM_AGENTS list currently references the module path
    agents.marketing.mkt_o2_cold_dm_sequence_writer — this file is named
    mkt_o2_cold_dm_writer.py per this session's explicit task spec, so the
    orchestrator's auto-fan-out will not find this module under that path
    until one side is reconciled. Flagged in the session report; not fixed
    here since mkt_orch is another session's file and wasn't in this
    session's declared scope.
    """
    db = get_supabase()
    leads_result = (
        db.table("mse_apollo_leads")
        .select("*")
        .eq("campaign_build_id", campaign_build["id"])
        .execute()
    )
    return run_o2_cold_dm_writer(
        product_id=campaign_build["product_id"],
        research_report=research_report,
        leads=leads_result.data or [],
        campaign_build_id=campaign_build["id"],
    )
