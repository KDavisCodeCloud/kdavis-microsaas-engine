# REFACTOR CANDIDATE: over 300 lines.
"""
agents/marketing/partner_sourcing.py

The PARTNER lane (Kelvin, 2026-10-18): referral partners, kept separate from
prospects.

WHO. AI development / automation agencies, fractional CISOs, and SOC 2 /
HIPAA / ISO compliance and audit firms. Each already has the client and the
deadline. The pitch is "I'm the implementation partner your clients need after
an audit" or "your white-label cloud and IAM arm" -- a partnership note, never
a sales sequence.

THE ONE EXCEPTION TO THE EXCLUSION RULES. The prospect pipeline drops every
consultancy, MSP and cloud partner (company_classification.py). Here those ARE
the target, so this lane uses PARTNER_EXCLUSION_POLICY, which drops only
staffing firms. The prospect policies are untouched: a partner firm still can
never become a consulting or Cloud Decoded prospect.

HOW, cheapest first:
  1. Brave search with the queries in the partner ICP row
     (mse_icp_configs.search_templates) -- the only metered step besides 4.
  2. Each result's own site: homepage, robots.txt honoured, read for evidence
     that the firm actually does the segment's work. A blog post ranking for
     "fractional CISO" is not a fractional CISO firm.
  3. Founder / managing partner from the firm's own team or about page
     (contact_discovery.from_company_pages with the partner vocabulary).
  4. Only if the site names nobody: one Brave query against LinkedIn's public
     profile index, read from the result title (no linkedin.com fetch). This
     also yields the profile URL the manual lane needs before approval.

No Clutch, no directories, no LinkedIn fetching. Routed manual LinkedIn.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import httpx

from agents.marketing.company_classification import (
    STAFFING,
    ExclusionPolicy,
    classify_company,
    normalise_company_name,
    text_mentions_company,
)
from agents.marketing.contact_discovery import (
    ContactDiscoveryStats,
    _allowed,
    _clean_text,
    from_company_pages,
)
from agents.marketing.company_first_sourcing import _pace
from agents.marketing.contact_fit import PARTNER_FIRM_TITLE_RE, evaluate_partner_contact_fit
from agents.marketing.lead_qualification import parse_person_result

log = logging.getLogger(__name__)

PARTNERS_PRODUCT_ID = "c0e8aaf3-e858-4df4-b0ba-863580556a18"  # mse_products.slug='thd-partners'

USER_AGENT = "THD-Agentic-Systems/1.0 (+https://thdstack.com)"
HTTP_TIMEOUT_SECONDS = 12
SIGNAL_TEXT_MAX = 1200

# Partner lane: service firms are the target. Only staffing is out.
PARTNER_EXCLUSION_POLICY = ExclusionPolicy(exclude_tags=(STAFFING,))

PARTNER_TEAM_PATHS = ("/about", "/team", "/about-us", "/our-team", "/leadership", "/company", "/people")

# Hosts that are directories, marketplaces, media or platforms -- never a
# firm's own site. Clutch is excluded by instruction.
_NOT_A_FIRM_HOSTS = (
    "clutch.co", "upwork.com", "fiverr.com", "g2.com", "capterra.com", "goodfirms.co",
    "designrush.com", "sortlist.com", "techbehemoths.com", "themanifest.com", "toptal.com",
    "linkedin.com", "reddit.com", "quora.com", "medium.com", "youtube.com", "facebook.com",
    "twitter.com", "x.com", "instagram.com", "forbes.com", "wikipedia.org", "gartner.com",
    "crunchbase.com", "glassdoor.com", "indeed.com", "ziprecruiter.com", "builtin.com",
    "github.com", "substack.com", "vanta.com", "drata.com", "secureframe.com", "aicpa-cima.com",
    "aicpa.org", "hhs.gov", "iso.org", "amazon.com", "microsoft.com", "google.com",
    "ibm.com", "accenture.com", "deloitte.com", "pwc.com", "ey.com", "kpmg.com",
    "techcrunch.com", "businessinsider.com", "inc.com", "entrepreneur.com", "hbr.org",
)

# The firm's own homepage must say it does the segment's work.
SEGMENT_EVIDENCE: dict[str, re.Pattern] = {
    "ai_agency": re.compile(
        r"\b(?:AI|artificial\s+intelligence|generative\s+AI|LLM|automation)\b[^.]{0,60}?"
        r"\b(?:agency|studio|consultanc(?:y|ies)|development\s+(?:company|firm|partner|services)|"
        r"solutions\s+for|services|we\s+build|build\s+custom)\b"
        r"|\bwe\s+(?:build|design|develop|deploy)\b[^.]{0,40}?\b(?:AI|agents?|automations?|LLM)\b",
        re.IGNORECASE),
    "fractional_ciso": re.compile(
        r"\b(?:fractional|virtual|part[\s-]time|outsourced)\s+(?:CISO|chief\s+information\s+security\s+officer)\b"
        r"|\bvCISO\b", re.IGNORECASE),
    "compliance_firm": re.compile(
        r"\b(?:SOC\s?-?2|HIPAA|ISO\s?-?27001|ISO\s?-?42001|HITRUST|PCI\s?DSS)\b[^.]{0,80}?"
        r"\b(?:audits?|auditors?|readiness|assessments?|attestations?|certifications?|compliance\s+(?:services|consulting|program))\b"
        r"|\b(?:audits?|readiness|assessments?|attestations?|certifications?)\b[^.]{0,60}?"
        r"\b(?:SOC\s?-?2|HIPAA|ISO\s?-?27001|HITRUST)\b",
        re.IGNORECASE),
}

# A firm that DOES the work for clients -- not a course on starting one, a
# creator teaching it, or a software vendor selling a platform to such firms.
# Each pattern below caught a real false positive in the 2026-10-18 run:
# "Launch My AI Automation Agency" (sells the agency-in-a-box course),
# Nate Herk (an educator), Cynomi (a vCISO SaaS platform sold to MSSPs).
_NOT_A_SERVICE_FIRM = {
    "course_or_educator": re.compile(
        r"\b(?:online\s+course|masterclass|bootcamp|cohort|my\s+course|enroll(?:\s+now)?|"
        r"start\s+(?:an|your\s+own)\s+(?:AI\s+)?(?:automation\s+)?agency|"
        r"(?:educator|creator|youtuber)\b|teaches\s+(?:founders|operators|people)|"
        r"join\s+(?:my|the)\s+(?:community|skool))", re.IGNORECASE),
    # Digital Reference ("The Video-First Platform to Find Professionals"):
    # a directory whose category list contains every keyword above.
    "directory_or_marketplace": re.compile(
        r"\b(?:platform|marketplace|directory|network)\s+(?:to|for)\s+(?:find|hire|connect\s+with|discover)\b"
        r"|\bfind\s+(?:and\s+hire\s+)?(?:the\s+)?(?:top\s+|best\s+)?(?:professionals|freelancers|experts|agencies|consultants)\b",
        re.IGNORECASE),
    "software_vendor": re.compile(
        r"\b(?:start\s+(?:a|your)\s+free\s+trial|request\s+a\s+demo|book\s+a\s+demo|"
        r"leverage\s+\w+(?:\u2019s|'s)?\s+[\w\s]{0,30}?platform|our\s+platform\s+(?:helps|enables|lets|automates))\b",
        re.IGNORECASE),
}

# AI agencies need a second, shape-of-the-business signal: "Artificial
# Intelligence ... Professional Services" alone matched a legal portfolio site.
_AGENCY_SHAPE = re.compile(
    r"\b(?:agency|studio|consultanc(?:y|ies)|development\s+(?:company|agency|partner|firm|services)|"
    r"we\s+(?:build|design|develop|deliver)|software\s+development)\b", re.IGNORECASE)

# And it must be a service business: someone who has clients.
_SERVICE_EVIDENCE = re.compile(
    r"\bour\s+clients\b|\bclients?\b[^.]{0,30}\b(?:trust|rely|work|served)\b|\bwe\s+help\b"
    r"|\b(?:services|consulting|agency|firm|advisory|engagements?)\b",
    re.IGNORECASE)

_SITE_NAME_RE = re.compile(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_RE = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', re.I)


def firm_host(url: str) -> Optional[str]:
    host = (urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if not host or "." not in host:
        return None
    if any(host == h or host.endswith("." + h) for h in _NOT_A_FIRM_HOSTS):
        return None
    if host.endswith(".gov") or host.endswith(".edu"):
        return None
    return host


def _brand(text: str) -> str:
    """The brand before a tagline: "Nate Herk · AI automation made easy",
    "Digital Reference - The Video-First Platform..." -> the first part."""
    return re.split(r"\s*[:·|]\s+|\s+[-–—]\s+", text.strip())[0].strip()


def firm_name(page_html: str, host: str) -> str:
    """og:site_name, else the <title>, each trimmed to the brand; else the
    host stem."""
    stem = host.split(".")[0]
    m = _SITE_NAME_RE.search(page_html or "")
    if m:
        name = _brand(html.unescape(m.group(1)))
        if 1 < len(name) <= 60:
            return name
    m = _TITLE_RE.search(page_html or "")
    if m:
        segments = [html.unescape(x).strip() for x in re.split(r"\s+[|–—-]\s+|\s*\|\s*", m.group(1)) if x.strip()]
        for seg in segments:  # prefer the segment that spells the domain
            if stem[:5] and stem[:5] in normalise_company_name(seg):
                return _brand(seg)[:60]
        if segments and len(_brand(segments[-1])) <= 40:
            return _brand(segments[-1])[:60]
    return stem.replace("-", " ").title()


@dataclass
class PartnerStats:
    queries: int = 0
    queries_contact: int = 0
    results_seen: int = 0
    dropped_not_a_firm_host: int = 0
    unique_firms: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    by_segment: dict[str, int] = field(default_factory=dict)
    contact_from_site: int = 0
    contact_from_brave: int = 0
    contact_pending: int = 0
    contact_testimonial_rejected: int = 0
    duplicates: int = 0
    leads_qualified: int = 0
    contact_discovery: dict = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["queries_total"] = self.queries + self.queries_contact
        d["dropped_total"] = sum(self.dropped.values())
        return d


def find_partner_founder(scraper, firm: str, *, stats: PartnerStats) -> dict:
    """One Brave query against LinkedIn's public profile index. Reads the
    result title only ("Jane Doe - Founder - Acme | LinkedIn"); the firm must
    appear in the result, or the person is someone else's founder."""
    query = f'site:linkedin.com/in "{firm}" ("Founder" OR "Managing Partner" OR "CEO")'
    items = scraper._search(query)
    stats.queries_contact += 1
    for item in items or []:
        person = parse_person_result(item)
        if not (person.name and person.title):
            continue
        if not PARTNER_FIRM_TITLE_RE.search(person.title):
            continue
        if not text_mentions_company(html.unescape(f"{item.get('title', '')} {item.get('snippet', '')}"), firm):
            continue
        return {"name": person.name, "title": person.title, "profile_url": item.get("link")}
    return {}


