"""
core/delivery_events.py

What a bounce or spam complaint does (Kelvin, 2026-11-07 brief, Phase 6).

Before this, bounce handling was inert: sends_paused() counted
mse_leads.status='bounced', but nothing in the codebase ever set it, because
no Resend webhook was received. A bounced address would have received
touch_2 and touch_3 regardless.

A hard bounce or a complaint:
  1. suppresses the address (checked before every send),
  2. marks the lead bounced / unsubscribed (feeds the 3% bounce pause),
  3. stops every in-flight sequence for that lead,
  4. writes an audit row.
A soft (transient) bounce is recorded only: the address is not dead.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Optional

from core.email_compliance import suppress_email

# Sequences in any of these states can still send something.
IN_FLIGHT_STATUSES = ("approved_hitl", "touch_1_sending", "touch_1_sent", "touch_2_sending",
                      "touch_2_sent", "touch_3_sending")

SIGNATURE_TOLERANCE_SECONDS = 300


class SignatureError(ValueError):
    """The webhook did not come from Resend (or was replayed)."""


def verify_svix_signature(secret: str, msg_id: str, timestamp: str, signature_header: str,
                          body: bytes, *, now: Optional[float] = None) -> None:
    """Resend signs webhooks with Svix: HMAC-SHA256 over
    "{svix-id}.{svix-timestamp}.{body}" keyed with the base64 part of the
    whsec_ secret. Raises SignatureError on any mismatch."""
    if not (secret and msg_id and timestamp and signature_header):
        raise SignatureError("missing webhook signature headers")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("bad svix-timestamp") from exc
    if abs((now if now is not None else time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        raise SignatureError("svix-timestamp outside tolerance")
    key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in signature_header.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return
    raise SignatureError("no matching v1 signature")


def classify_event(event: dict) -> Optional[str]:
    """'bounced', 'complained', 'soft_bounce' or None (ignored event)."""
    kind = (event or {}).get("type")
    if kind == "email.complained":
        return "complained"
    if kind == "email.bounced":
        bounce = ((event.get("data") or {}).get("bounce") or {})
        if str(bounce.get("type") or "").lower() in ("transient", "soft"):
            return "soft_bounce"
        return "bounced"
    return None


def recipient(event: dict) -> Optional[str]:
    to = ((event or {}).get("data") or {}).get("to")
    if isinstance(to, list):
        to = to[0] if to else None
    return (to or "").strip().lower() or None


def apply_delivery_failure(db, event: dict) -> dict:
    """Apply one Resend event. Idempotent: replays converge on the same state."""
    outcome = classify_event(event)
    email = recipient(event)
    result = {"event": (event or {}).get("type"), "outcome": outcome, "email_domain":
              (email or "").rpartition("@")[2] or None, "leads": 0, "sequences_stopped": 0}
    if outcome is None or not email:
        return result

    if outcome in ("bounced", "complained"):
        suppress_email(db, email, reason=outcome)
        lead_status = "bounced" if outcome == "bounced" else "unsubscribed"
        update = {"status": lead_status}
        if outcome == "bounced":
            update["email_status"] = "bounced"
        leads = (db.table("mse_leads").update(update).eq("email", email).execute().data or [])
        result["leads"] = len(leads)
        seq_status = "bounced" if outcome == "bounced" else "suppressed"
        for lead in leads:
            stopped = (db.table("mse_dm_sequences").update({"status": seq_status})
                       .eq("lead_finder_lead_id", lead["id"]).in_("status", list(IN_FLIGHT_STATUSES))
                       .execute().data or [])
            result["sequences_stopped"] += len(stopped)
        product_id = leads[0].get("product_id") if leads else None
    else:
        product_id = None

    db.table("audit_log").insert({
        "agent_id": "resend-webhook",
        "action": f"delivery_{outcome}",
        "outcome": "win",
        "product_id": product_id,
        "metadata": {**result, "resend_id": ((event.get("data") or {}).get("email_id"))},
    }).execute()
    return result
