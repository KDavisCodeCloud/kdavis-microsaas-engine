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
# Threads are monthly; one older than this is last month's, not current.
STALE_THREAD_DAYS = 40

# The user's list: DevOps / SRE / platform / Azure / Terraform. "platform" is
# narrowed to platform ENGINEERING -- bare "platform" appeared in 90 of 266
# October posts, almost all as "our platform".
INFRA_TERMS = re.compile(
    r"\b(?:dev\s?ops|devsecops|SRE|site[\s-]reliability|platform[\s-](?:engineer(?:s|ing)?|team|infrastructure)"
    # 2026-11-15 (lead-sources brief, "relevant infra roles"): infrastructure
    # and cloud ROLES, not the bare words -- "cloud" alone is in most posts.
    r"|infrastructure[\s-]engineer(?:s|ing)?|cloud[\s-](?:engineer(?:s|ing)?|infrastructure|architect)"
    r"|azure|terraform)\b",
    re.IGNORECASE,
)
ENGAGEMENT_TERMS = re.compile(r"\b(?:contract(?:or)?|fractional|part[\s-]time|freelance)\b", re.IGNORECASE)
SEEKING_FREELANCER = re.compile(r"^\W*seeking\s+freelancers?\b", re.IGNORECASE)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
# Real TLDs only: "Node.js" or "Vue.js" in a header is not a domain.
_BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w.])((?:[a-z0-9-]+\.)+(?:com|io|ai|co|dev|app|org|net|tech|inc|us|eu|so|xyz|pe|de|fr|uk|nl|se|fi"
    r"|ca|au|in|health|bio|me|sh|gg|vc|ly|to|cloud|systems|software|works|studio|finance|energy|space))(?![\w.])",
    re.IGNORECASE)
_NOT_COMPANY_HOSTS = (
    "news.ycombinator.com", "linkedin.com", "github.com", "twitter.com", "x.com",
    "docs.google.com", "forms.gle", "calendly.com", "notion.so", "notion.site",
    "lever.co", "greenhouse.io", "ashbyhq.com", "workable.com", "wellfound.com",
    "angel.co", "ycombinator.com", "bit.ly", "youtube.com", "medium.com",
    # ATS and job-board short links. grnh.se is Greenhouse's: Chronograph's
    # first-run domain was stored as grnh.se.
    "grnh.se", "lnkd.in", "dover.com", "workatastartup.com", "recruitee.com", "bamboohr.com",
    "breezy.hr", "smartrecruiters.com", "teamtailor.com", "jobvite.com", "icims.com",
    "myworkdayjobs.com", "rippling-ats.com", "personio.de", "personio.com", "join.com", "gem.com",
    # 2026-11-15 live run: an ATS and a job board taken as company domains.
    "pinpointhq.com", "devitjobs.com", "devitjobs.uk", "welcometothejungle.com", "otta.com",
    "builtin.com", "hiring.cafe", "indeed.com", "glassdoor.com", "ziprecruiter.com",
)
# careers.acme.com / jobs.acme.com -> acme.com
_CAREERS_SUBDOMAIN = re.compile(r"^(?:careers?|jobs|apply|hiring|work)\.(?=[^.]+\.[^.]+)")


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
                 posters: Iterable[str] = THREAD_POSTERS,
                 now: Optional[datetime] = None) -> list[dict]:
    """The latest `months` thread(s) of each kind, newest first, ACROSS
    posters.

    Picked by the thread's own timestamp, not by which poster is read first:
    `whoishiring` still has its October 2025 freelancer thread among its
    recent submissions, and taking the first match from the first poster read
    that stale thread instead of jon_north's current one (live run,
    2026-10-18). A thread older than STALE_THREAD_DAYS per month requested is
    ignored rather than read as if it were current."""
    now = now or datetime.now(timezone.utc)
    candidates: dict[str, list[dict]] = {WHO_IS_HIRING: [], FREELANCER: []}
    for poster in posters:
        try:
            user = _get_json(f"{HN_API}/user/{poster}.json", http_get) or {}
        except Exception as exc:
            log.warning("[HN] user %s unreadable: %s", poster, exc)
            continue
        per_poster = {WHO_IS_HIRING: 0, FREELANCER: 0}
        for item_id in (user.get("submitted") or [])[:40]:
            if all(n >= months for n in per_poster.values()):
                break
            try:
                item = _get_json(f"{HN_API}/item/{item_id}.json", http_get) or {}
            except Exception:
                continue
            kind = thread_kind(item.get("title"))
            if kind and per_poster[kind] < months and not item.get("dead"):
                per_poster[kind] += 1
                candidates[kind].append(item)
    cutoff = now.timestamp() - STALE_THREAD_DAYS * 86400 * months
    out: list[dict] = []
    for kind in (WHO_IS_HIRING, FREELANCER):
        fresh = sorted((t for t in candidates[kind] if (t.get("time") or 0) >= cutoff),
                       key=lambda t: t.get("time") or 0, reverse=True)
        out.extend(fresh[:months])
    return out


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
    # Bare domains in the header ("Chronograph (chronograph.pe) | ...") count
    # too -- many posts name their site without a scheme.
    urls = _URL_RE.findall(post.text)
    urls += [f"https://{d}" for d in _BARE_DOMAIN_RE.findall(_URL_RE.sub(" ", post.first_line))]
    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        if not host or any(host == h or host.endswith("." + h) for h in _NOT_COMPANY_HOSTS):
            continue
        candidates.append(_CAREERS_SUBDOMAIN.sub("", host))
    if not candidates:
        return None
    key = normalise_company_name(company)
    for host in candidates:  # prefer a host that spells the company name
        if key and key[:6] in normalise_company_name(host):
            return host
    return candidates[0]


