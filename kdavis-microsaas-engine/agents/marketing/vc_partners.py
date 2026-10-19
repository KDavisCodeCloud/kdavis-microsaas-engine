# REFACTOR CANDIDATE: over 300 lines.
"""
agents/marketing/vc_partners.py

VC operating partners (Kelvin, 2026-10-18).

TWO OUTPUTS FROM ONE CURATED LIST (mse_vc_funds, ~25 seed-to-Series-B funds
backing B2B SaaS and infrastructure):

  1. PARTNER CONTACTS. Each fund's own team page, read for operating,
     platform and talent partners -- the people portfolio founders ask "who
     do you know who can fix our infrastructure?". Written to the partner
     lane: source='vc_operating_partner', manual LinkedIn, partner-style
     copy.

  2. A COMPANY SIGNAL. Each fund's own portfolio page lists its companies.
     Those are stored (mse_vc_portfolio_companies); a portfolio company with
     an open infrastructure role gets mse_leads.funded_by and an intent boost
     (company_first_sourcing.VC_FUNDED_INTENT_BOOST). Portfolio companies
     with no known ATS board are probed on the ATS public JSON APIs (no
     Brave cost) and any board found joins mse_ats_board_tokens, so the
     weekly prospect scout reads it for free.

WHAT IS READ. Only the fund's own pages, robots.txt honoured. Many fund
sites render their team page in JavaScript; for those the plain HTML holds no
names, the fund is recorded as last_team_status='no_contacts_in_html', and
nothing is guessed. No LinkedIn, no Crunchbase, no directories. Zero Brave
queries.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urljoin, urlparse

import httpx

from agents.marketing.company_classification import distinctive_company_name, normalise_company_name
from agents.marketing.contact_discovery import _allowed, adjacent_name_title_pairs, page_segments
from agents.marketing.contact_fit import VC_PLATFORM_TITLE_RE
from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID
from agents.marketing.role_taxonomy import CONSULTING_FRESHNESS, RoleTaxonomy
from agents.marketing.role_taxonomy import matching_postings as taxonomy_matching_postings
from scrapers.ats_boards import AtsBoardClient, BoardRef

log = logging.getLogger(__name__)

USER_AGENT = "THD-Agentic-Systems/1.0 (+https://thdstack.com)"
HTTP_TIMEOUT_SECONDS = 15
MAX_CONTACTS_PER_FUND = 3
PROBE_PROVIDERS = ("greenhouse", "ashby", "lever")

# Operating partners for functions nobody refers infrastructure work
# through. A talent partner stays: they hear "we can't hire a platform
# engineer" first.
_OFF_FUNCTION = re.compile(
    r"\b(?:marketing|communications?|finance|legal|general\s+counsel|go[\s-]to[\s-]market|GTM|sales"
    r"|design|brand|content|events?|investor\s+relations|fundraising|accounting|bio"
    r"|business\s+development|CFO|chief\s+financial|people\s+operations)\b",
    re.IGNORECASE,
)
_RANK = (
    (re.compile(r"platform", re.I), 0),
    (re.compile(r"engineering|technical|technology|infrastructure|security|cto", re.I), 1),
    (re.compile(r"talent", re.I), 2),
    (re.compile(r"operating\s+partner|portfolio", re.I), 3),
)

# Hosts on a portfolio page that are not portfolio companies.
_NOT_COMPANY = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "medium.com", "substack.com", "github.com", "apple.com", "google.com", "spotify.com",
    "crunchbase.com", "techcrunch.com", "bloomberg.com", "forbes.com", "wsj.com", "nytimes.com",
    "businesswire.com", "prnewswire.com", "vimeo.com", "tiktok.com", "threads.net", "bsky.app",
    "wikipedia.org", "calendly.com", "typeform.com", "hubspot.com", "mailchimp.com", "squarespace.com",
    "webflow.io", "wix.com", "wordpress.com", "notion.so", "notion.site", "carta.com", "angel.co",
    "wellfound.com", "ycombinator.com", "podcasts.apple.com", "open.spotify.com", "goo.gl", "bit.ly",
)

_A_RE = re.compile(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)


def parse_team_page(page_html: str) -> list[dict]:
    """[{name, title}] for operating / platform / talent roles, ranked
    platform > engineering > talent > generic operating partner, with
    off-function operating partners (GTM, finance, marketing) dropped."""
    found = []
    for name, title in adjacent_name_title_pairs(page_segments(page_html), VC_PLATFORM_TITLE_RE):
        if _OFF_FUNCTION.search(title) and not re.search(r"talent|platform|engineering|technical|security", title, re.I):
            continue
        found.append({"name": name, "title": title})
    return sorted(found, key=lambda c: next((r for p, r in _RANK if p.search(c["title"])), 9))


def parse_portfolio_page(page_html: str, fund_domain: str) -> list[dict]:
    """[{company, domain}] from the outbound links on a fund's portfolio
    page. A link back to the fund itself, or to social/media/tooling hosts,
    is not a portfolio company."""
    fund_host = fund_domain.lower()
    fund_host = fund_host[4:] if fund_host.startswith("www.") else fund_host
    out: dict[str, dict] = {}
    for href, inner in _A_RE.findall(page_html or ""):
        host = (urlparse(href).hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        if not host or "." not in host or host.endswith(fund_host):
            continue
        if any(host == h or host.endswith("." + h) for h in _NOT_COMPANY):
            continue
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split())
        if not label or len(label) > 40 or label.lower() in ("website", "visit", "visit site", "learn more", "read more"):
            label = host.split(".")[0].replace("-", " ").title()
        out.setdefault(host, {"company": label, "domain": host})
    return list(out.values())


def _fetch(url: str, http_get: Callable[..., Any]) -> tuple[Optional[str], str]:
    """(html or None, status) with robots.txt honoured."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not _allowed(host, parsed.path or "/", http_get):
        return None, "robots_disallowed"
    try:
        r = http_get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    except Exception as exc:
        return None, f"request_error:{type(exc).__name__}"
    if getattr(r, "status_code", 0) != 200:
        return None, f"http_{getattr(r, 'status_code', 0)}"
    return getattr(r, "text", "") or "", "ok"


