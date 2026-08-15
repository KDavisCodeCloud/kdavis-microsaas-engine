"""
MKT-O2 Cold DM Sequence Writer.

Writes a 2-touch cold DM sequence per lead using exact pain language from
research_report. Framing: "make more money" + a dollar amount — never "save
time." Every sequence lands in mse_dm_sequences with status='pending_hitl'
— this agent never sends anything. A human approves in the HITL queue;
approval unlocks MKT-O5 (agents/marketing/mkt_o5_sequence_sender.py), the
separate sender, which sends via Resend with the CAN-SPAM compliance guard
(core/email_compliance.py — suppression checks, mailing address, one-click
unsubscribe) wired in as of 2026-08-12.

lead_source (2026-08-14): Apollo.io is suspended, so LinkedIn manual
outreach (agents/marketing/mkt_li_intake.py, mse_linkedin_leads) is now
the active first-customer channel alongside it. This writer handles four
sources — "apollo" (unchanged behavior, campaign_build_id required),
"linkedin_manual" (same standard sequence, no campaign_build_id), and
"linkedin_engager" (a lead who already liked/commented on one of Kelvin's
posts — the opener references that interaction directly, a warmer touch
than a cold one). Every source still only ever writes status='pending_hitl'
rows; this agent never sends anything regardless of source.

lead_source="lead_finder" (2026-08-14, agents/marketing/mkt_lead_finder.py):
self-hosted-scraped-and-verified leads (mse_leads). Unlike the LinkedIn
sources, these get real emails auto-sent by MKT-O5 once approved — see
api/routers/outreach.py's approve endpoint for why lead_finder routes to
'approved_hitl' (MKT-O5's actual send trigger) rather than 'approved_manual'.
After a lead_finder sequence is successfully queued, this writer advances
that lead's mse_leads.status from 'pending_dm' to 'pending_email' — a
two-stage lifecycle mse_apollo_leads/mse_linkedin_leads don't have, since
those tables never distinguish "sequence drafted, not yet sent" from
"nothing drafted yet."
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

# linkedin_engager leads already interacted with a real post -- touch_1
# should open by naming that interaction (task spec's own template:
# "Saw your [like/comment] on my post about [topic] — since you're
# [title] at [company], wanted to reach out directly..."), not restart
# cold. Everything else (pain framing, dollar amount, low-friction CTA)
# stays identical to the standard sequence.
_ENGAGER_SYSTEM_PROMPT = f"""You are MKT-O2, writing a 2-touch cold outreach DM sequence for one LinkedIn
lead who already engaged (liked or commented) with one of Kelvin's posts. Return ONLY a single JSON
object — no prose, no markdown fences — matching exactly this schema:

{{
  "touch_1": str,
  "touch_2": str
}}

touch_1 = opening DM, max {TOUCH_1_MAX_CHARS} chars. MUST open by naming their specific interaction, in
this shape: "Saw your [like/comment] on my post about [topic] — since you're [title] at [company], wanted
to reach out directly..." — write it naturally from the real interaction_type/post_topic/title/company
given below, never the literal bracket placeholders.
touch_2 = follow-up sent 3 days later, max {TOUCH_2_MAX_CHARS} chars.

