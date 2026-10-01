"""
agents/marketing/domain_resolution.py

Mandatory company-domain resolution for scraper v2 (Kelvin's decision 5,
2026-10-01).

WHY IT IS MANDATORY. The first live runs produced `routed_outbound_email =
0` on every single lead, because ATS feeds carry only the ATS's own URL
(job-boards.greenhouse.io/caylent/...) and never the company's website. No
domain means no email pattern, which means no SMTP verification, which
means no lead can ever reach MKT-O5. The email path was unreachable by
construction.

Resolution order, cheapest and most reliable first:

  (a) A company URL in the JD text.           free
  (b) The ATS board page's own company link.  free (already fetched)
  (c) token + .com/.io/.ai, VERIFIED by DNS   free, but must be verified
      and a homepage title match.
  (d) Brave fallback.                         costs quota, max 10/run

Step (c) is where a careless implementation invents facts. "caylent" +
".com" is a guess, not a resolution -- plenty of tokens resolve to a
parked page, a squatter, or an unrelated company with the same short name.
So a constructed candidate is accepted ONLY if DNS resolves AND the
homepage identifies itself as that company. Everything else returns None,
and a lead with no domain routes to the manual LinkedIn track rather than
carrying a fabricated one.

Email grading then runs through core/email_finder (pattern candidates ->
SMTP RCPT TO -> catch-all detection) and
agents.marketing.lead_qualification.grade_email. Only "valid" routes to
outbound email.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import httpx

from agents.marketing.company_classification import _normalise_company_name as _norm

log = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 12
MAX_BRAVE_DOMAIN_QUERIES = 10
CONSTRUCTED_TLDS = (".com", ".io", ".ai")

# Hosts that are never a company's own domain.
_NOT_COMPANY_HOSTS = frozenset({
    "greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "boards-api.greenhouse.io", "lever.co", "jobs.lever.co", "api.lever.co",
    "ashbyhq.com", "jobs.ashbyhq.com", "workable.com", "apply.workable.com",
    "linkedin.com", "www.linkedin.com", "twitter.com", "x.com", "facebook.com",
    "github.com", "youtube.com", "instagram.com", "medium.com", "glassdoor.com",
    "indeed.com", "google.com", "docs.google.com", "notion.so", "bit.ly",
    "crunchbase.com", "wellfound.com", "angel.co", "ziprecruiter.com",
    "gmail.com", "mailto", "calendly.com", "lnkd.in",
})

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
_BARE_DOMAIN_RE = re.compile(
    r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|io|ai|co|dev|net|org|cloud|tech|app))\b", re.I
)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


@dataclass
class DomainResolution:
    """The resolved domain plus how it was established. `method` is stored
    so a wrong domain can be traced to the step that produced it rather
    than being an unexplained value in a row."""

    domain: Optional[str] = None
    method: Optional[str] = None
    evidence: Optional[str] = None
    brave_queries_used: int = 0
    attempts: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return bool(self.domain)


def _registrable(host: str) -> str:
    """Strip a leading www. -- not a full PSL implementation, deliberately:
    a public-suffix list is a dependency and a data file to keep current,
    and every consumer here only needs 'is this the same site'."""
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_company_host(host: str) -> bool:
    host = _registrable(host)
    if not host or "." not in host:
        return False
    if host in _NOT_COMPANY_HOSTS:
        return False
    # Also reject a subdomain of a known non-company host
    return not any(host == h or host.endswith("." + h) for h in _NOT_COMPANY_HOSTS)


# ── (a) company URL in the JD text ───────────────────────────────────────

def from_jd_text(jd_text: Optional[str], company: Optional[str]) -> Optional[tuple[str, str]]:
    """(domain, evidence) from a URL or bare domain in the JD prose.

    Prefers a host whose name resembles the company, because a JD routinely
    links to unrelated third parties (a benefits provider, a conference, a
    blog post). Falling back to "first URL in the text" would attach those
    to the lead as its website.
    """
    if not jd_text:
        return None
    candidates: list[str] = []
    for match in _URL_RE.finditer(jd_text):
        host = _registrable(urlparse(match.group(0)).netloc)
        if _is_company_host(host):
            candidates.append(host)
    for match in _BARE_DOMAIN_RE.finditer(jd_text):
        host = _registrable(match.group(1))
        if _is_company_host(host):
            candidates.append(host)
    if not candidates:
        return None

    wanted = _norm(company or "")
    if wanted:
        for host in candidates:
            stem = _norm(host.split(".")[0])
            if stem and (stem in wanted or wanted in stem):
                return host, f"jd_url:{host}"
    # No name-resembling host: not confident enough to claim one.
    return None


# ── (b) the ATS board's own company link ─────────────────────────────────

def from_postings(postings: Iterable[Any]) -> Optional[tuple[str, str]]:
    """(domain, evidence) from a posting URL that points off the ATS host.
    Some boards (notably Workable) carry the customer's own careers URL."""
    for posting in postings:
        url = getattr(posting, "url", None)
        if not url:
            continue
        host = _registrable(urlparse(url).netloc)
        if _is_company_host(host):
            return host, f"posting_url:{host}"
    return None


# ── (c) constructed candidate, DNS + homepage verified ───────────────────

def _default_dns_resolves(host: str) -> bool:
    import socket

    try:
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False