def probe_board(company: str, domain: str, client: AtsBoardClient, taxonomy: RoleTaxonomy) -> dict:
    """Does this portfolio company run a public ATS board, and does it have
    an open infra role? Tries the domain stem as the board token on each
    provider's public JSON API.

    A stem match is accepted only when it can be tied to the company: the
    board's own company name names it, or the stem is distinctive (5+
    characters). "box" on Greenhouse could be anyone's."""
    stem = domain.split(".")[0].lower()
    for provider in PROBE_PROVIDERS:
        ref = BoardRef(provider=provider, token=stem)
        postings = client.fetch_board(ref)
        if client.board_status.get(ref.cache_key) == "failed":
            continue
        board_company = next((p.company for p in postings if p.company), None)
        tied = (len(stem) >= 5) or (board_company and
                                    distinctive_company_name(board_company) == distinctive_company_name(company))
        if not tied:
            continue
        matched, _drops = taxonomy_matching_postings(postings, taxonomy, freshness=CONSULTING_FRESHNESS)
        return {"board_key": ref.cache_key, "board_status": "ok" if postings else "empty",
                "infra_roles": len(matched), "board_company": board_company or company, "ref": ref}
    return {"board_key": None, "board_status": "none_found", "infra_roles": 0}


@dataclass
class VcStats:
    funds: int = 0
    team_pages_read: int = 0
    team_status: dict[str, int] = field(default_factory=dict)
    contacts_found: int = 0
    contacts_duplicate: int = 0
    portfolio_pages_read: int = 0
    portfolio_companies: int = 0
    boards_probed: int = 0
    boards_found: int = 0
    portfolio_with_infra_roles: int = 0
    prospect_leads_tagged: int = 0
    leads_qualified: int = 0
    per_fund: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def find_vc_contacts(
    funds: Iterable[dict],
    *,
    http_get: Optional[Callable[..., Any]] = None,
    existing_people: Optional[set] = None,
    stats: Optional[VcStats] = None,
) -> tuple[list[dict], dict[str, list[dict]], VcStats]:
    """(partner lead rows, {fund_id: portfolio companies}, stats). No DB."""
    http_get = http_get or httpx.get
    stats = stats if stats is not None else VcStats()
    existing_people = set(existing_people or set())
    rows: list[dict] = []
    portfolios: dict[str, list[dict]] = {}
    for fund in funds:
        stats.funds += 1
        name, domain = fund["name"], fund["domain"]
        per = {"team": "no_team_url", "contacts": 0, "portfolio": "no_portfolio_url", "companies": 0}

        if fund.get("team_url"):
            page, status = _fetch(fund["team_url"], http_get)
            contacts = parse_team_page(page) if page else []
            per["team"] = status if status != "ok" else ("ok" if contacts else "no_contacts_in_html")
            if page:
                stats.team_pages_read += 1
            for c in contacts[:MAX_CONTACTS_PER_FUND]:
                key = f"{normalise_company_name(name)}:{normalise_company_name(c['name'])}"
                if key in existing_people:
                    stats.contacts_duplicate += 1
                    continue
                existing_people.add(key)
                first, _, last = c["name"].partition(" ")
                rows.append({
                    "product_id": PARTNERS_PRODUCT_ID,
                    "source": "vc_operating_partner",
                    "partner_type": "vc_operating_partner",
                    "company": name,
                    "domain": domain,
                    "domain_source": "already_known",
                    "first_name": first,
                    "last_name": last or None,
                    "title": c["title"],
                    "seniority": "c_level",
                    "status": "pending_dm",
                    "contact_status": "found",
                    "lead_route": "manual_linkedin",
                    "job_posting_url": fund["team_url"],
                    "signal_text": (f"{c['name']} is listed as \"{c['title']}\" on {name}'s own team page "
                                    f"({fund['team_url']}). {name} invests {fund.get('stage_focus') or 'early-stage'}."),
                    "score_reasons": [f"fund={name}", f"team page title: {c['title']}"],
                    "company_tags": ["vc_operating_partner"],
                })
                per["contacts"] += 1
                stats.contacts_found += 1
        stats.team_status[per["team"]] = stats.team_status.get(per["team"], 0) + 1

        if fund.get("portfolio_url"):
            page, status = _fetch(fund["portfolio_url"], http_get)
            companies = parse_portfolio_page(page, domain) if page else []
            per["portfolio"] = status if status != "ok" else ("ok" if companies else "no_links_in_html")
            if page:
                stats.portfolio_pages_read += 1
            portfolios[fund["id"]] = companies
            per["companies"] = len(companies)
            stats.portfolio_companies += len(companies)
        stats.per_fund[name] = per
    stats.leads_qualified = len(rows)
    return rows, portfolios, stats