# ── Post fields and the poster as a candidate buyer (2026-11-15) ──────────

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# HN authors obfuscate: "jane [at] acme [dot] com", "jane at acme dot com".
_OBFUSCATED_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\{at\}|\sat\s)\s*([A-Za-z0-9-]+)"
    r"\s*(?:\[dot\]|\(dot\)|\{dot\}|\sdot\s|\.)\s*([A-Za-z]{2,})\b", re.IGNORECASE)
_REMOTE_RE = re.compile(r"\b(?:remote|wfh|work from home|distributed)\b", re.IGNORECASE)
_ONSITE_RE = re.compile(r"\b(?:onsite|on-site|in[- ]office|hybrid)\b", re.IGNORECASE)
# The poster says who they are: "I'm the CTO", "CTO here", "co-founder here".
_LEADER_ROLE = (r"(?:co-?founder(?:\s*(?:&|and|/)\s*CTO)?|founder|CTO|CEO|chief technology officer"
                r"|VP(?:\s+of)?\s+Engineering|head\s+of\s+(?:engineering|platform|infrastructure)"
                r"|director\s+of\s+engineering)")
_SELF_ROLE_RE = re.compile(
    rf"\b(?:I['’]?m|I\s+am)\s+(?:the\s+|a\s+|one\s+of\s+the\s+)?({_LEADER_ROLE})\b"
    rf"|\b({_LEADER_ROLE})\s+here\b", re.IGNORECASE)
_LOCATION_HINT = re.compile(r"\b(?:remote|onsite|on-site|hybrid|[A-Z][a-z]+,\s*[A-Z]{2}\b|NYC|SF|London|Berlin"
                            r"|Toronto|Austin|Seattle|Boston|USA|US|EU|UK|Europe)", re.IGNORECASE)


ROLE_MAILBOXES = frozenset({
    "jobs", "job", "careers", "career", "hiring", "hire", "hr", "people", "talent", "recruiting",
    "recruitment", "recruiter", "apply", "applications", "work", "join", "joinus", "info", "hello", "hi",
    "team", "contact", "admin", "office", "engineering", "eng", "dev", "tech", "founders", "support",
})


@dataclass
class PostFields:
    roles: Optional[str] = None
    location: Optional[str] = None
    remote: Optional[bool] = None
    urls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)


def post_fields(post: "HnPost") -> PostFields:
    """The conventional header ("Company | Role | Location | REMOTE | URL")
    split into fields, plus every URL and contact email in the post. A field
    the post does not state stays None -- nothing is inferred."""
    parts = [p.strip() for p in post.first_line.split("|")]
    out = PostFields()
    if len(parts) >= 2:
        rest = parts[1:]
        role_parts = [p for p in rest if not _URL_RE.search(p) and not _BARE_DOMAIN_RE.search(p)
                      and (INFRA_TERMS.search(p) or re.search(
            r"\b(?:engineer|developer|architect|lead|manager|director|head|scientist)\b", p, re.I))]
        loc_parts = [p for p in rest if p not in role_parts and not _URL_RE.search(p)
                     and _LOCATION_HINT.search(p)]
        out.roles = "; ".join(role_parts)[:200] or None
        out.location = ", ".join(loc_parts)[:120] or None
    if _REMOTE_RE.search(post.first_line):
        out.remote = True
    elif _ONSITE_RE.search(post.first_line):
        out.remote = False
    out.urls = list(dict.fromkeys(_URL_RE.findall(post.text)))[:10]
    emails = [e.lower() for e in _EMAIL_RE.findall(post.text)]
    emails += [f"{a}@{b}.{c}".lower() for a, b, c in _OBFUSCATED_EMAIL_RE.findall(post.text)]
    out.emails = [e for e in dict.fromkeys(emails) if not e.endswith(("@example.com", "@ycombinator.com"))]
    return out


