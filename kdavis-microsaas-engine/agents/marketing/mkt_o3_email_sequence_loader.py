"""
MKT-O3 Email Sequence Loader.

Loads an already-approved sequence (written by MKT-O2 into mse_dm_sequences,
HITL-approved via hitl_approved_at/hitl_approved_by) into systeme.io by
enrolling the lead's contact into a systeme.io email sequence. This agent
never writes new copy — it takes touch_1/touch_2 as-is from the approved
row and hands the *enrollment* off to systeme.io, which then owns actual
delivery.

Non-negotiable HITL gate (CLAUDE.md "No autonomous outbound — approved
status required in DB"): raises if the referenced mse_dm_sequences row
does not have hitl_approved_at set. Never loads an unapproved sequence.

No run() adapter here (unlike MKT-O1/O2) — MKT-ORCH's dynamic dispatch
fires at campaign-build time with a whole research_report/campaign_build,
but this agent's trigger is per-sequence, after a human approves one
specific mse_dm_sequences row. Wiring it into MKT-ORCH's auto-fan-out
would bypass that HITL gate, so it's invoked only via
POST /marketing/load-sequence.

NOTE: systeme.io's exact contact-lookup/create and enrollment request
shapes below (GET/POST /api/contacts, POST /api/contacts/{id}/sequences,
X-API-Key header) match their documented REST API as of this writing but
haven't been exercised against a live key — verify against current
systeme.io docs once SYSTEME_API_KEY is set. Which systeme.io sequence to
enroll into is configured via SYSTEME_SEQUENCE_ID (one global sequence for
now — swap to a per-product mapping if/when multiple sequences are needed).

NOTE: "load each step" — the task spec for this agent calls out loading
mse_dm_sequences' two touches (touch_1, touch_2) as separate steps, so
POST /api/contacts/{contact_id}/sequences is called once per touch below
rather than once for the whole sequence. systeme.io's real sequences are
pre-built in their dashboard and enrolled into by ID, not assembled from
per-step content via this endpoint — there's no documented way to hand
touch_1/touch_2's actual text to systeme.io through it. Each call below
enrolls the same contact into the same SYSTEME_SEQUENCE_ID, tagged with
a step number, on the assumption systeme.io either de-dupes enrollment
or that "step" is meaningful metadata on their side; this is unverified
against live docs and worth confirming before this touches real contacts.

NOTE: mse_dm_sequences.touch_1/touch_2 were written by MKT-O2 as LinkedIn
DM copy (300/500-char limits, DM framing — see
mkt_o2_cold_dm_writer.py's _SYSTEM_PROMPT) rather than email copy. This
agent enrolls the contact into systeme.io's sequence infrastructure; it
does not push touch_1/touch_2 text into systeme.io itself (systeme.io
sequences own their own email content once a contact is enrolled). Flagged
here since "email sequence" vs. "DM sequence" naming is easy to conflate —
confirm the systeme.io sequence configured via SYSTEME_SEQUENCE_ID is
actually the intended email content before enrolling live contacts.
"""

import os
from typing import Any, Optional

import httpx

from core.supabase_client import get_supabase

AGENT_ID = "mkt-o3"
SYSTEME_API_BASE = "https://api.systeme.io"


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


def _find_or_create_contact(
    email: str, first_name: Optional[str], last_name: Optional[str], api_key: str, http_client=None,
) -> str:
    client = http_client or httpx
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    search = client.get(
        f"{SYSTEME_API_BASE}/api/contacts",
        params={"email": email},
        headers=headers,
        timeout=30,
    )
    search.raise_for_status()
    existing = search.json().get("items") or []
    if existing:
        return str(existing[0]["id"])

    created = client.post(
        f"{SYSTEME_API_BASE}/api/contacts",
        json={"email": email, "fields": {"first_name": first_name, "surname": last_name}},
        headers=headers,
        timeout=30,
    )
    created.raise_for_status()
    return str(created.json()["id"])


def _load_sequence_step(contact_id: str, sequence_id: str, step: int, api_key: str, http_client=None) -> None:
    client = http_client or httpx
    response = client.post(
        f"{SYSTEME_API_BASE}/api/contacts/{contact_id}/sequences",
        json={"sequence_id": sequence_id, "step": step},
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()


def run_o3_email_sequence_loader(
    sequence_id: str,
    product_id: str,
    supabase_client: Optional[Any] = None,
    http_client: Optional[Any] = None,
) -> dict:
    """
    Loads an approved mse_dm_sequences row into systeme.io by enrolling the
    matching lead's contact into the configured systeme.io sequence.

    Returns {"status": "skipped", "reason": "no_api_key"} without raising
    if SYSTEME_API_KEY isn't set — matches MKT-O1's graceful-skip precedent
    for unprovisioned third-party accounts. Raises RuntimeError for any
    other failure (including an unapproved sequence) — never fails
    silently.
    """
    db = supabase_client if supabase_client is not None else get_supabase()
    api_key = os.getenv("SYSTEME_API_KEY")

    if not api_key:
        print("SYSTEME_API_KEY not set — mkt_o3 skipped")
        _write_audit(db, "lose", product_id, {"sequence_id": sequence_id, "reason": "no_api_key"})
        return {"status": "skipped", "reason": "no_api_key"}

    _emit_event(db, "email_sequence_load_started", {"product_id": product_id, "sequence_id": sequence_id})

    try:
        sequence = db.table("mse_dm_sequences").select("*").eq("id", sequence_id).single().execute().data
        if not sequence:
            raise RuntimeError(f"mse_dm_sequences row {sequence_id} not found")
        if not sequence.get("hitl_approved_at"):
            raise RuntimeError(f"mse_dm_sequences row {sequence_id} is not HITL-approved — refusing to load")

        lead = db.table("mse_apollo_leads").select("*").eq("id", sequence["lead_id"]).single().execute().data
        if not lead or not lead.get("email"):
            raise RuntimeError(f"No email on file for lead {sequence.get('lead_id')} — cannot load into systeme.io")

        sequence_target = os.getenv("SYSTEME_SEQUENCE_ID")
        if not sequence_target:
            raise RuntimeError("SYSTEME_SEQUENCE_ID not configured — cannot enroll contact into a sequence")

        contact_id = _find_or_create_contact(
            lead["email"], lead.get("first_name"), lead.get("last_name"), api_key, http_client=http_client,
        )

        steps = [sequence["touch_1"], sequence["touch_2"]]
        for step_number, _touch_text in enumerate(steps, start=1):
            _load_sequence_step(contact_id, sequence_target, step_number, api_key, http_client=http_client)

        db.table("mse_dm_sequences").update({"status": "loaded"}).eq("id", sequence_id).execute()

    except Exception as exc:
        _write_audit(db, "lose", product_id, {"sequence_id": sequence_id, "error": str(exc)})
        raise RuntimeError(f"MKT-O3 email sequence load failed for sequence {sequence_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {"sequence_id": sequence_id, "steps_loaded": len(steps)})
    _emit_event(db, "email_sequence_load_completed", {
        "product_id": product_id, "sequence_id": sequence_id, "steps_loaded": len(steps),
    })

    return {"status": "loaded", "sequence_id": sequence_id, "steps_loaded": len(steps)}
