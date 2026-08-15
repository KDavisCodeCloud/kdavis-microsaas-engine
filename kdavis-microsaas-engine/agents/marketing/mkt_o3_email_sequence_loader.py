"""
MKT-O3 Email Sequence Loader.

Drafts a short trial-nurture email sequence from the research report's pain
language and ICP, then loads it into mse_email_sequences — never sends
anything, never activates. A human approves the drafted copy in the HITL
queue; the real "sending" is Brevo's own automation workflow (built once,
manually, per product, in the Brevo UI — see BREVO_SETUP.md), not this
agent. Matches MKT-O2's draft-only precedent — this agent has no
sender/activation path of its own.

PROVIDER SWAP (2026-08-14): systeme.io replaced by Brevo. systeme.io's
public API has no campaigns/sequences endpoint at all — CONFIRMED
BLOCKER, verified directly: GET /api/contacts, /api/tags, /api/webhooks,
/api/funnels all return 200 against the live account; GET/POST
/api/campaigns and every plausible variant (email_campaigns, sequences,
email_sequences, automations, campaign, newsletters) return 404.
Systeme.io's public API does not currently expose campaign/sequence
creation at all. _SystemeIOClient/SystemeIOError are retained below
(DEPRECATED = True), unused by the active flow, purely to document the
real integration shape investigated — do not delete.

Design note on where the systeme.io push disappeared to, not just what
replaced it: systeme.io's create_unactivated_campaign was trying to
create the SEQUENCE CONTAINER itself via API at draft time (before any
real trial signup exists) — that's structurally what always 404'd.
Brevo's equivalent (the automation workflow) is built once, manually, in
the Brevo UI (BREVO_SETUP.md) — there's no API call that belongs at
draft time for Brevo at all. What Brevo DOES need an API call for is
enrolling one specific real contact once a real trial signup happens,
which is a different moment in time with different available data (an
email, a name, a plan tier — none of which exist yet at draft time). That
per-signup enrollment is enroll_trial_in_sequence(), a new, separate
entry point below, not a modification of run_o3_email_sequence_loader's
draft step. A side effect worth naming: removing the always-404ing
systeme.io push from the draft step also fixes a real, standing bug —
campaign_builds.email_sequence_status could previously never reach
'ready_for_hitl' for any real campaign build, only 'failed'.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import core.llm_router as llm_router
from core.brevo_client import create_or_update_contact
from core.email_compliance import is_suppressed
from core.sanitization import DataSanitizationShield
from core.supabase_client import get_supabase

AGENT_ID = "mkt-o3"

SEQUENCE_LENGTH = 5
SUBJECT_MAX_CHARS = 80
BODY_MAX_CHARS = 800

_SYSTEM_PROMPT = f"""You are MKT-O3, drafting a {SEQUENCE_LENGTH}-email trial-nurture sequence \
for one product's new trial signups. Return ONLY a single JSON object — no prose, no markdown \
fences — matching exactly this schema:

{{
  "emails": [
    {{"day": int, "subject": str, "body": str}},
    ...
  ]
}}

Exactly {SEQUENCE_LENGTH} emails, days must be strictly increasing starting at 0.
subject = max {SUBJECT_MAX_CHARS} chars, specific, no clickbait, reflects the email content exactly.
body = max {BODY_MAX_CHARS} chars, plain text (no heavy HTML), reads like a person sent it, one \
point and one CTA per email, no buzzwords ("AI-powered", "revolutionary", "game-changing").

