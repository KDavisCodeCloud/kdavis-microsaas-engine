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
import logging
from typing import Any, Optional

import core.llm_router as llm_router
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-o2"

_log = logging.getLogger(__name__)

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

# Channel/offer composition moved to agents/marketing/outreach_copy.py
# (decision 1, 2026-10-06). The two prompts that used to live here were
# selected by lead_source and had the channel welded in, which is how
# LinkedIn-shaped copy ended up being emailed. The URLs are re-exported from
# there so there is exactly ONE definition of each.
from agents.marketing.outreach_copy import (  # noqa: E402
    CLOUD_DECODED_DEMO_URL,
    CONSULTING_OFFER_URL,
)

# The three lead_source values whose leads live in mse_leads and therefore
# carry contact_status / title / open_role_count.
_MSE_LEADS_SOURCES = ("lead_finder", "job_posting_signal", "cloud_decoded_job_signal")


_TERMINAL_SEQUENCE_STATUSES = ("rejected_hitl", "sequence_complete", "suppressed", "approval_expired")


def _existing_sequences_by_lead(db, leads: list[dict]) -> dict[str, dict]:
    """lead_id -> its most relevant live sequence, for the leads given.

    Terminal statuses are excluded: a rejected or completed sequence must not
    block a fresh draft later. An 'awaiting_contact' row is returned so the
    caller can regenerate it in place instead of inserting a duplicate.
    """
    ids = [l.get("id") for l in leads if l.get("id")]
    if not ids:
        return {}
    rows = (db.table("mse_dm_sequences")
            .select("id,status,lead_finder_lead_id,drafted_for_route")
            .in_("lead_finder_lead_id", ids).execute().data or [])
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("status") in _TERMINAL_SEQUENCE_STATUSES:
            continue
        lid = r.get("lead_finder_lead_id")
        if not lid:
            continue
        # Prefer a non-awaiting row, so an existing live draft wins over a
        # parked one and the lead is skipped rather than duplicated.
        if lid not in out or (out[lid]["status"] == "awaiting_contact"
                              and r["status"] != "awaiting_contact"):
            out[lid] = {"id": r["id"], "status": r["status"],
                        "drafted_for_route": r.get("drafted_for_route")}
    return out


def contact_is_draftable(lead: dict) -> tuple[bool, str]:
    """(ok, reason) -- may MKT-O2 spend a draft and a human's review on this?

    TWO conditions, because 'found' was never strong enough on its own:
    contact_status='found' only ever meant "a name was discovered", which is
    why six sequences reached the approval queue addressed to a VP of
    Marketing, two COOs, and a bare "Director". The title must ALSO clear
    contact_fit -- the same rules already applied at discovery time, now
    applied at draft time too.

    A failure here is never a judgement about the company. Stage 1 already
    qualified it; only the contact is wrong, so the lead returns to the
    Find-the-Buyer lane rather than being dropped.
    """
    from agents.marketing.contact_fit import evaluate_contact_fit

    status = (lead.get("contact_status") or "").strip()
    if status != "found":
        return False, f"contact_status={status or 'unset'!r}, need 'found'"

    first = (lead.get("first_name") or "").strip()
    title = (lead.get("title") or "").strip()
    if not first:
        return False, "no first name: the copy addresses the contact by first name"
    if not title:
        return False, "no title: contact fit cannot be assessed without one"

    fit = evaluate_contact_fit(title, open_role_count=lead.get("open_role_count"))
    if not fit.accepted:
        return False, f"contact fit rejected -- {fit.reason}"
    return True, f"ok: {fit.reason}"