def find_partner_leads(
    templates: Iterable[dict],
    *,
    scraper,
    http_get: Optional[Callable[..., Any]] = None,
    max_queries: int = 30,
    max_firms: int = 25,
    existing_domains: Optional[set] = None,
    existing_companies: Optional[set] = None,
    existing_linkedin_urls: Optional[set] = None,
    stats: Optional[PartnerStats] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> tuple[list[dict], PartnerStats]:
    """Qualified partner rows (not yet written) and the funnel.

    `max_queries` is a HARD Brave cap across discovery and contact lookups.
    Discovery runs first and is capped at the number of templates; whatever
    remains pays for founder lookups on firms whose site named nobody.
    """
    http_get = http_get or httpx.get
    stats = stats if stats is not None else PartnerStats()
    existing_domains = set(existing_domains or set())
    existing_companies = set(existing_companies or set())
    existing_linkedin_urls = set(existing_linkedin_urls or set())

    # 1. Discovery.
    firms: dict[str, str] = {}  # host -> segment that found it
    for t in templates:
        if stats.queries >= max_queries:
            break
        segment, query = (t or {}).get("segment"), (t or {}).get("query")
        if segment not in SEGMENT_EVIDENCE or not query:
            continue
        if stats.queries:
            _pace(sleep)
        items = scraper._search(query)
        stats.queries += 1
        for item in items or []:
            stats.results_seen += 1
            host = firm_host(item.get("link") or "")
            if not host:
                stats.dropped_not_a_firm_host += 1
                continue
            firms.setdefault(host, segment)
    stats.unique_firms = len(firms)

    # 2-4. Qualify each firm on its own site.
    rows: list[dict] = []
    contact_stats = ContactDiscoveryStats()
    for host, segment in list(firms.items())[:max_firms]:
        if host in existing_domains:
            stats.duplicates += 1
            continue
        if not _allowed(host, "/", http_get):
            stats.drop("robots_disallowed")
            continue
        try:
            resp = http_get(f"https://{host}/", timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True,
                            headers={"User-Agent": USER_AGENT})
        except Exception:
            stats.drop("homepage_unreachable")
            continue
        if getattr(resp, "status_code", 0) != 200:
            stats.drop(f"homepage_http_{getattr(resp, 'status_code', 0)}")
            continue
        page = getattr(resp, "text", "") or ""
        # The head matters: a directory says what it is in its <title> and
        # og:site_name, which tag-stripping the body throws away.
        head_bits = [m.group(1) for rx in (_TITLE_RE, _SITE_NAME_RE, _DESC_RE) for m in [rx.search(page)] if m]
        text = " ".join([html.unescape(" ".join(head_bits)), _clean_text(page)])
        evidence = SEGMENT_EVIDENCE[segment].search(text)
        if not evidence:
            # Re-check the other segments: a firm found by a CISO query may
            # really be a compliance firm, and that is still a partner.
            for other, pattern in SEGMENT_EVIDENCE.items():
                evidence = pattern.search(text)
                if evidence:
                    segment = other
                    break
        if not evidence:
            stats.drop("site_does_not_do_the_work")
            continue
        if not _SERVICE_EVIDENCE.search(text):
            stats.drop("not_a_service_business")
            continue
        if segment == "ai_agency" and not _AGENCY_SHAPE.search(text):
            stats.drop("not_an_agency")
            continue
        not_firm = next((reason for reason, pat in _NOT_A_SERVICE_FIRM.items() if pat.search(text)), None)
        if not_firm:
            stats.drop(not_firm)
            continue
        name = firm_name(page, host)
        excluded, _mult, reasons = PARTNER_EXCLUSION_POLICY.decide(classify_company(name, text))
        if excluded:
            stats.drop("excluded_staffing")
            continue
        key = normalise_company_name(name)
        if key and key in existing_companies:
            stats.duplicates += 1
            continue

        # Contact: the firm's own pages first, Brave last.
        contact: dict = {}
        site_contact = from_company_pages(host, http_get=http_get, paths=PARTNER_TEAM_PATHS,
                                          stats=contact_stats, max_pages=3,
                                          title_re=PARTNER_FIRM_TITLE_RE)
        if site_contact and site_contact.is_usable and _names_another_company(site_contact.title, name):
            # "Founder, eData Enterprise" on 75way's page is a client
            # testimonial, not 75way's founder.
            stats.contact_testimonial_rejected += 1
            site_contact = None
        if site_contact and site_contact.is_usable:
            contact = {"name": site_contact.name, "title": site_contact.title, "profile_url": None,
                       "source": site_contact.source}
            stats.contact_from_site += 1
        elif stats.queries + stats.queries_contact < max_queries:
            _pace(sleep)
            found = find_partner_founder(scraper, name, stats=stats)
            if found and found.get("profile_url") not in existing_linkedin_urls:
                contact = dict(found, source="brave_linkedin_index")
                stats.contact_from_brave += 1
        if contact and not evaluate_partner_contact_fit(contact.get("title"), segment).accepted:
            contact = {}
        if not contact:
            stats.contact_pending += 1
        elif contact.get("profile_url"):
            existing_linkedin_urls.add(contact["profile_url"])

        first, last = _split(contact.get("name"))
        description = (_DESC_RE.search(page).group(1).strip() if _DESC_RE.search(page) else "")
        signal = " ".join(x for x in (html.unescape(description), f"Evidence: \"{evidence.group(0).strip()}\"") if x)
        stats.by_segment[segment] = stats.by_segment.get(segment, 0) + 1
        existing_domains.add(host)
        if key:
            existing_companies.add(key)
        rows.append({
            "product_id": PARTNERS_PRODUCT_ID,
            "source": "partner_signal",
            "partner_type": segment,
            "company": name,
            "domain": host,
            "domain_source": "already_known",
            "first_name": first,
            "last_name": last,
            "title": contact.get("title"),
            "linkedin_url": contact.get("profile_url"),
            "status": "pending_dm" if contact else "company_qualified",
            "contact_status": "found" if contact else "pending",
            "lead_route": "manual_linkedin",
            "seniority": "c_level" if contact else None,
            "signal_text": signal[:SIGNAL_TEXT_MAX],
            "score_reasons": [
                f"segment={segment}",
                f"site evidence: {evidence.group(0).strip()[:120]}",
                f"contact via {contact.get('source')}" if contact else "founder not named on site",
            ],
            "company_tags": [segment],
        })
    stats.contact_discovery = contact_stats.as_dict()
    stats.leads_qualified = len(rows)
    return rows, stats


_TITLE_ORG_TAIL = re.compile(r"(?:,|\s@|\bat\b)\s*([A-Za-z0-9][\w&.' -]{1,60})$")


def _names_another_company(title: Optional[str], firm: str) -> bool:
    """True when the title is attributed to a different organisation --
    the shape of a client testimonial ("Founder, Acme"), not the firm's own
    leader ("Co-Founder @ Bright Defense")."""
    m = _TITLE_ORG_TAIL.search((title or "").strip())
    if not m:
        return False
    tail = m.group(1).strip()
    if PARTNER_FIRM_TITLE_RE.fullmatch(tail) or re.fullmatch(r"(?i)(?:managing\s+partner|ceo|cto|founder)", tail):
        return False  # "Founder, CEO" is two titles, not a company
    return not text_mentions_company(tail, firm) and not text_mentions_company(firm, tail)


def _split(name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not name or not name.strip():
        return None, None
    parts = name.strip().split()
    return (parts[0], None) if len(parts) == 1 else (parts[0], " ".join(parts[1:]))
