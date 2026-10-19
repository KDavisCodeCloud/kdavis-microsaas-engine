# REFACTOR CANDIDATE: over 300 lines.
"""
agents/marketing/hn_scout.py

Hacker News hiring threads as a lead source (Kelvin, 2026-10-18).

Reads the monthly "Ask HN: Who is hiring?" and "Ask HN: Freelancer? Seeking
freelancer?" threads through the OFFICIAL Hacker News API
(hacker-news.firebaseio.com/v0) -- documented, unauthenticated JSON, no page
scraping and no Brave quota.

WHERE THE THREADS COME FROM. "Who is hiring?" is posted by the `whoishiring`
account. The freelancer thread was too until October 2025; since November
2025 it has been posted by `jon_north` (checked 2026-10-18). The API has no
search, so threads are found from those accounts' own submission lists --
THREAD_POSTERS below. If the poster changes again the scout reports
`threads_found` short of the expected two rather than silently reading
nothing.

WHAT IS KEPT
  * A top-level post mentioning DevOps, SRE / site reliability, platform
    engineering, Azure or Terraform (INFRA_TERMS).
  * "contract" / "fractional" are recorded as an intent marker, NOT a reason
    to keep a post on their own: in the October 2026 thread, 21 of 22 posts
    that said "contract" without an infra term were iOS, UX, sales and
    frontend roles. Keeping them would fill the manual lane with work the
    consulting offer does not do.
  * In the freelancer thread, only "SEEKING FREELANCER" posts. "SEEKING WORK"
    posts are other freelancers selling the same thing -- competitors, not
    buyers. (From May to October 2026 there was ONE buyer post in that
    thread, so this source is mostly "Who is hiring?".)

WHAT IS WRITTEN. One mse_leads row per company: source='hn_thread',
consulting product, lead_route='manual_linkedin', status
'company_qualified' / contact_status 'pending' -- the Find-the-Buyer lane --
with the post text in signal_text and the HN item URL in job_posting_url.
The poster's HN handle is recorded in notes; it is a public username, not a
claim about who the buyer is.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import httpx

from agents.marketing.company_classification import (
    CONSULTING_EXCLUSION_POLICY,
    classify_company,
    normalise_company_name,
)
from agents.marketing.lead_qualification import extract_stack

log = logging.getLogger(__name__)

HN_API = "https://hacker-news.firebaseio.com/v0"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
THREAD_POSTERS = ("whoishiring", "jon_north")
WHO_IS_HIRING = "who_is_hiring"
FREELANCER = "freelancer"
SIGNAL_TEXT_MAX = 4000

# The user's list: DevOps / SRE / platform / Azure / Terraform. "platform" is
# narrowed to platform ENGINEERING -- bare "platform" appeared in 90 of 266
# October posts, almost all as "our platform".
INFRA_TERMS = re.compile(
    r"\b(?:dev\s?ops|devsecops|SRE|site[\s-]reliability|platform[\s-](?:engineer(?:s|ing)?|team|infrastructure)"
    r"|azure|terraform)\b",
    re.IGNORECASE,
)
ENGAGEMENT_TERMS = re.compile(r"\b(?:contract(?:or)?|fractional|part[\s-]time|freelance)\b", re.IGNORECASE)
SEEKING_FREELANCER = re.compile(r"^\W*seeking\s+freelancers?\b", re.IGNORECASE)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_NOT_COMPANY_HOSTS = (
    "news.ycombinator.com", "linkedin.com", "github.com", "twitter.com", "x.com",
    "docs.google.com", "forms.gle", "calendly.com", "notion.so", "notion.site",
    "lever.co", "greenhouse.io", "ashbyhq.com", "workable.com", "wellfound.com",
    "angel.co", "ycombinator.com", "bit.ly", "youtube.com", "medium.com",
)


def _get_json(url: str, http_get: Callable[..., Any]) -> Any:
    r = http_get(url, timeout=20, headers={"User-Agent": "THD-Agentic-Systems/1.0 (+https://thdstack.com)"})
    r.raise_for_status()
    return r.json()


def post_text(raw_html: Optional[str]) -> str:
    """HN item text is HTML (<p>, <a>, entities). Plain text, paragraphs kept."""
    t = re.sub(r"<p>", "\n", raw_html or "", flags=re.I)
    t = re.sub(r"<a\s[^>]*href=\"([^\"]+)\"[^>]*>.*?</a>", r"\1", t, flags=re.I | re.S)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def thread_kind(title: Optional[str]) -> Optional[str]:
    t = (title or "").lower()
    if t.startswith("ask hn: who is hiring"):
        return WHO_IS_HIRING
    if t.startswith("ask hn: freelancer? seeking freelancer"):
        return FREELANCER
    return None


def find_threads(http_get: Callable[..., Any], *, months: int = 1,
                 posters: Iterable[str] = THREAD_POSTERS) -> list[dict]:
    """The latest `months` thread(s) of each kind, newest first."""
    found: dict[str, list[dict]] = {WHO_IS_HIRING: [], FREELANCER: []}
    for poster in posters:
        try:
            user = _get_json(f"{HN_API}/user/{poster}.json", http_get) or {}
        except Exception as exc:
            log.warning("[HN] user %s unreadable: %s", poster, exc)
            continue
        for item_id in (user.get("submitted") or [])[:40]:
            if all(len(v) >= months for v in found.values()):
                break
            try:
                item = _get_json(f"{HN_API}/item/{item_id}.json", http_get) or {}
            except Exception:
                continue
            kind = thread_kind(item.get("title"))
            if kind and len(found[kind]) < months and not item.get("dead"):
                found[kind].append(item)
    return [t for kind in (WHO_IS_HIRING, FREELANCER) for t in found[kind]]


@dataclass
class HnPost:
    item_id: int
    thread_kind: str
    thread_title: str
    by: Optional[str]
    posted_at: Optional[datetime]
    text: str

    @property
    def first_line(self) -> str:
        return next((ln.strip() for ln in self.text.splitlines() if ln.strip()), "")

    @property
    def url(self) -> str:
        return HN_ITEM_URL.format(id=self.item_id)


def company_from_post(post: HnPost) -> Optional[str]:
    """The company, from the conventional "Company | Role | Location" header.

    None when the header is not in that shape -- "Founding Engineer --
    Stealth Project" names no company, and inventing one from the role text
    would be a false claim on the lead."""
    line = post.first_line
    if post.thread_kind == FREELANCER:
        line = SEEKING_FREELANCER.sub("", line).lstrip(" |:-–—")
    if "|" not in line:
        return None
    head = line.split("|", 1)[0].strip(" -–—:")
    head = re.sub(r"\s*\((?:https?://)?[^)]*\)\s*$", "", head).strip()   # "Acme (acme.com)"
    head = _URL_RE.sub("", head).strip(" -–—:")
    if not head or len(head) > 60 or INFRA_TERMS.fullmatch(head):
        return None
    if re.search(r"\b(?:stealth|confidential|undisclosed|early[- ]stage startup)\b", head, re.I):
        return None
    return head


def domain_from_post(post: HnPost, company: Optional[str]) -> Optional[str]:
    """The company's own site, if the post links one. ATS/social/doc hosts
    are not the company's domain."""
    candidates = []
    for url in _URL_RE.findall(post.text):
        host = (urlparse(url).hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        if not host or any(host == h or host.endswith("." + h) for h in _NOT_COMPANY_HOSTS):
            continue
        candidates.append(host)
    if not candidates:
        return None
    key = normalise_company_name(company)
    for host in candidates:  # prefer a host that spells the company name
        if key and key[:6] in normalise_company_name(host):
            return host
    return candidates[0]


def keep_post(post: HnPost) -> tuple[bool, str, list[str]]:
    """(keep, reason, matched infra terms)."""
    if post.thread_kind == FREELANCER and not SEEKING_FREELANCER.search(post.text[:200]):
        return False, "freelancer_thread_seeking_work", []
    terms = sorted({m.group(0).lower() for m in INFRA_TERMS.finditer(post.text)})
    if not terms:
        return False, "no_infra_term", []
    return True, "infra_term", terms


@dataclass
class HnStats:
    threads_found: int = 0
    threads: list[str] = field(default_factory=list)
    posts_read: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    kept: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    leads_qualified: int = 0

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["dropped_total"] = sum(self.dropped.values())
        return d


def find_hn_leads(
    product_id: str,
    *,
    http_get: Optional[Callable[..., Any]] = None,
    months: int = 1,
    existing_companies: Optional[set] = None,
    existing_domains: Optional[set] = None,
    stats: Optional[HnStats] = None,
) -> tuple[list[dict], HnStats]:
    """Qualified mse_leads rows (not yet written) and the funnel."""
    http_get = http_get or httpx.get
    stats = stats if stats is not None else HnStats()
    existing_companies = set(existing_companies or set())
    existing_domains = set(existing_domains or set())

    threads = find_threads(http_get, months=months)
    stats.threads_found = len(threads)
    stats.threads = [t.get("title") or str(t.get("id")) for t in threads]

    rows: list[dict] = []
    for thread in threads:
        kind = thread_kind(thread.get("title")) or WHO_IS_HIRING
        for kid in thread.get("kids") or []:
            try:
                item = _get_json(f"{HN_API}/item/{kid}.json", http_get) or {}
            except Exception:
                stats.drop("item_unreadable")
                continue
            if item.get("deleted") or item.get("dead") or not item.get("text"):
                stats.drop("deleted_or_dead")
                continue
            stats.posts_read += 1
            post = HnPost(
                item_id=item["id"], thread_kind=kind, thread_title=thread.get("title") or "",
                by=item.get("by"), text=post_text(item.get("text")),
                posted_at=(datetime.fromtimestamp(item["time"], tz=timezone.utc) if item.get("time") else None),
            )
            keep, reason, terms = keep_post(post)
            if not keep:
                stats.drop(reason)
                continue
            company = company_from_post(post)
            if not company:
                stats.drop("no_company_in_header")
                continue
            classification = classify_company(company, post.text)
            excluded, _mult, reasons = CONSULTING_EXCLUSION_POLICY.decide(classification)
            if excluded:
                tag = reasons[0].split(":", 1)[1].split("=", 1)[0] if reasons else "excluded"
                stats.excluded[tag] = stats.excluded.get(tag, 0) + 1
                continue
            domain = domain_from_post(post, company)
            key = normalise_company_name(company)
            if (key and key in existing_companies) or (domain and domain in existing_domains):
                stats.duplicates += 1
                continue
            stats.kept += 1
            existing_companies.add(key)
            if domain:
                existing_domains.add(domain)

            engagement = sorted({m.group(0).lower() for m in ENGAGEMENT_TERMS.finditer(post.text)})
            stack = extract_stack(post.text)
            reasons_out = [f"HN {post.thread_title}", f"mentions {', '.join(terms)}"]
            if engagement:
                reasons_out.append(f"engagement: {', '.join(engagement)}")
            if stack:
                reasons_out.append(f"stack: {', '.join(stack)}")
            rows.append({
                "product_id": product_id,
                "source": "hn_thread",
                "company": company,
                "domain": domain,
                "status": "company_qualified",
                "contact_status": "pending",
                "lead_route": "manual_linkedin",
                "job_posting_url": post.url,
                "job_posting_title": post.first_line[:200],
                "job_posting_date": post.posted_at.date().isoformat() if post.posted_at else None,
                "stack_tags": stack or None,
                "score_reasons": reasons_out,
                "signal_text": post.text[:SIGNAL_TEXT_MAX],
                "notes": f"Posted on Hacker News by {post.by}" if post.by else None,
            })
    stats.leads_qualified = len(rows)
    return rows, stats