def _homepage_identifies(host: str, company: Optional[str], http_get: Callable[..., Any]) -> Optional[str]:
    """The homepage <title> if it names the company, else None.

    This is the check that turns a GUESS into a resolution. Without it,
    "token + .com" attaches parked domains, squatters and unrelated
    same-name companies to leads as fact.
    """
    wanted = _norm(company or "")
    if not wanted:
        return None
    for scheme in ("https://", "http://"):
        try:
            response = http_get(
                f"{scheme}{host}", timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": "THD-Agentic-Systems/1.0 (+https://thdstack.com)"},
            )
        except Exception:
            continue
        if getattr(response, "status_code", 0) != 200:
            continue
        body = getattr(response, "text", "") or ""
        match = _TITLE_TAG_RE.search(body)
        haystack = _norm(match.group(1) if match else body[:4000])
        if wanted and wanted in haystack:
            title = (match.group(1).strip()[:80] if match else host)
            return title
    return None


def from_constructed(
    token: Optional[str],
    company: Optional[str],
    *,
    http_get: Optional[Callable[..., Any]] = None,
    dns_resolves: Optional[Callable[[str], bool]] = None,
    tlds: Iterable[str] = CONSTRUCTED_TLDS,
) -> Optional[tuple[str, str]]:
    """(domain, evidence) for token+TLD, ONLY when DNS resolves and the
    homepage identifies the company. Returns None otherwise -- a
    constructed domain that fails verification is a guess, and a guess in
    this field becomes a fabricated email address downstream."""
    http_get = http_get or httpx.get
    dns_resolves = dns_resolves or _default_dns_resolves
    stem = re.sub(r"[^a-z0-9-]", "", (token or "").lower())
    if not stem:
        return None
    for tld in tlds:
        host = f"{stem}{tld}"
        if not dns_resolves(host):
            continue
        title = _homepage_identifies(host, company, http_get)
        if title:
            return host, f"constructed+verified:{host} (title={title!r})"
        log.info("[DomainResolution] %s resolves but its homepage does not name %r", host, company)
    return None


# ── (d) Brave fallback ───────────────────────────────────────────────────

def from_brave(
    company: Optional[str],
    scraper,
    *,
    stats: Optional[dict] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> Optional[tuple[str, str]]:
    """(domain, evidence) from one Brave query for the company's own site.

    Budgeted by the caller (MAX_BRAVE_DOMAIN_QUERIES per run). The result
    must still resemble the company name -- Brave will happily return a
    directory listing or a competitor for an ambiguous name.
    """
    if not company or scraper is None or not getattr(scraper, "api_key", None):
        return None
    query = f'"{company}" official website'
    if sleep is not None:
        sleep(0)
    items = scraper._search(query)
    if stats is not None:
        stats["brave_domain_queries"] = stats.get("brave_domain_queries", 0) + 1

    wanted = _norm(company)
    for item in items:
        host = _registrable(urlparse(item.get("link") or "").netloc)
        if not _is_company_host(host):
            continue
        stem = _norm(host.split(".")[0])
        if stem and (stem in wanted or wanted in stem):
            return host, f"brave:{host}"
    return None


# ── Orchestrator ─────────────────────────────────────────────────────────

def resolve_domain(
    company: Optional[str],
    *,
    token: Optional[str] = None,
    jd_text: Optional[str] = None,
    postings: Optional[Iterable[Any]] = None,
    known_domain: Optional[str] = None,
    scraper=None,
    brave_budget_remaining: int = 0,
    http_get: Optional[Callable[..., Any]] = None,
    dns_resolves: Optional[Callable[[str], bool]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    stats: Optional[dict] = None,
) -> DomainResolution:
    """
    Run the (a) -> (d) chain and stop at the first VERIFIED answer.

    Returns a DomainResolution whose `domain` is None when nothing could be
    established. That is a real, expected outcome and the caller routes
    such a lead to the manual LinkedIn track -- it is never filled in with
    a plausible-looking guess.
    """
    result = DomainResolution()

    if known_domain and _is_company_host(known_domain):
        result.domain, result.method, result.evidence = _registrable(known_domain), "already_known", known_domain
        return result

    # (a) JD text
    result.attempts.append("jd_text")
    found = from_jd_text(jd_text, company)
    if found:
        result.domain, result.method, result.evidence = found[0], "jd_text", found[1]
        return result

    # (b) ATS posting links
    result.attempts.append("posting_url")
    found = from_postings(postings or [])
    if found:
        result.domain, result.method, result.evidence = found[0], "posting_url", found[1]
        return result

    # (c) constructed + verified
    result.attempts.append("constructed")
    found = from_constructed(token, company, http_get=http_get, dns_resolves=dns_resolves)
    if found:
        result.domain, result.method, result.evidence = found[0], "constructed_verified", found[1]
        return result

    # (d) Brave, budgeted
    if brave_budget_remaining > 0:
        result.attempts.append("brave")
        found = from_brave(company, scraper, stats=stats, sleep=sleep)
        result.brave_queries_used = 1
        if found:
            result.domain, result.method, result.evidence = found[0], "brave", found[1]
            return result

    log.info("[DomainResolution] no domain established for %r (tried %s)", company, result.attempts)
    return result
