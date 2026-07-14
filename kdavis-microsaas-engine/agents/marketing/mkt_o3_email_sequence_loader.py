"""
MKT-O3 Email Sequence Loader.

Drafts a short trial-nurture email sequence from the research report's pain
language and ICP, then loads it into systeme.io as an UNACTIVATED sequence
("campaign" in Systeme.io's terminology) — never sends anything, never
activates. Every sequence lands in mse_email_sequences with
status='pending_hitl' (or 'loaded_unactivated' once the systeme.io call
succeeds); a human approves and activates it manually in systeme.io. Matches
MKT-O2's draft-only precedent — this agent has no sender/activation path.
"""

import json
import os
from typing import Any, Optional

import core.llm_router as llm_router
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


class _SystemeIOClient:
    """
    Thin wrapper around Systeme.io's REST API, scoped to what MKT-O3 needs:
    creating an unactivated campaign/sequence and its email steps. Endpoint
    paths reflect Systeme.io's public API docs (developer.systeme.io) as
    best-effort — Systeme.io's public API historically has limited/unclear
    support for programmatic campaign creation with content; verify against
    current docs before relying on this against a live account. Mirrors the
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
    systeme_client: Optional[Any] = None,
) -> dict:
    """
    Drafts a trial-nurture sequence and loads it into systeme.io unactivated.
    Never activates anything. Raises on any failure — never fails silently.
    Returns {status, sequence_id, email_count}.
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

        systeme = systeme_client if systeme_client is not None else _SystemeIOClient()
        campaign = systeme.create_unactivated_campaign(
            name=f"{product_name or product_id} — trial nurture",
            emails=emails,
        )

        db.table("mse_email_sequences").update({
            "systeme_sequence_id": campaign.get("id"),
            "status": "loaded_unactivated",
        }).eq("id", row_id).execute()

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


def run(research_report: dict, campaign_build: dict) -> dict:
    """Adapter for MKT-ORCH's dynamic dispatch (agents.marketing.mkt_o3_email_sequence_loader.run)."""
    return run_o3_email_sequence_loader(
        product_id=campaign_build["product_id"],
        research_report=research_report,
        campaign_build_id=campaign_build["id"],
        product_name=campaign_build.get("product_name", ""),
    )
