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

# Infra-consulting ICP (mse_products.slug='thdagentic-consulting', see
# thd_lead_scout.py's INFRA_CONSULTING_ICP), added 2026-09-16. Genuinely
# different shape from the standard 2-touch sequence above: 3 touches,
# shorter and more direct, a LinkedIn connection-request note as touch_1
# (not a cold DM opener), and no "make more money + dollar amount" framing
# — Kelvin's own spec for this ICP never asked for a dollar figure, and
# CTOs/VPs Eng evaluating infrastructure consulting aren't the same buyer
# psychology as the dollar-value-prop framing was written for.
#
# Career-history grounding: Kelvin's real, documented background is Boeing,
# Honeywell Aerospace, and currently CorVel (kdavis-agentic-platform's own
# MKT-LI1 system prompt: "career history... is texture that proves pattern
# recognition and real-world engineering depth... not his identity or his
# headline"). No specific dollar figure, project, or quantified outcome at
# any of these three companies is documented anywhere in this platform —
# only the real fact that the work was aerospace-grade/regulated-industry
# production infrastructure. The prompt below is deliberately worded to
# reference THAT real credibility signal, not to invent a specific "outcome
# delivered" the way Kelvin's task description phrased it — inventing one
# would violate this platform's own repeated "never fabricate a metric or
# outcome" rule (see e.g. kdavis-agentic-platform's Pillar 5 content rules).
TOUCH_1_INFRA_MAX_CHARS = 300  # LinkedIn's own connection-note character cap
TOUCH_2_INFRA_MAX_CHARS = 500
TOUCH_3_INFRA_MAX_CHARS = 300

_INFRA_CONSULTING_SYSTEM_PROMPT = f"""You are writing a 3-touch LinkedIn cold outreach sequence for Kelvin \
Davis, a senior cloud/platform engineer, targeting a CTO/VP Engineering/Engineering Director/Head of \
Platform/Founder+CTO at a funded startup (20-200 employees) about infrastructure consulting work. Return \
ONLY a single JSON object — no prose, no markdown fences — matching exactly this schema:

{{
  "touch_1": str,
  "touch_2": str,
  "touch_3": str
}}

touch_1 = LinkedIn CONNECTION REQUEST NOTE, max {TOUCH_1_INFRA_MAX_CHARS} chars. Reference something \
specific and real about their company or a real post they made (from the lead/signal context given below \
— never invent a detail not present in it). One sentence on what Kelvin does. No pitch, no ask.

touch_2 = sent 3 days after the connection request is accepted, max {TOUCH_2_INFRA_MAX_CHARS} chars. Lead \
with a specific observation about their infrastructure challenge, inferred ONLY from the real signal given \
(a job posting they're running, their funding stage, or real LinkedIn content) — never invent a challenge \
the signal doesn't support. Then exactly one sentence establishing credibility: Kelvin's background \
includes production infrastructure work in aerospace and other regulated environments (Boeing, Honeywell \
Aerospace) and currently at CorVel — reference this as real professional context, NEVER invent a specific \
dollar figure, project name, or quantified outcome at any of these companies, since none is documented or \
true to claim. End with exactly this soft ask, adapted naturally to fit the message: "Worth a 20-minute \
call?"

touch_3 = sent 5 days after touch_2 ONLY IF there has been no reply, max {TOUCH_3_INFRA_MAX_CHARS} chars. \
One line. A different angle than touch_2 — reference the specific pain point again, briefly. This is the \
final message in the sequence; no further follow-up happens after it, so the ask here is the last one.

Rules, non-negotiable:
- Never use dollar-amount/"make more money" framing — this ICP is not that buyer
- Every specific claim about the company (their hiring, their funding, their content) must come from the \
signal context given below — never invented
- No hype words ("game-changing", "revolutionary"), no generic flattery, no "I noticed you..." as a \
generic opener with nothing specific behind it
- The sequence stops entirely if they reply at any point — touch_3 is only ever sent on zero reply"""