def run_vc_scout(db, *, http_get=None, max_probes: int = 60, probe_budget_seconds: float = 600,
                 sleep=None, stats: Optional[VcStats] = None) -> tuple[list[dict], VcStats]:
    """Contacts + portfolio store + board probing + funded_by tagging.
    Returns (partner rows to write, stats); writes everything else itself."""
    from agents.marketing.company_first_sourcing import (
        VC_FUNDED_INTENT_BOOST,
        PortfolioIndex,
        queue_board_tokens,
    )
    from agents.marketing.mkt_lead_finder import PROSPECT_SIGNAL_SOURCES

    stats = stats if stats is not None else VcStats()
    funds = (db.table("mse_vc_funds").select("*").eq("active", True).execute().data or [])
    existing = db.table("mse_leads").select("company,first_name,last_name").eq(
        "source", "vc_operating_partner").execute().data or []
    existing_people = {f"{normalise_company_name(r.get('company'))}:"
                       f"{normalise_company_name(' '.join(x for x in (r.get('first_name'), r.get('last_name')) if x))}"
                       for r in existing}
    rows, portfolios, stats = find_vc_contacts(funds, http_get=http_get, existing_people=existing_people, stats=stats)

    now_iso = datetime.now(timezone.utc).isoformat()
    for fund in funds:
        per = stats.per_fund.get(fund["name"], {})
        db.table("mse_vc_funds").update({
            "last_team_status": per.get("team"), "last_portfolio_status": per.get("portfolio"),
            "last_checked_at": now_iso,
        }).eq("id", fund["id"]).execute()

    # Portfolio store. One upsert per company keeps first_seen_at honest.
    cached = (db.table("mse_ats_board_tokens").select("provider,board_token,company_domain")
              .execute().data or [])
    known_boards = {(r.get("company_domain") or "").lower(): f"{r['provider']}:{r['board_token']}"
                    for r in cached if r.get("company_domain")}
    cached_keys = {f"{r['provider']}:{r['board_token']}" for r in cached}
    stored = {(r["fund_id"], r["domain"]): r for r in
              (db.table("mse_vc_portfolio_companies").select("fund_id,domain,board_status")
               .limit(20000).execute().data or [])}
    to_probe: list[tuple[str, dict]] = []
    for fund_id, companies in portfolios.items():
        for c in companies:
            row = {"fund_id": fund_id, "company": c["company"], "domain": c["domain"], "last_seen_at": now_iso}
            board = known_boards.get(c["domain"])
            if board:
                row.update(board_key=board, board_status="ok")
            elif (fund_id, c["domain"]) not in stored or not stored[(fund_id, c["domain"])].get("board_status"):
                to_probe.append((fund_id, c))
                row["board_status"] = "not_probed"
            db.table("mse_vc_portfolio_companies").upsert(row, on_conflict="fund_id,domain").execute()

    # Board probing, bounded by count and wall clock. Free: ATS JSON APIs.
    client = AtsBoardClient(sleep=sleep)
    taxonomy = RoleTaxonomy.from_config(None)
    started = time.monotonic()
    probed_domains: dict[str, dict] = {}
    new_refs: dict[str, BoardRef] = {}
    for fund_id, c in to_probe:
        if stats.boards_probed >= max_probes or time.monotonic() - started > probe_budget_seconds:
            break
        result = probed_domains.get(c["domain"])
        if result is None:
            result = probe_board(c["company"], c["domain"], client, taxonomy)
            probed_domains[c["domain"]] = result
            stats.boards_probed += 1
            if result.get("ref"):
                stats.boards_found += 1
                if result["ref"].cache_key not in cached_keys:
                    new_refs[result["ref"].cache_key] = result["ref"]
                if result["infra_roles"]:
                    stats.portfolio_with_infra_roles += 1
        db.table("mse_vc_portfolio_companies").update({
            "board_key": result.get("board_key"), "board_status": result["board_status"],
            "infra_roles": result.get("infra_roles"),
        }).eq("fund_id", fund_id).eq("domain", c["domain"]).execute()
    if new_refs:
        queue_board_tokens(db, new_refs, None)
        for key, ref in new_refs.items():
            dom = next(d for d, r in probed_domains.items() if r.get("board_key") == key)
            db.table("mse_ats_board_tokens").update({
                "company_domain": dom, "discovered_from": "vc_portfolio",
            }).eq("provider", ref.provider).eq("board_token", ref.token).execute()

    # funded_by on existing prospect leads. Those leads were qualified on an
    # open infrastructure role, so a portfolio match is exactly "portfolio
    # company with an open infra role". The boost is applied once, when the
    # tag first appears; a rerun updates the fund list without re-boosting.
    index = PortfolioIndex.load(db)
    for source in PROSPECT_SIGNAL_SOURCES:
        leads = (db.table("mse_leads").select("id,company,domain,funded_by,intent_score,score_reasons")
                 .eq("source", source).execute().data or [])
        for lead in leads:
            funds_for = index.funds_for(lead.get("company"), lead.get("domain"))
            if not funds_for or sorted(lead.get("funded_by") or []) == funds_for:
                continue
            update: dict = {"funded_by": funds_for}
            if not lead.get("funded_by"):
                update["intent_score"] = round(min(1.0, float(lead.get("intent_score") or 0) + VC_FUNDED_INTENT_BOOST), 3)
                update["score_reasons"] = list(lead.get("score_reasons") or []) + [
                    f"VC-backed per portfolio page (+{VC_FUNDED_INTENT_BOOST:.2f}): {', '.join(funds_for)}"]
            db.table("mse_leads").update(update).eq("id", lead["id"]).execute()
            stats.prospect_leads_tagged += 1
    return rows, stats