Rules, non-negotiable:
- Day 0: welcome + what to do first (onboarding), no pitch
- Middle emails: use the exact pain language from the research below, one concrete result/metric
- Last email: trial-ending nudge with a clear, low-friction CTA
- Never invent a discount, price, or guarantee not present in the research context"""


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


def _draft_sequence(research_context: dict, anthropic_client=None) -> list[dict]:
    user_prompt = (
        f"Pain language, proof signals, and pricing context from research:\n"
        f"{json.dumps(research_context, indent=2)}\n\n"
        "Draft the trial-nurture sequence now."
    )
    raw = _analyze(_SYSTEM_PROMPT, user_prompt, anthropic_client=anthropic_client)
    parsed = json.loads(_strip_fences(raw))
    emails = parsed.get("emails") if isinstance(parsed, dict) else None
    if not isinstance(emails, list) or not emails:
        raise ValueError(f"MKT-O3 expected {{emails: [...]}}, got: {raw[:200]}")

    cleaned = []
    for e in emails:
        cleaned.append({
            "day": int(e["day"]),
            "subject": str(e["subject"])[:SUBJECT_MAX_CHARS],
            "body": str(e["body"])[:BODY_MAX_CHARS],
        })
    return cleaned


class SystemeIOError(RuntimeError):
    pass


DEPRECATED = True  # see module docstring's PROVIDER SWAP note — Brevo replaces this


class _SystemeIOClient:
    """
    DEPRECATED (2026-08-14) — never called by the active flow anymore.
    Retained only to document the real integration shape that was
    investigated (endpoint paths, auth header) in case Systeme.io ever
    ships a real campaigns API and this is worth revisiting. Mirrors the
    existing read-mostly wrapper in kdavis-agentic-platform's
    leads/integrations/systeme_io.py (base URL, X-API-Key header) rather
    than importing across repos, matching this repo's self-containment
    convention for agents/marketing/*.
    """

    DEFAULT_BASE_URL = "https://api.systeme.io/api"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, client: Optional[Any] = None):
        self._api_key = api_key or os.environ["SYSTEME_API_KEY"]
        self._base_url = (base_url or os.getenv("SYSTEME_API_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                base_url=self._base_url,
                headers={"X-API-Key": self._api_key, "Content-Type": "application/json"},
                timeout=15.0,
            )
        return self._client

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self._get_client().request(method, path, **kwargs)
        if response.status_code >= 400:
            raise SystemeIOError(f"Systeme.io {method} {path} failed [{response.status_code}]: {response.text}")
        return response.json() if response.content else {}

    def create_unactivated_campaign(self, name: str, emails: list[dict]) -> dict:
        """
        Creates a campaign/sequence in a draft (unactivated) state and adds
        each email as a step. Best-effort shape — Systeme.io may require
        activating steps individually or via a separate endpoint; if this
        call's shape is wrong against the live API, it raises SystemeIOError
        rather than silently pretending to succeed.
        """
        campaign = self._request("POST", "/campaigns", json={"name": name, "status": "draft"})
        campaign_id = campaign.get("id")
        for step in emails:
            self._request(
                "POST",
                f"/campaigns/{campaign_id}/emails",
                json={
                    "subject": step["subject"],
                    "content": step["body"],
                    "delay_days": step["day"],
                },
            )
        return campaign


def run_o3_email_sequence_loader(
    product_id: str,
    research_report: dict,
    campaign_build_id: str,
    product_name: str = "",
    supabase_client: Optional[Any] = None,
    anthropic_client: Optional[Any] = None,
) -> dict:
    """
    Drafts a trial-nurture sequence and saves it to mse_email_sequences,
    pending HITL approval. Runs at campaign-build time, before any real
    trial signup exists — there is no contact to enroll in Brevo yet, and
    (unlike the old systeme.io attempt) Brevo needs no API call at this
    stage at all, since its equivalent of "the sequence container" is the
    automation workflow built manually in the Brevo UI. Enrolling a real
    signup into that workflow is enroll_trial_in_sequence(), below,
    called separately whenever a real trial actually starts. Raises on
    any failure — never fails silently. Returns {status, sequence_id,
    email_count}.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    _emit_event(db, "email_sequence_load_started", {
        "product_id": product_id, "campaign_build_id": campaign_build_id,
    })

    research_context = DataSanitizationShield.clean({
        "pain_language": research_report.get("pain_language", []),
        "proof_signals": research_report.get("proof_signals", []),
        "willingness_to_pay_band": research_report.get("willingness_to_pay_band", ""),
    })

    row_id = None
    try:
        emails = _draft_sequence(research_context, anthropic_client=anthropic_client)

        insert_result = db.table("mse_email_sequences").insert({
            "product_id": product_id,
            "campaign_build_id": campaign_build_id,
            "emails": emails,
            "status": "pending_hitl",
        }).execute()
        if not insert_result.data:
            raise RuntimeError("Insert into mse_email_sequences returned no data")
        row_id = insert_result.data[0]["id"]

        db.table("campaign_builds").update(
            {"email_sequence_status": "ready_for_hitl"}
        ).eq("id", campaign_build_id).execute()

    except Exception as exc:
        _write_audit(db, "lose", product_id, {
            "campaign_build_id": campaign_build_id, "error": str(exc),
        })
        if row_id is not None:
            db.table("mse_email_sequences").update({"status": "failed"}).eq("id", row_id).execute()
        db.table("campaign_builds").update({"email_sequence_status": "failed"}).eq("id", campaign_build_id).execute()
        raise RuntimeError(f"MKT-O3 email sequence load failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {
        "campaign_build_id": campaign_build_id, "sequence_id": row_id, "email_count": len(emails),
    })
    _emit_event(db, "email_sequence_load_completed", {
        "product_id": product_id, "campaign_build_id": campaign_build_id, "email_count": len(emails),
    })

    return {"status": "ready_for_hitl", "sequence_id": row_id, "email_count": len(emails)}


@dataclass
class EnrollmentResult:
    status: str  # "enrolled" | "suppressed" | "failed"
    sequence_drafted: bool
    enrolled: bool
    sequence_id: Optional[str] = None
    brevo_list_id: Optional[int] = None
    error: Optional[str] = None


def enroll_trial_in_sequence(
    product_id: str,
    email: str,
    first_name: str,
    last_name: str,
    plan_tier: str,
    trial_start: str,
    supabase_client: Optional[Any] = None,
    brevo_client: Optional[Any] = None,
) -> EnrollmentResult:
    """
    Called when a real trial signup occurs (POST /marketing/brevo/enroll,
    triggered by n8n/trial_enrollment_workflow.json off a Stripe/Supabase
    trialing-status event). Enrolls the contact into the product's Brevo
    list, which is what fires that product's pre-built Brevo automation
    (BREVO_SETUP.md) — this function itself never sends an email.

    "Drafts the sequence if not already drafted" means: reuses the most
    recent mse_email_sequences row for this product if one exists (the
    normal case — MKT-O3's campaign-build step already ran long before
    any real trial signup). It deliberately does NOT attempt to draft a
    brand-new sequence here on the fly: this function's signature has no
    research_report to draft from (only signup-specific data — email,
    name, plan tier), and MKT-O3's own system prompt is explicit that
    nothing gets invented without real research context. If no sequence
    has ever been drafted for this product, that's a real, surfaced
    failure (status="failed"), not a silently-fabricated generic one.

    Never raises for expected/recoverable conditions (suppressed email,
    no Brevo list registered yet, no sequence drafted yet, Brevo API
    failure) — returns a typed EnrollmentResult for all of them, since
    this is called from a live per-signup webhook path where a clean
    logged result is more useful than a generic 500. Every outcome is
    still audited via audit_log, win or lose, per this repo's own
    non-negotiable.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    _emit_event(db, "trial_enrollment_started", {"product_id": product_id, "email": email})

    if is_suppressed(db, email):
        _write_audit(db, "lose", product_id, {"email": email, "reason": "suppressed"})
        return EnrollmentResult(status="suppressed", sequence_drafted=False, enrolled=False)

    list_result = (
        db.table("mse_brevo_lists")
        .select("brevo_list_id")
        .eq("product_id", product_id)
        .maybe_single()
        .execute()
    )
    if list_result is None or not list_result.data:
        error = f"No Brevo list registered for product {product_id} — POST /marketing/brevo/lists first"
        _write_audit(db, "lose", product_id, {"email": email, "error": error})
        return EnrollmentResult(status="failed", sequence_drafted=False, enrolled=False, error=error)
    brevo_list_id = list_result.data["brevo_list_id"]

    sequence_result = (
        db.table("mse_email_sequences")
        .select("id")
        .eq("product_id", product_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    sequence_rows = sequence_result.data or []
    if not sequence_rows:
        error = f"No drafted trial-nurture sequence for product {product_id} yet — run the campaign build's MKT-O3 step first"
        _write_audit(db, "lose", product_id, {"email": email, "error": error})
        return EnrollmentResult(status="failed", sequence_drafted=False, enrolled=False, brevo_list_id=brevo_list_id, error=error)
    sequence_id = sequence_rows[0]["id"]

    safe_attributes = DataSanitizationShield.clean({
        "product_id": product_id, "plan_tier": plan_tier, "trial_start": trial_start,
    })
    contact_result = create_or_update_contact(
        email=email, first_name=first_name, last_name=last_name,
        attributes=safe_attributes, list_ids=[brevo_list_id],
        brevo_client=brevo_client,
    )

    if not contact_result.success:
        _write_audit(db, "lose", product_id, {
            "email": email, "sequence_id": sequence_id, "brevo_list_id": brevo_list_id, "error": contact_result.error,
        })
        return EnrollmentResult(
            status="failed", sequence_drafted=True, enrolled=False,
            sequence_id=sequence_id, brevo_list_id=brevo_list_id, error=contact_result.error,
        )

    _write_audit(db, "win", product_id, {
        "email": email, "sequence_id": sequence_id, "brevo_list_id": brevo_list_id,
    })
    _emit_event(db, "trial_enrollment_completed", {
        "product_id": product_id, "email": email, "brevo_list_id": brevo_list_id,
    })

    return EnrollmentResult(
        status="enrolled", sequence_drafted=True, enrolled=True,
        sequence_id=sequence_id, brevo_list_id=brevo_list_id,
    )


def run(research_report: dict, campaign_build: dict) -> dict:
    """Adapter for MKT-ORCH's dynamic dispatch (agents.marketing.mkt_o3_email_sequence_loader.run)."""
    return run_o3_email_sequence_loader(
        product_id=campaign_build["product_id"],
        research_report=research_report,
        campaign_build_id=campaign_build["id"],
        product_name=campaign_build.get("product_name", ""),
    )