def _trim_copy(text: Any, limit: int, *, label: str = "touch") -> str:
    """Enforce a length cap WITHOUT cutting a word, sentence or URL in half.

    The previous `str(x)[:limit]` produced prospect-facing copy ending
    "...an experienced c" and "Worth a 20-minu" the moment the 2026-10-05
    rules made drafts longer (the added first-person credibility line plus a
    URL pushed consulting touch_2 past 500 chars). A draft that ends mid-word
    reads as a broken system, which is worse than one that ends early.

    Trims to the last sentence end inside the limit; falls back to the last
    word boundary. Logs when it trims, because copy that regularly needs
    trimming means the prompt and the cap disagree and a human should know.
    """
    text = (str(text) if text is not None else "").strip()
    if len(text) <= limit:
        return text

    window = text[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "),
              window.rfind("\n"))
    if cut >= limit * 0.5:
        trimmed = window[:cut + 1].strip()
    else:
        space = window.rfind(" ")
        trimmed = (window[:space] if space > 0 else window).strip()
    _log.warning("[MKT-O2] %s exceeded %d chars (%d); trimmed at a boundary",
                 label, limit, len(text))
    return trimmed


def _ensure_close_url(text: str, url: str, limit: int) -> str:
    """Guarantee the closing touch carries its destination.

    The prompt asks for the URL, but a prompt is a request: across three
    regenerations the model included it for consulting and then omitted it for
    GoReel's Cloud Decoded touch_2, leaving a pitch with nowhere to go. The
    link is the one element that must not depend on model compliance, so it is
    appended deterministically when absent.

    Appending respects the cap by trimming the BODY first, never the URL --
    a half-written URL is worse than a shorter message.
    """
    if not url or not text:
        return text
    if url in text:
        return text
    suffix = f"\n\n{url}"
    room = limit - len(suffix)
    if room <= 0:
        return text
    return _trim_copy(text, room, label="close body").rstrip() + suffix


# ── Route-aware writer (Kelvin's decision 1, 2026-10-06) ─────────────────

