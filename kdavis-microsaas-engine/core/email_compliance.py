"""
CAN-SPAM compliance guard for cold-outreach email.

MKT-O5 (agents/marketing/mkt_o5_sequence_sender.py) is the only agent that
sends real commercial email via Resend -- it had no unsubscribe mechanism,
no suppression check, and no physical mailing address anywhere in the send
path. All three are legally required by CAN-SPAM for any commercial email
to a US recipient, not just best practice. Flagged in the 2026-08-12
marketing infrastructure audit; this closes it.

Unsubscribe tokens are stateless -- an HMAC of the email address, not a
stored per-lead token -- so there's nothing to generate ahead of a send or
clean up afterward, and a link keeps working even across a fresh deploy.
Suppression itself is global across every MSE product (see the migration's
own comment for why), checked via mse_email_suppressions
(supabase/migrations/20260812000020_email_suppressions.sql).
"""
import hashlib
import hmac
import os
from urllib.parse import quote

_TOKEN_LENGTH = 32


def _secret() -> str:
    # Deliberately os.environ[...] not .get() -- an unset secret must fail
    # closed (every call site that needs it raises immediately) rather than
    # silently mint tokens against an empty string, which anyone could
    # forge.
    return os.environ["UNSUBSCRIBE_SECRET"]


def _normalize(email: str) -> str:
    return email.strip().lower()


def generate_unsubscribe_token(email: str) -> str:
    return hmac.new(_secret().encode(), _normalize(email).encode(), hashlib.sha256).hexdigest()[:_TOKEN_LENGTH]


def verify_unsubscribe_token(email: str, token: str) -> bool:
    if not token:
        return False
    expected = generate_unsubscribe_token(email)
    return hmac.compare_digest(expected, token)


def build_unsubscribe_url(email: str) -> str:
    base_url = os.environ["MARKETING_API_BASE_URL"].rstrip("/")
    token = generate_unsubscribe_token(email)
    return f"{base_url}/marketing/unsubscribe?email={quote(_normalize(email))}&token={token}"


def is_suppressed(db, email: str) -> bool:
    result = (
        db.table("mse_email_suppressions")
        .select("id")
        .eq("email", _normalize(email))
        .maybe_single()
        .execute()
    )
    return result is not None and bool(result.data)


def suppress_email(db, email: str, reason: str = "unsubscribed") -> None:
    db.table("mse_email_suppressions").upsert(
        {"email": _normalize(email), "reason": reason},
        on_conflict="email",
    ).execute()


def build_list_unsubscribe_headers(email: str) -> dict[str, str]:
    """RFC 8058 one-click unsubscribe headers -- required on every
    marketing send per the 2026-09-21 email-campaign-system build
    (Cloud Decoded's equivalent build locked the same requirement). A
    conforming mail client POSTs to the https URI below with body
    `List-Unsubscribe=One-Click` instead of making the human click through
    a landing page; api/routers/marketing.py's POST /marketing/unsubscribe
    handler is the corresponding server-side half of this."""
    return {
        "List-Unsubscribe": f"<{build_unsubscribe_url(email)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def append_compliance_footer(body: str, email: str) -> str:
    """
    Appends the CAN-SPAM-required physical mailing address and a one-click
    unsubscribe link. Raises rather than silently sending without them --
    a missing physical address in a commercial email is a real compliance
    violation, not a degraded-but-fine state (unlike, say, Apollo skipping
    gracefully with no API key, which just means nothing to do yet).
    """
    mailing_address = os.environ["COMPLIANCE_MAILING_ADDRESS"]
    unsubscribe_url = build_unsubscribe_url(email)
    footer = f"\n\n---\n{mailing_address}\nDon't want these emails? Unsubscribe: {unsubscribe_url}"
    return f"{body}{footer}"


_DEFAULT_DAILY_SEND_CAP = 200


def sends_today(db) -> int:
    """Counts real sends (audit_log wins, agent_id=mkt-o5, action=
    sequence_send) since midnight UTC. Not a legal CAN-SPAM requirement
    like the rest of this file -- a safety cap against a bad batch or a
    runaway n8n retry loop sending far more mail than intended in one day,
    which is a deliverability/reputation risk on top of just being wrong."""
    from datetime import datetime, timezone

    midnight_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    result = (
        db.table("audit_log")
        .select("id", count="exact")
        .eq("agent_id", "mkt-o5")
        .eq("action", "sequence_send")
        .eq("outcome", "win")
        .gte("created_at", midnight_utc)
        .execute()
    )
    return result.count or 0


def daily_send_cap() -> int:
    return int(os.environ.get("MARKETING_DAILY_SEND_CAP", _DEFAULT_DAILY_SEND_CAP))
