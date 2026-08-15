"""
Email pattern finder + SMTP verifier (Source 3). Once a name and company
domain are known, finds and verifies the email without paying per-lookup:

1. Pattern detection (build_pattern_candidates): tries this domain's
   highest-success learned pattern first (core/email_patterns.py), then
   falls back to the standard professional-email pattern order.
2. SMTP verification (verify_email): connects to the domain's real MX
   server and probes with RCPT TO — never actually sends mail, just reads
   the server's own accept/reject response for that address. Also probes
   a second, deliberately-fake address at the same domain to distinguish
   a genuine 250 (verified) from a catch-all domain that accepts every
   address (catch_all) — the same technique tools like Hunter.io/NeverBounce
   use, since a catch-all 250 on the real candidate proves nothing.

Throttled by design, not by a background scheduler: callers verifying many
candidates in one run should call verify_email in a loop and let
VERIFY_DELAY_SECONDS pace them — keeps any single run well under the
"10-20 verifications/hour from one IP" ceiling without needing shared
cross-process rate-limit state.

Never send to invalid or catch_all emails — core/email_finder.py only
ever reports a status; agents/marketing/mkt_lead_finder.py and MKT-O5 are
what actually enforce "only verified enters the send queue."
"""

import random
import re
import smtplib
import time
from dataclasses import dataclass
from typing import Any, Optional

import dns.resolver

from core.email_patterns import DEFAULT_PATTERN_ORDER, get_known_patterns, record_attempt

MIN_VERIFY_DELAY_SECONDS = 180  # 3-6 min spacing -> well under 20/hour even across a long batch
MAX_VERIFY_DELAY_SECONDS = 360

SMTP_TIMEOUT_SECONDS = 10
SMTP_PORT = 25

_PATTERN_BUILDERS = {
    "firstname": lambda first, last: f"{first}@{{domain}}" if first else None,
    "firstname.lastname": lambda first, last: f"{first}.{last}@{{domain}}" if first and last else None,
    "flastname": lambda first, last: f"{first[0]}{last}@{{domain}}" if first and last else None,
    "firstnamelastname": lambda first, last: f"{first}{last}@{{domain}}" if first and last else None,
}


@dataclass
class EmailResult:
    email: Optional[str]
    pattern_used: Optional[str]
    verification_status: str  # verified | unverified | catch_all | invalid
    confidence_score: float


@dataclass
class VerificationResult:
    status: str  # verified | unverified | catch_all | invalid
    smtp_code: Optional[int]
    message: str


def _clean_name_part(value: Optional[str]) -> str:
    return re.sub(r"[^a-z]", "", (value or "").lower())


def build_pattern_candidates(first_name: str, last_name: str, domain: str, known_patterns: Optional[list[str]] = None) -> list[str]:
    """Candidate emails in priority order: this domain's highest-success
    learned pattern(s) first (if given), then the standard fallback
    order. Never returns a candidate built from a blank first name or
    domain — nothing to build a pattern from without those."""
    first = _clean_name_part(first_name)
    last = _clean_name_part(last_name)
    if not first or not domain:
        return []

    order: list[str] = []
    for pattern in (known_patterns or []) + DEFAULT_PATTERN_ORDER:
        if pattern not in order:
            order.append(pattern)

    candidates: list[str] = []
    seen: set[str] = set()
    for pattern in order:
        builder = _PATTERN_BUILDERS.get(pattern)
        if not builder:
            continue
        template = builder(first, last)
        if not template:
            continue
        email = template.format(domain=domain)
        if email not in seen:
            seen.add(email)
            candidates.append(email)
    return candidates


def _mx_host(domain: str, resolver: Optional[Any] = None) -> Optional[str]:
    resolve = (resolver or dns.resolver).resolve
    try:
        answers = resolve(domain, "MX")
        best = min(answers, key=lambda r: r.preference)
        return str(best.exchange).rstrip(".")
    except Exception:
        return None


def _probe_rcpt(smtp_client: Any, mail_from: str, rcpt_to: str) -> tuple[int, str]:
    smtp_client.helo("localhost")
    smtp_client.mail(mail_from)
    code, message = smtp_client.rcpt(rcpt_to)
    return code, message.decode() if isinstance(message, (bytes, bytearray)) else str(message)