Rules, non-negotiable:
- Frame around "make more money" + a specific dollar amount — never "save time"
- Name a specific pain from the research below, not a generic problem
- End with a low-friction call to action (one question, not a meeting ask)
- No hype words, no generic flattery
- touch_1 leads with the interaction reference then the pain signal; touch_2 leads with the specific
  dollar value prop"""


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


def _write_dm_for_lead(lead: dict, research_context: dict, lead_source: str = "apollo", anthropic_client=None) -> dict:
    safe_lead = DataSanitizationShield.clean({
        "first_name": lead.get("first_name"),
        "title": lead.get("title"),
        "company": lead.get("company"),
    })

    if lead_source == "linkedin_engager":
        system_prompt = _ENGAGER_SYSTEM_PROMPT
        safe_lead["interaction_type"] = DataSanitizationShield.clean(lead.get("interaction_type") or "engaged with")
        safe_lead["post_topic"] = DataSanitizationShield.clean(lead.get("interaction_note") or "a recent post")
    else:
        system_prompt = _SYSTEM_PROMPT

    user_prompt = (
        f"Lead:\n{json.dumps(safe_lead, indent=2)}\n\n"
        f"Pain language and proof signals from research:\n{json.dumps(research_context, indent=2)}\n\n"
        "Write the 2-touch sequence now."
    )
    raw = _analyze(system_prompt, user_prompt, anthropic_client=anthropic_client)
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
    campaign_build_id: Optional[str] = None,
    lead_source: str = "apollo",
    supabase_client: Optional[Any] = None,
    anthropic_client: Optional[Any] = None,
) -> dict:
    """
    Writes a 2-touch cold DM sequence for each lead, all from the same
    lead_source ("apollo" | "linkedin_manual" | "linkedin_engager" |
    "lead_finder"). Never sends anything — every row lands with
    status='pending_hitl'. Raises on any failure — never fails silently.
    Returns {status, sequences_written}.

    campaign_build_id is only meaningful for lead_source="apollo" (every
    apollo lead comes from a MKT-ORCH campaign run) — None for every
    other source, and campaign_builds.dm_sequence_status is only touched
    when a campaign_build_id is actually given.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    _emit_event(db, "dm_sequence_write_started", {
        "product_id": product_id, "campaign_build_id": campaign_build_id,
        "lead_source": lead_source, "lead_count": len(leads),
    })

    research_context = DataSanitizationShield.clean({
        "pain_language": research_report.get("pain_language", []),
        "proof_signals": research_report.get("proof_signals", []),
    })

    rows: list[dict] = []
    try:
        for lead in leads:
            sequence = _write_dm_for_lead(lead, research_context, lead_source=lead_source, anthropic_client=anthropic_client)
            row = {
                "product_id": product_id,
                "campaign_build_id": campaign_build_id,
                "lead_source": lead_source,
                "touch_1": sequence["touch_1"],
                "touch_2": sequence["touch_2"],
            }
            if lead_source == "apollo":
                row["lead_id"] = lead["id"]
            elif lead_source == "lead_finder":
                row["lead_finder_lead_id"] = lead["id"]
            else:
                row["linkedin_lead_id"] = lead["id"]
            rows.append(row)

        if rows:
            insert_result = db.table("mse_dm_sequences").insert(rows).execute()
            if not insert_result.data:
                raise RuntimeError("Insert into mse_dm_sequences returned no data")

        if lead_source == "lead_finder":
            # Two-stage lifecycle unique to mse_leads (see module
            # docstring): a sequence now exists for each of these leads,
            # advance them out of "nothing drafted yet" so MKT-O2's own
            # next run doesn't redraft a sequence that already exists.
            for lead in leads:
                db.table("mse_leads").update({"status": "pending_email"}).eq("id", lead["id"]).execute()

        if campaign_build_id:
            db.table("campaign_builds").update(
                {"dm_sequence_status": "ready_for_hitl"}
            ).eq("id", campaign_build_id).execute()

    except Exception as exc:
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "lead_source": lead_source,
            "error": str(exc), "sequences_written": len(rows),
        })
        if campaign_build_id:
            db.table("campaign_builds").update({"dm_sequence_status": "failed"}).eq("id", campaign_build_id).execute()
        raise RuntimeError(f"MKT-O2 DM sequence write failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {
        "campaign_build_id": campaign_build_id, "lead_source": lead_source, "sequences_written": len(rows),
    })
    _emit_event(db, "dm_sequence_write_completed", {
        "product_id": product_id, "campaign_build_id": campaign_build_id,
        "lead_source": lead_source, "sequences_written": len(rows),
    })

    return {"status": "ready_for_hitl", "sequences_written": len(rows)}


def run_o2_for_linkedin_leads(
    product_id: str,
    research_report: dict,
    supabase_client: Optional[Any] = None,
    anthropic_client: Optional[Any] = None,
) -> dict:
    """
    Entry point for every non-Apollo lead source —
    n8n/linkedin_outreach_workflow.json calls this once per product with
    pending mse_linkedin_leads rows; POST /marketing/linkedin/dm-sequences
    (api/routers/linkedin_intake.py) is its HTTP trigger. Despite the
    name (kept for backward compatibility — this function predates
    lead_finder), it now also processes mse_leads rows from
    agents/marketing/mkt_lead_finder.py (source="lead_finder").

    Priority order: linkedin_engager first (already showed real interest
    by liking/commenting — the warmest lead type), then linkedin_manual,
    then lead_finder last (cold, self-sourced leads — lowest priority of
    the three). lead_finder leads are additionally filtered to
    email_status="verified" — MKT-O2 never drafts a sequence for a lead
    whose email hasn't been confirmed deliverable, since that sequence
    would otherwise sit in mse_dm_sequences with nothing MKT-O5 can safely
    send to. Each source written via run_o2_cold_dm_writer above.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    total_written = 0
    by_source: dict[str, int] = {}
    for source in ("linkedin_engager", "linkedin_manual"):
        leads = (
            db.table("mse_linkedin_leads")
            .select("*")
            .eq("product_id", product_id)
            .eq("status", "pending_dm")
            .eq("source", source)
            .order("created_at")
            .execute()
            .data
            or []
        )
        if not leads:
            by_source[source] = 0
            continue
        result = run_o2_cold_dm_writer(
            product_id=product_id, research_report=research_report, leads=leads,
            campaign_build_id=None, lead_source=source,
            supabase_client=db, anthropic_client=anthropic_client,
        )
        total_written += result["sequences_written"]
        by_source[source] = result["sequences_written"]

    lead_finder_leads = (
        db.table("mse_leads")
        .select("*")
        .eq("product_id", product_id)
        .eq("status", "pending_dm")
        .eq("email_status", "verified")
        .order("created_at")
        .execute()
        .data
        or []
    )
    if lead_finder_leads:
        result = run_o2_cold_dm_writer(
            product_id=product_id, research_report=research_report, leads=lead_finder_leads,
            campaign_build_id=None, lead_source="lead_finder",
            supabase_client=db, anthropic_client=anthropic_client,
        )
        total_written += result["sequences_written"]
        by_source["lead_finder"] = result["sequences_written"]
    else:
        by_source["lead_finder"] = 0

    return {"status": "ready_for_hitl", "sequences_written": total_written, "by_source": by_source}


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
