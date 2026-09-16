"""
Company public-site signal scanner (THD Consulting lead scout, Source 3
alongside email discovery). Fetches ONE page — the candidate company's own
public homepage, already discovered via scrapers/google_search.py or a
vertical scraper — and reads it for security-posture signals a human doing
manual prospecting would also look for: does this company already mention
an IT vendor/MSP relationship, or a security certification, that would make
them a poor fit for THD's IT-security-implementation service.

Same rules as scrapers/google_search.py: robots.txt-checked before fetching,
official public page only (never a login-gated page, never LinkedIn/Indeed/
Glassdoor/Yelp/Clutch), 2-5s randomized delay is the caller's responsibility
(this module makes one request per call and does not loop). Raw HTML is
parsed and discarded in this same call — only the boolean/string signals
below are ever returned or stored, per "never store raw HTML."
"""

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

REQUEST_TIMEOUT_SECONDS = 10

_USER_AGENT = "THDConsultingLeadScout/1.0 (+https://thdagentic.com)"

_MSP_TERMS = [
    "managed service provider", "managed it services", "managed it provider",
    "our it partner", "it support provided by", "powered by",  # generic co-branding footer
    "msp partner",
]
_CERT_TERMS = [
    "soc 2", "soc2", "iso 27001", "iso/iec 27001", "hipaa compliant",
    "hipaa-compliant", "pci dss", "pci-dss",
]
_DEDICATED_IT_TERMS = [
    "it department", "information security team", "ciso", "chief information security officer",
    "director of it", "it manager", "network administrator",
]
_OUTDATED_STACK_HINTS = [
    ("generator", re.compile(r"wordpress\s*([0-9]+\.[0-9]+)?", re.I)),
]


@dataclass
class CompanySignals:
    reachable: bool = False
    mentions_msp: bool = False
    mentions_security_cert: bool = False
    mentions_dedicated_it: bool = False
    outdated_stack_hint: Optional[str] = None
    server_header: Optional[str] = None


def _robots_allowed(url: str, get_fn) -> bool:
    """Fail-closed, matching scrapers/google_search.py's own convention."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = get_fn(robots_url, timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT})
        if response.status_code >= 400:
            return True
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch(_USER_AGENT, url)
    except Exception:
        return False


def fetch_company_signals(domain: str, http_get=None) -> CompanySignals:
    """Best-effort single fetch of https://{domain} — returns
    CompanySignals(reachable=False) on any error, timeout, or robots.txt
    disallow rather than raising, so one unreachable site never fails a
    whole scrape run."""
    get_fn = http_get or (lambda url, **kw: httpx.get(url, **kw))
    url = f"https://{domain}"

    if not _robots_allowed(url, get_fn):
        return CompanySignals(reachable=False)

    try:
        response = get_fn(url, timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT}, follow_redirects=True)
        if response.status_code >= 400:
            return CompanySignals(reachable=False)
    except Exception:
        return CompanySignals(reachable=False)

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True).lower()

    generator_meta = soup.find("meta", attrs={"name": "generator"})
    generator_content = (generator_meta.get("content") or "") if generator_meta else ""

    outdated_hint = None
    match = _OUTDATED_STACK_HINTS[0][1].search(generator_content)
    if match:
        outdated_hint = match.group(0)

    signals = CompanySignals(
        reachable=True,
        mentions_msp=any(term in text for term in _MSP_TERMS),
        mentions_security_cert=any(term in text for term in _CERT_TERMS),
        mentions_dedicated_it=any(term in text for term in _DEDICATED_IT_TERMS),
        outdated_stack_hint=outdated_hint,
        server_header=response.headers.get("Server"),
    )
    return signals