# Cloud Decoded job-signal branch, added 2026-09-25 (agents/marketing/
# mkt_lead_finder.py's run_combined_job_signal_scout /
# thd_lead_scout.CLOUD_DECODED_JOB_SIGNAL_ICP). A company hiring an
# ongoing DevOps/SRE/platform/cloud engineer is signal that Cloud
# Decoded's 11-agent roster can force-multiply that team -- a
# fundamentally different pitch from the infra-consulting sequence above
# (one-off project work, sold as Kelvin's personal time): this is a
# product, sold as a force MULTIPLIER for the hire/team, never framed as
# a reason not to hire. 2-touch EMAIL sequence (not LinkedIn connection
# notes) -- Cloud Decoded has a real signup funnel (theclouddecoded.com)
# an email CTA links to naturally, unlike the consulting ICP's personal-
# brand LinkedIn sale. Explicitly does NOT reuse lead_source=
# "job_posting_signal" (that value is hardcoded above to
# _write_infra_consulting_dm_for_lead's copy) -- a new, separate
# lead_source keeps this from ever silently picking up the wrong prompt.
TOUCH_1_CD_JOB_SIGNAL_MAX_CHARS = 300
TOUCH_2_CD_JOB_SIGNAL_MAX_CHARS = 500

_CLOUD_DECODED_JOB_SIGNAL_SYSTEM_PROMPT = f"""You are writing a 2-touch cold outreach EMAIL sequence for Cloud \
Decoded (theclouddecoded.com), an 11-agent DevOps/platform automation product, to a company you found publicly \
hiring for an ongoing DevOps/SRE/platform/cloud engineer role. Return ONLY a single JSON object — no prose, no \
markdown fences — matching exactly this schema:

{{
  "touch_1": str,
  "touch_2": str
}}

touch_1 = opening email, max {TOUCH_1_CD_JOB_SIGNAL_MAX_CHARS} chars. Reference the SPECIFIC job posting given \
below (the role title, and their stated stack keywords if any are present) — never invent a detail the posting \
context doesn't support. Frame Cloud Decoded as force-multiplication for the team they're building/the person \
they're hiring — it makes that hire (once made) faster and covers gaps day-to-day, NEVER a substitute for making \
the hire, and never imply they shouldn't hire. No pitch beyond one sentence on what Cloud Decoded does.

touch_2 = follow-up sent 3 days later, max {TOUCH_2_CD_JOB_SIGNAL_MAX_CHARS} chars. One concrete detail about how \
Cloud Decoded's agents (CI/CD triage, K8s alert remediation, IAM minimization, FinOps, drift detection — pick \
whichever is most relevant to their stated stack, only from what's given below) helps a team like theirs. End \
with a soft call to action pointing to theclouddecoded.com — a single low-friction next step (e.g. "worth a look \
at theclouddecoded.com?"), never a hard meeting ask.

Rules, non-negotiable:
- Never frame Cloud Decoded as a substitute for hiring, or the posting/role as unnecessary — force-multiplication
  for the hire/team only
- Every specific claim about their stack or the role must come from the job posting context given below — never
  invented
- No hype words ("game-changing", "revolutionary"), no generic flattery, no "I noticed you..." as a generic
  opener with nothing specific behind it
- End touch_2 with the soft CTA to theclouddecoded.com, not a meeting request"""


def _write_cloud_decoded_job_signal_dm_for_lead(lead: dict, anthropic_client=None) -> dict:
    """lead_source='cloud_decoded_job_signal' only. No research_context
    argument, same reasoning as _write_infra_consulting_dm_for_lead: this
    signal (the job posting itself, plus any stack keywords extracted
    from its JD text) already lives on the lead row."""
    safe_lead = DataSanitizationShield.clean({
        "company": lead.get("company"),
        "job_posting_title": lead.get("job_posting_title"),
        "job_posting_url": lead.get("job_posting_url"),
        "job_posting_stack_keywords": lead.get("job_posting_stack_keywords") or [],
    })

    user_prompt = (
        f"Lead + real job posting signal (never invent anything beyond this):\n{json.dumps(safe_lead, indent=2)}\n\n"
        "Write the 2-touch email sequence now."
    )
    raw = _analyze(_CLOUD_DECODED_JOB_SIGNAL_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client, max_tokens=1024)
    parsed = json.loads(_strip_fences(raw))
    if not isinstance(parsed, dict) or "touch_1" not in parsed or "touch_2" not in parsed:
        raise ValueError(f"MKT-O2 (cloud-decoded job signal) expected {{touch_1, touch_2}}, got: {raw[:200]}")

    return {
        "touch_1": str(parsed["touch_1"])[:TOUCH_1_CD_JOB_SIGNAL_MAX_CHARS],
        "touch_2": str(parsed["touch_2"])[:TOUCH_2_CD_JOB_SIGNAL_MAX_CHARS],
    }


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