def candidate_buyer(post: "HnPost", domain: Optional[str], fields: PostFields) -> Optional[dict]:
    """The POSTER as a candidate buyer, only when the post itself says they
    are a founder / CTO / engineering leader. Never a verified contact: the
    email (if any) still goes through MX + SMTP verification in the buyer
    resolver, and a name is recorded only when the post states one.

    Returns a buyer_candidates entry or None."""
    from agents.marketing.buyer_gate import ALLOW, classify_buyer_title, registrable_domain
    from agents.marketing.contact_discovery import _contact_from_text

    named = _contact_from_text(post.text, "hn_post")
    self_role = _SELF_ROLE_RE.search(post.text)
    title = (named.title if named and named.title else None) or (
        (self_role.group(1) or self_role.group(2)) if self_role else None)
    if not title:
        return None
    verdict = classify_buyer_title(title)
    if verdict.verdict != ALLOW:
        return None
    # A role mailbox (jobs@, careers@) is not the person; only a personal
    # address on the company's own domain is worth an SMTP probe.
    company_emails = [e for e in fields.emails
                      if domain and registrable_domain(e) == registrable_domain(domain)
                      and e.split("@", 1)[0] not in ROLE_MAILBOXES]
    return {
        "name": named.name if named and named.is_usable else None,
        "title": title.strip(),
        "email": company_emails[0] if company_emails else None,
        "email_grade": None,  # unverified until the resolver probes it
        "source": "hn_poster",
        "confidence": "medium" if (named and named.is_usable) else "low",
        "evidence": (named.evidence if named and named.evidence else
                     post.text[max(0, self_role.start() - 60): self_role.end() + 60].strip()
                     if self_role else None),
        "evidence_url": post.url,
        "hn_handle": post.by,
    }


HEADER_INTENT = 0.45   # the infra role is in the post's own header line
BODY_INTENT = 0.20     # an infra term appears only in the body
ENGAGEMENT_INTENT = 0.10


def score_post(post: HnPost, domain: Optional[str]) -> tuple[float, float, list[str]]:
    """(fit, intent, reasons) on the same 0-1 scales as the ATS pipeline, so
    HN leads rank among the others in Find the Buyer instead of floating to
    the top on a NULL score.

    Fit reuses lead_qualification.company_fit_score (stack overlap and a
    resolved domain; size is unknown from a post). Intent separates an
    advertised infra ROLE (term in the header line) from a passing mention."""
    from agents.marketing.lead_qualification import company_fit_score
    stack = extract_stack(post.text)
    fit = company_fit_score(stack=stack, size_proxy_ok=False, domain_resolved=bool(domain))
    header = bool(INFRA_TERMS.search(post.first_line))
    intent = HEADER_INTENT if header else BODY_INTENT
    reasons = ["infra role in the post header" if header else "infra term in the body only"]
    if ENGAGEMENT_TERMS.search(post.text):
        intent += ENGAGEMENT_INTENT
        reasons.append("open to contract/fractional")
    return fit.value, round(min(1.0, intent), 3), reasons + list(fit.reasons)


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
    candidate_buyers: int = 0
    posts_with_email: int = 0

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
            # Buyer gate, company half (2026-11-07): a resolved company domain
            # is required, and boards / ATS hosts / competitors never qualify.
            from agents.marketing.buyer_gate import company_block_reason
            block = company_block_reason(company, domain)
            if block:
                stats.drop(block.split(":", 1)[0].replace(" ", "_"))
                continue
            stats.kept += 1
            existing_companies.add(key)
            if domain:
                existing_domains.add(domain)

            fields = post_fields(post)
            candidate = candidate_buyer(post, domain, fields)
            if candidate:
                stats.candidate_buyers += 1
            if fields.emails:
                stats.posts_with_email += 1
            engagement = sorted({m.group(0).lower() for m in ENGAGEMENT_TERMS.finditer(post.text)})
            stack = extract_stack(post.text)
            fit, intent, score_reasons = score_post(post, domain)
            reasons_out = [f"HN {post.thread_title}", f"mentions {', '.join(terms)}"] + score_reasons
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
                "job_posting_title": (fields.roles or post.first_line)[:200],
                "location": (fields.location or ("Remote" if fields.remote else None)),
                "buyer_candidates": [candidate] if candidate else None,
                "hn_post_fields": {"remote": fields.remote, "urls": fields.urls,
                                   "emails": fields.emails, "header": post.first_line[:300]},
                "job_posting_date": post.posted_at.date().isoformat() if post.posted_at else None,
                "stack_tags": stack or None,
                "fit_score": fit,
                "intent_score": intent,
                "score_reasons": reasons_out,
                "signal_text": post.text[:SIGNAL_TEXT_MAX],
                "notes": f"Posted on Hacker News by {post.by}" if post.by else None,
            })
    stats.leads_qualified = len(rows)
    return rows, stats