def _write_route_aware_sequence(lead: dict, product_id: str, anthropic_client=None) -> dict:
    """Generate a sequence shaped by the lead's ROUTE, with the product's offer.

    Replaces the two prompts that were selected by lead_source. lead_source is
    provenance; the channel is lead_route, and the old coupling meant
    consulting leads got a LinkedIn-shaped sequence that MKT-O5 then emailed
    verbatim.

    Returns the touches, plus `subject` on the email route and
    `drafted_for_route` so a later route change is detectable.
    """
    from agents.marketing.mkt_lead_finder import CLOUD_DECODED_PRODUCT_ID
    from agents.marketing.outreach_copy import (
        CLOUD_DECODED_OFFER,
        CONSULTING_OFFER,
        build_system_prompt,
        channel_for_route,
        channel_violations,
    )

    route = (lead.get("lead_route") or "").strip()
    channel = channel_for_route(route)
    offer = CLOUD_DECODED_OFFER if product_id == CLOUD_DECODED_PRODUCT_ID else CONSULTING_OFFER
    system = build_system_prompt(channel, offer)

    safe_lead = DataSanitizationShield.clean({
        # first_name is REQUIRED: the shared rules demand a first-name
        # greeting, and an instruction the context cannot satisfy produces
        # "Hi --".
        "first_name": lead.get("first_name"),
        "company": lead.get("company"),
        "title": lead.get("title"),
        "job_posting_title": lead.get("job_posting_title"),
        "job_posting_url": lead.get("job_posting_url"),
        "job_posting_stack_keywords": lead.get("job_posting_stack_keywords") or [],
    })
    user_prompt = (
        f"Lead + real signal context (never invent anything beyond this):\n"
        f"{json.dumps(safe_lead, indent=2)}\n\n"
        f"Write the sequence as the JSON object described."
    )

    raw = _analyze(system, user_prompt, anthropic_client=anthropic_client, max_tokens=1600)
    parsed = json.loads(_strip_fences(raw))
    required = (["subject"] if channel.wants_subject else []) + list(channel.touches)
    missing = [k for k in required if k not in parsed or not str(parsed.get(k) or "").strip()]
    if missing:
        raise ValueError(f"MKT-O2 ({channel.route}) missing {missing} -- got: {raw[:200]}")

    out: dict = {"drafted_for_route": channel.route}
    for key in required:
        limit = channel.limits.get(key, 900)
        value = _trim_copy(parsed[key], limit, label=f"{channel.route} {key}")
        out[key] = value

    # The close URL must survive on the touch that carries the ask.
    out["touch_2"] = _ensure_close_url(
        out["touch_2"], offer.close_url, channel.limits.get("touch_2", 900))

    # A generation that drifted across channels is caught here, not shipped.
    # Raising rather than scrubbing: the wrong channel's vocabulary usually
    # means the whole message is framed for the wrong channel, and a
    # find-and-replace would leave incoherent copy behind.
    drift = []
    for key in required:
        drift += [f"{key}: {v}" for v in channel_violations(out[key], channel.route)]
    if drift:
        raise ValueError(
            f"MKT-O2 produced {channel.route} copy with the other channel's phrasing: {drift}")

    return out


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
        "touch_1": _trim_copy(parsed["touch_1"], TOUCH_1_MAX_CHARS, label="touch_1"),
        "touch_2": _trim_copy(parsed["touch_2"], TOUCH_2_MAX_CHARS, label="touch_2"),
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

    # CONTACT-FIRST GATE (Kelvin's decision 2, 2026-10-05). A draft addressed
    # to the wrong person wastes the only scarce thing in this pipeline -- a
    # human's review attention -- and six of eleven pending sequences were
    # exactly that. Company-only leads are NOT failures: they belong in the
    # Find-the-Buyer lane until a buyer is named, and they come back here
    # automatically afterwards.
    draftable, parked = [], []
    existing_seq = _existing_sequences_by_lead(db, leads) if lead_source in _MSE_LEADS_SOURCES else {}
    regenerating: dict[str, str] = {}
    for lead in leads:
        if lead_source in _MSE_LEADS_SOURCES:
            ok, reason = contact_is_draftable(lead)
            if not ok:
                parked.append((lead, reason))
                continue
            prior = existing_seq.get(lead.get("id"))
            # A lead's route can change (an email grade improves, a catch-all
            # becomes sendable). A draft written for the other channel is not
            # "already done" -- it is wrong, and it would sit in the queue
            # looking fine. Regenerate it in place.
            if (prior and prior.get("drafted_for_route")
                    and prior["drafted_for_route"] != (lead.get("lead_route") or "").strip()
                    and prior["status"] in ("pending_hitl", "awaiting_contact")):
                _log.info("[MKT-O2] %s route changed %s -> %s; regenerating draft",
                          lead.get("company"), prior["drafted_for_route"], lead.get("lead_route"))
                regenerating[lead["id"]] = prior["id"]
                draftable.append(lead)
                continue
            if prior and prior["status"] == "awaiting_contact":
                # A buyer has since been named: regenerate this parked draft
                # IN PLACE rather than inserting a second one.
                regenerating[lead["id"]] = prior["id"]
            elif prior:
                # Any other live sequence already exists. Skipping is what
                # stops the duplicate-draft bug that put TWO Clutch sequences
                # (a2699e5d and 7059b489) in the queue for one lead.
                parked.append((lead, f"a {prior['status']} sequence already exists"))
                continue
            draftable.append(lead)
        else:
            draftable.append(lead)

    if parked:
        _emit_event(db, "dm_sequence_parked_no_buyer", {
            "product_id": product_id, "lead_source": lead_source,
            "parked": [{"lead_id": l.get("id"), "company": l.get("company"), "reason": r}
                       for l, r in parked],
        })
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "lead_source": lead_source,
            "skipped": "no_fit_accepted_contact", "parked_count": len(parked),
        })
        for l, r in parked:
            _log.info("[MKT-O2] parked %s (%s): %s", l.get("company"), l.get("id"), r)
    leads = draftable

    rows: list[dict] = []
    try:
        for lead in leads:
            if lead_source in _MSE_LEADS_SOURCES:
                # ROUTE decides the channel (decision 1, 2026-10-06). Selecting
                # on lead_source is what produced LinkedIn-shaped copy that
                # MKT-O5 emailed: provenance is not channel.
                sequence = _write_route_aware_sequence(
                    lead, product_id, anthropic_client=anthropic_client)
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
            # Email subject: written and reviewed WITH the body. MKT-O5 used
            # to invent "Quick question, {first_name}" at send time, making the
            # subject the only prospect-facing text nobody approved.
            if sequence.get("subject"):
                row["subject"] = sequence["subject"]
            if sequence.get("drafted_for_route"):
                row["drafted_for_route"] = sequence["drafted_for_route"]
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

        # Regenerated drafts UPDATE their parked row; genuinely new ones
        # insert. Splitting here rather than deleting-and-reinserting keeps
        # the sequence id stable, so anything already referencing it (a
        # dashboard link, an audit entry) still resolves.
        to_insert = []
        for row in rows:
            seq_id = regenerating.get(row.get("lead_finder_lead_id"))
            if not seq_id:
                to_insert.append(row)
                continue
            payload = {k: v for k, v in row.items()
                       if k in ("touch_1", "touch_2", "touch_3", "subject",
                                "drafted_for_route")}
            payload["status"] = "pending_hitl"
            payload["rejection_reason"] = None
            updated = db.table("mse_dm_sequences").update(payload).eq("id", seq_id).execute()
            if not updated.data:
                raise RuntimeError(f"Regenerating sequence {seq_id} returned no data")
            _emit_event(db, "dm_sequence_regenerated", {
                "sequence_id": seq_id, "lead_id": row.get("lead_finder_lead_id"),
                "product_id": product_id,
            })

        if to_insert:
            insert_result = db.table("mse_dm_sequences").insert(to_insert).execute()
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

    # Outbound loop closure (2026-10-01): per-lead skip accounting.
    # This function previously returned only written-counts, so a run that
    # wrote zero sequences while pending_dm leads existed was
    # indistinguishable from a run with nothing to do -- which is exactly
    # how the daily 8am workflow stayed silently inert for weeks. Every
    # pending_dm lead for this product is now accounted for: either
    # written, or skipped with a named reason.
    _all_pending = (
        db.table("mse_leads").select("id,source,email_status")
        .eq("product_id", product_id).eq("status", "pending_dm").execute().data or []
    )
    skip_reasons: dict[str, int] = {}

    def _skip(reason: str, n: int = 1) -> None:
        if n:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + n

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

    # Reconcile: every pending_dm lead must be either written or skipped
    # with a named reason. An unexplained gap is the exact silent-failure
    # shape that kept this engine inert -- surface it loudly rather than
    # returning a clean-looking zero.
    pending_total = len(_all_pending)
    _skip("lead_finder_email_not_verified",
          sum(1 for r in _all_pending
              if r.get("source") not in ("job_posting_signal", "cloud_decoded_job_signal")
              and r.get("email_status") != "verified"))
    accounted = total_written + sum(skip_reasons.values())
    unexplained = max(pending_total - accounted, 0)
    if unexplained:
        _skip("UNEXPLAINED", unexplained)

    if pending_total and total_written == 0:
        _log.warning(
            "[MKT-O2] product=%s had %d pending_dm lead(s) but wrote 0 sequences; skip reasons=%s",
            product_id, pending_total, skip_reasons,
        )
    else:
        _log.info(
            "[MKT-O2] product=%s pending_dm=%d written=%d skip_reasons=%s",
            product_id, pending_total, total_written, skip_reasons,
        )

    return {
        "status": "ready_for_hitl",
        "sequences_written": total_written,
        "by_source": by_source,
        "pending_dm_total": pending_total,
        "skip_reasons": skip_reasons,
    }


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
