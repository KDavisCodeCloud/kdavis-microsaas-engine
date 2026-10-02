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

# Retained for callers that still reference them; the pacing they described is
# superseded by the per-MX throttle below.
MIN_VERIFY_DELAY_SECONDS = 180
MAX_VERIFY_DELAY_SECONDS = 360

# ── Per-MX throttling (Kelvin's decision 1c, 2026-10-02) ─────────────────
#
# WHY THE OLD SLEEP WAS THE WRONG SHAPE. find_email slept 180-360s between
# EVERY pair of candidates, unconditionally. The ceiling it was protecting --
# "10-20 verifications/hour from one IP" -- is a property of the MAIL SERVER
# being probed, not of our process: probing acme.com and then globex.com costs
# two different servers one connection each and needs no spacing at all. The
# unconditional sleep therefore bought nothing against the limit it named,
# while costing up to 36 minutes per lead (6 candidates x 360s). A 50-lead
# product took 2.5-5 hours; the 2026-10-02 run was 2h20m in at lead 18 of 50.
#
# So the delay is now keyed on the MX host actually being contacted. Probes to
# the same mail server are spaced; probes to different ones are not. Because
# many domains share one provider (every Google Workspace tenant resolves to
# aspmx.l.google.com), this also correctly throttles two different COMPANIES
# that happen to sit behind the same server -- which the old per-call sleep
# never did.
#
# Two tiers, because the hyperscalers are built for exactly this volume and a
# small self-hosted server is not. Treating them identically means either
# hammering the small one or crawling at the big one's expense.
SAME_MX_DELAY_SECONDS = 60.0
HYPERSCALE_MX_DELAY_SECONDS = 15.0

HYPERSCALE_MX_SUFFIXES = (
    "google.com", "googlemail.com",          # aspmx.l.google.com
    "protection.outlook.com", "outlook.com", # Microsoft 365
    "pphosted.com", "ppe-hosted.com",        # Proofpoint
    "mimecast.com", "mimecast.co.za",
    "messagelabs.com",                       # Broadcom/Symantec
    "amazonaws.com",                         # SES inbound
    "zoho.com", "zohomail.com",
    "qq.com", "mail.ru",
)

# MX host -> monotonic timestamp of the last probe we sent it.
_mx_last_probe: dict[str, float] = {}
# domain -> MX host, so one run never re-resolves the same domain.
_mx_cache: dict[str, Optional[str]] = {}

SMTP_TIMEOUT_SECONDS = 10
SMTP_PORT = 25


def reset_mx_throttle_state() -> None:
    """Clear the throttle and MX caches. For tests, and for a long-lived
    process that wants a clean slate between runs."""
    _mx_last_probe.clear()
    _mx_cache.clear()


def mx_delay_for(mx_host: Optional[str]) -> float:
    """How long two consecutive probes to this mail server must be spaced."""
    if not mx_host:
        return SAME_MX_DELAY_SECONDS
    host = mx_host.lower().rstrip(".")
    if any(host == s or host.endswith("." + s) for s in HYPERSCALE_MX_SUFFIXES):
        return HYPERSCALE_MX_DELAY_SECONDS
    return SAME_MX_DELAY_SECONDS


def throttle_for_mx(
    mx_host: Optional[str],
    *,
    sleep: Optional[Any] = None,
    clock: Optional[Any] = None,
) -> float:
    """Wait only as long as THIS mail server still needs, then record the
    probe. Returns the seconds actually slept (0.0 when no wait was due),
    so a caller can report real pacing cost rather than estimating it.
    """
    now = (clock or time.monotonic)()
    key = (mx_host or "").lower().rstrip(".")
    required = mx_delay_for(mx_host)
    last = _mx_last_probe.get(key)
    slept = 0.0
    if last is not None:
        remaining = required - (now - last)
        if remaining > 0:
            (sleep or time.sleep)(remaining)
            slept = remaining
            now = now + remaining
    _mx_last_probe[key] = now
    return slept

# All six common shapes (Kelvin's decision 3a, 2026-10-01). The first four
# were the original set; "f.lastname" and "firstname_lastname" were added
# after the 2026-10-01 runs graded 10 of 19 contacts "invalid" -- a verdict
# that only means "none of the patterns we TRIED exists", which is a much
# weaker claim than "this person has no address".
_PATTERN_BUILDERS = {
    "firstname": lambda first, last: f"{first}@{{domain}}" if first else None,
    "firstname.lastname": lambda first, last: f"{first}.{last}@{{domain}}" if first and last else None,
    "flastname": lambda first, last: f"{first[0]}{last}@{{domain}}" if first and last else None,
    "firstnamelastname": lambda first, last: f"{first}{last}@{{domain}}" if first and last else None,
    "f.lastname": lambda first, last: f"{first[0]}.{last}@{{domain}}" if first and last else None,
    "firstname_lastname": lambda first, last: f"{first}_{last}@{{domain}}" if first and last else None,
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


def _mx_host(domain: str, resolver: Optional[Any] = None, use_cache: bool = True) -> Optional[str]:
    """The domain's lowest-preference MX host, or None.

    Cached per domain: find_email now needs the MX before each probe (to
    decide the throttle), and re-resolving the same domain 6 times in a row
    would add latency and DNS traffic for an answer that cannot change
    mid-run. A None result is cached too -- "this domain has no MX" is an
    answer, and retrying it per candidate just repeats the same timeout.
    """
    if use_cache and domain in _mx_cache:
        return _mx_cache[domain]
    resolve = (resolver or dns.resolver).resolve
    try:
        answers = resolve(domain, "MX")
        best = min(answers, key=lambda r: r.preference)
        host = str(best.exchange).rstrip(".")
    except Exception:
        host = None
    if use_cache:
        _mx_cache[domain] = host
    return host


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
    sleep: Optional[Any] = None,
    clock: Optional[Any] = None,
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

    # Resolved once per domain; also decides the throttle tier below.
    mx_host = _mx_host(domain, resolver=resolver)

    best_result: Optional[EmailResult] = None
    for i, email in enumerate(candidates):
        pattern = seen_patterns[i] if i < len(seen_patterns) else "unknown"

        # Throttle BEFORE the probe, keyed on the server about to be
        # contacted -- not after, and not per candidate. The first probe to a
        # given MX in a run waits not at all; a later probe to the SAME server
        # waits only the remainder of that server's interval. Probes to a
        # different domain on a different server never wait on each other.
        throttle_for_mx(mx_host, sleep=sleep, clock=clock)

        verification = verify_email(email, domain=domain, smtp_client=smtp_client, resolver=resolver)
        record_attempt(domain, pattern, success=verification.status == "verified", supabase_client=supabase_client)

        confidence = {"verified": 0.95, "catch_all": 0.4, "unverified": 0.2, "invalid": 0.0}[verification.status]
        result = EmailResult(email=email, pattern_used=pattern, verification_status=verification.status, confidence_score=confidence)

        if verification.status == "verified":
            return result
        if best_result is None or confidence > best_result.confidence_score:
            best_result = result

    return best_result or EmailResult(email=None, pattern_used=None, verification_status="unverified", confidence_score=0.0)