def verify_email(
    email: str,
    domain: Optional[str] = None,
    smtp_client: Optional[Any] = None,
    mail_from: Optional[str] = None,
    resolver: Optional[Any] = None,
) -> VerificationResult:
    """Real SMTP RCPT TO verification against the domain's own MX server.
    `smtp_client` is injectable (an object exposing helo/mail/rcpt/quit,
    the same shape as smtplib.SMTP) so tests never open a real socket —
    when omitted, connects for real via smtplib.
    """
    resolved_domain = domain or (email.split("@", 1)[1] if "@" in email else None)
    if not resolved_domain:
        return VerificationResult(status="invalid", smtp_code=None, message="no domain to verify against")

    mx_host = _mx_host(resolved_domain, resolver=resolver)
    if not mx_host:
        return VerificationResult(status="unverified", smtp_code=None, message="no MX record found")

    from_address = mail_from or "verify@resend.dev"
    owns_client = smtp_client is None
    client = smtp_client
    try:
        if owns_client:
            client = smtplib.SMTP(timeout=SMTP_TIMEOUT_SECONDS)
            client.connect(mx_host, SMTP_PORT)

        real_code, real_message = _probe_rcpt(client, from_address, email)

        if real_code == 250:
            # Distinguish a genuine accept from a catch-all domain that
            # 250s everything -- probe a deliberately-fake address at the
            # same domain; if it ALSO 250s, the domain accepts all mail
            # and the real candidate's 250 proves nothing.
            fake_local_part = f"nonexistent-verify-probe-{random.randint(100000, 999999)}"
            fake_code, _ = _probe_rcpt(client, from_address, f"{fake_local_part}@{resolved_domain}")
            if fake_code == 250:
                return VerificationResult(status="catch_all", smtp_code=real_code, message=real_message)
            return VerificationResult(status="verified", smtp_code=real_code, message=real_message)

        if real_code == 550:
            return VerificationResult(status="invalid", smtp_code=real_code, message=real_message)

        return VerificationResult(status="unverified", smtp_code=real_code, message=real_message)

    except Exception as exc:
        return VerificationResult(status="unverified", smtp_code=None, message=str(exc))
    finally:
        if owns_client and client is not None:
            try:
                client.quit()
            except Exception:
                pass


def find_email(
    first_name: str,
    last_name: str,
    domain: str,
    supabase_client: Optional[Any] = None,
    smtp_client: Optional[Any] = None,
    resolver: Optional[Any] = None,
    max_candidates: int = 4,
) -> EmailResult:
    """
    Orchestrates pattern generation + SMTP verification for one lead:
    tries candidates in priority order (this domain's best-known pattern
    first), stops at the first `verified` result, records every attempt
    to core/email_patterns.py regardless of outcome (so the pattern
    database learns from failures too, not just successes), and never
    raises — a domain with no MX record or a fully unreachable mail
    server comes back as a normal `unverified` EmailResult, not an
    exception.
    """
    known = [row["pattern"] for row in get_known_patterns(domain, supabase_client=supabase_client)]
    candidates = build_pattern_candidates(first_name, last_name, domain, known_patterns=known)[:max_candidates]

    if not candidates:
        return EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)

    # pattern name per candidate, in the same order build_pattern_candidates produced them
    pattern_order = [p for p in (known + DEFAULT_PATTERN_ORDER) if p in _PATTERN_BUILDERS]
    seen_patterns: list[str] = []
    for p in pattern_order:
        if p not in seen_patterns:
            seen_patterns.append(p)

    best_result: Optional[EmailResult] = None
    for i, email in enumerate(candidates):
        pattern = seen_patterns[i] if i < len(seen_patterns) else "unknown"
        verification = verify_email(email, domain=domain, smtp_client=smtp_client, resolver=resolver)
        record_attempt(domain, pattern, success=verification.status == "verified", supabase_client=supabase_client)

        confidence = {"verified": 0.95, "catch_all": 0.4, "unverified": 0.2, "invalid": 0.0}[verification.status]
        result = EmailResult(email=email, pattern_used=pattern, verification_status=verification.status, confidence_score=confidence)

        if verification.status == "verified":
            return result
        if best_result is None or confidence > best_result.confidence_score:
            best_result = result

        if i < len(candidates) - 1:
            time.sleep(random.uniform(MIN_VERIFY_DELAY_SECONDS, MAX_VERIFY_DELAY_SECONDS))

    return best_result or EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)