def _get_selling_stage(db, product_id: str) -> str:
    """Marketing stage-gate update (session 2026-09-15). Mirrors
    mse_icp_configs.selling_stage's own DB default ('building', the most
    conservative option) when no config row exists at all for this
    product, so a product with no ICP config yet never accidentally gets
    treated as active."""
    result = (
        db.table("mse_icp_configs")
        .select("selling_stage")
        .eq("product_id", product_id)
        .maybe_single()
        .execute()
    )
    if result is None or not result.data:
        return "building"
    return result.data.get("selling_stage") or "building"


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


def _write_infra_consulting_dm_for_lead(lead: dict, anthropic_client=None) -> dict:
    """lead_source='job_posting_signal' only (mse_products.slug=
    'thdagentic-consulting') — separate return shape (touch_1/2/3) from
    _write_dm_for_lead's 2-touch contract above, so this never has to be
    force-fit into the standard sequence's schema. No research_context
    argument: this ICP's signal (the job posting itself) already lives on
    the lead row (job_posting_title/job_posting_url), unlike the dollar-
    value-prop sequence's dependency on a separate research_report."""
    safe_lead = DataSanitizationShield.clean({
        "company": lead.get("company"),
        "title": lead.get("title"),
        "job_posting_title": lead.get("job_posting_title"),
        "job_posting_url": lead.get("job_posting_url"),
    })

    user_prompt = (
        f"Lead + real signal context (never invent anything beyond this):\n{json.dumps(safe_lead, indent=2)}\n\n"
        "Write the 3-touch sequence now."
    )
    raw = _analyze(_INFRA_CONSULTING_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client, max_tokens=1024)
    parsed = json.loads(_strip_fences(raw))
    if not isinstance(parsed, dict) or not all(k in parsed for k in ("touch_1", "touch_2", "touch_3")):
        raise ValueError(f"MKT-O2 (infra consulting) expected {{touch_1, touch_2, touch_3}}, got: {raw[:200]}")

    return {
        "touch_1": str(parsed["touch_1"])[:TOUCH_1_INFRA_MAX_CHARS],
        "touch_2": str(parsed["touch_2"])[:TOUCH_2_INFRA_MAX_CHARS],
        "touch_3": str(parsed["touch_3"])[:TOUCH_3_INFRA_MAX_CHARS],
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

    Stage-gated (session 2026-09-15): a product whose mse_icp_configs.
    selling_stage isn't 'active' never gets a sequence written at all —
    no LLM call, no mse_dm_sequences row, nothing to approve. This is the
    real enforcement point for "nothing from a non-active product reaches
    the HITL approval queue": that queue is just mse_dm_sequences WHERE
    status='pending_hitl', read directly by the dashboard's own Supabase
    client (see api/routers/outreach.py's module docstring) — gating the
    write here is what keeps a warming/building product's rows out of it
    in the first place, rather than filtering the read after the fact.
    'warming' products may still get content-mention treatment elsewhere
    in the marketing system; this function only ever handles outreach
    sequences, so 'warming' is gated identically to 'building' here.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    stage = _get_selling_stage(db, product_id)
    if stage != "active":
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "lead_source": lead_source,
            "skipped": "selling_stage_not_active", "selling_stage": stage, "lead_count": len(leads),
        })
        _emit_event(db, "dm_sequence_write_skipped_stage_gate", {
            "product_id": product_id, "selling_stage": stage, "lead_count": len(leads),
        })
        return {"status": "stage_gated", "sequences_written": 0}

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
            if lead_source == "job_posting_signal":
                # Infra-consulting ICP -- 3-touch, no research_report dependency
                # (see _write_infra_consulting_dm_for_lead's own docstring).
                sequence = _write_infra_consulting_dm_for_lead(lead, anthropic_client=anthropic_client)
            elif lead_source == "cloud_decoded_job_signal":
                # Cloud Decoded job-signal ICP -- 2-touch email, force-
                # multiplication framing (see _write_cloud_decoded_job_signal_dm_for_lead).
                sequence = _write_cloud_decoded_job_signal_dm_for_lead(lead, anthropic_client=anthropic_client)
            else:
                sequence = _write_dm_for_lead(lead, research_context, lead_source=lead_source, anthropic_client=anthropic_client)
            row = {
                "product_id": product_id,
                "campaign_build_id": campaign_build_id,
                "lead_source": lead_source,
                "touch_1": sequence["touch_1"],
                "touch_2": sequence["touch_2"],
            }
            if "touch_3" in sequence:
                row["touch_3"] = sequence["touch_3"]
            if lead_source == "apollo":
                row["lead_id"] = lead["id"]
            elif lead_source in ("lead_finder", "job_posting_signal", "cloud_decoded_job_signal"):
                # All three sources live in mse_leads -- same lead-reference
                # column, migrations 20260916000045/20260925000054 didn't
                # need a new one.
                row["lead_finder_lead_id"] = lead["id"]
            else:
                row["linkedin_lead_id"] = lead["id"]
            rows.append(row)

        if rows:
            insert_result = db.table("mse_dm_sequences").insert(rows).execute()
            if not insert_result.data:
                raise RuntimeError("Insert into mse_dm_sequences returned no data")

        if lead_source in ("lead_finder", "job_posting_signal", "cloud_decoded_job_signal"):
            # Two-stage lifecycle unique to mse_leads (see module
            # docstring): a sequence now exists for each of these leads,
            # advance them out of "nothing drafted yet" so MKT-O2's own
            # next run doesn't redraft a sequence that already exists.
            # job_posting_signal and cloud_decoded_job_signal both reuse
            # 'pending_email' despite the name -- there's no separate
            # "drafted, awaiting manual send" status in mse_leads'
            # vocabulary, and nothing auto-acts on this value; the actual
            # send-vs-manual decision is api/routers/outreach.py's approve
            # endpoint routing this sequence's OWN status. Both
            # job_posting_signal and cloud_decoded_job_signal fall to
            # that endpoint's default 'approved_manual' branch today
            # (neither is in its explicit approved_hitl allowlist) --
            # deliberate for cloud_decoded_job_signal too: most job
            # postings don't expose a real contact email, so auto-send
            # would crash on a missing address for most of these leads
            # far more often than it would succeed. An operator sends
            # manually (email if a real one was found, LinkedIn/company
            # site otherwise), same as the consulting branch already does.
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

    # job_posting_signal (infra-consulting ICP, added 2026-09-16) -- deliberately
    # its own query, NOT folded into lead_finder_leads above: these leads are
    # LinkedIn-DM-intended and typically have no verified (or any) email, so
    # lead_finder_leads' email_status='verified' filter would otherwise
    # correctly-but-silently exclude every one of them forever. research_report
    # isn't passed through to the writer for this source (see
    # _write_infra_consulting_dm_for_lead's own docstring for why).
    job_posting_leads = (
        db.table("mse_leads")
        .select("*")
        .eq("product_id", product_id)
        .eq("status", "pending_dm")
        .eq("source", "job_posting_signal")
        .order("created_at")
        .execute()
        .data
        or []
    )
    if job_posting_leads:
        result = run_o2_cold_dm_writer(
            product_id=product_id, research_report=research_report, leads=job_posting_leads,
            campaign_build_id=None, lead_source="job_posting_signal",
            supabase_client=db, anthropic_client=anthropic_client,
        )
        total_written += result["sequences_written"]
        by_source["job_posting_signal"] = result["sequences_written"]
    else:
        by_source["job_posting_signal"] = 0

    # cloud_decoded_job_signal (added 2026-09-25) -- same reasoning as the
    # job_posting_signal block above: a dedicated query rather than
    # folding into lead_finder_leads, since these leads typically have no
    # verified (or any) email either and that filter would otherwise
    # silently exclude every one of them forever.
    cloud_decoded_job_signal_leads = (
        db.table("mse_leads")
        .select("*")
        .eq("product_id", product_id)
        .eq("status", "pending_dm")
        .eq("source", "cloud_decoded_job_signal")
        .order("created_at")
        .execute()
        .data
        or []
    )
    if cloud_decoded_job_signal_leads:
        result = run_o2_cold_dm_writer(
            product_id=product_id, research_report=research_report, leads=cloud_decoded_job_signal_leads,
            campaign_build_id=None, lead_source="cloud_decoded_job_signal",
            supabase_client=db, anthropic_client=anthropic_client,
        )
        total_written += result["sequences_written"]
        by_source["cloud_decoded_job_signal"] = result["sequences_written"]
    else:
        by_source["cloud_decoded_job_signal"] = 0

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
