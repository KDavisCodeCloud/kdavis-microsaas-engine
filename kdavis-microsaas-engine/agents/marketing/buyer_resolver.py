# REFACTOR CANDIDATE: over 300 lines (five sources + email + outcome).
"""
agents/marketing/buyer_resolver.py

Auto-fill Find the Buyer (lead-source expansion, Phase 5, 2026-11-15).

For every company in the Find the Buyer lane (company qualified, no named
buyer), try these sources in the brief's order and record source and
confidence for each candidate:

  1. form_d        SEC Form D executive officers (Phase 3, already on the lead)
  2. company_page  the company's own /team, /about, /leadership, /company ...
                   pages, robots.txt honoured (contact_discovery)
  3. github        public members of the company's GitHub org -- the org is
                   accepted only when its own `blog`/`email` field is on the
                   lead's domain -- whose profile bio states a buyer title
                   and names the company
  4. hn_poster     the HN poster who said they are the founder/CTO (Phase 2)
  5. search_snippet  OFF unless ENABLE_SEARCH_SNIPPET_RESOLVER=1: Brave
                   '"CTO" "<company>"', name + title parsed from result
                   TITLES only, never a fetched page; its own Brave share

Then the existing email path (pattern + SMTP RCPT + catch-all probe, the same
grading as a human-saved contact):

  * VERIFIED email + buyer-gate ALLOW title -> the contact is created
    (status pending_dm, lead_route outbound_email); MKT-O2 drafts it into
    HITL at 8am. Nothing is sent without approval and a 'valid' grade.
  * a name but no verified email, or an AMBIGUOUS title -> the card is
    PRE-FILLED (buyer_suggestion) with source + evidence for a one-click
    confirm by a human.
  * nothing -> the card stays manual.

Every outcome is written to the lead (buyer_resolution) and the run's funnel.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx

log = logging.getLogger(__name__)

SOURCE_ORDER = ("form_d", "company_page", "github", "hn_poster", "search_snippet")
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
MAX_EMAIL_ATTEMPTS_PER_LEAD = 2
RECHECK_AFTER = timedelta(days=14)
GITHUB_API = "https://api.github.com"
GITHUB_MAX_MEMBERS = 40

OUTCOME_RESOLVED = "auto_resolved"
OUTCOME_PREFILLED = "prefilled"
OUTCOME_MANUAL = "manual"

# A buyer title stated in a GitHub bio ("CTO @acme", "Co-founder & CTO at Acme").
_BIO_TITLE = re.compile(
    r"\b(co-?founder(?:\s*(?:&|and|/)\s*CTO)?|founder|CTO|chief technology officer|VP(?:\s+of)?\s+engineering"
    r"|head\s+of\s+(?:engineering|platform|infrastructure|devops|sre)|director\s+of\s+(?:engineering|platform"
    r"|infrastructure))\b", re.IGNORECASE)


def snippet_resolver_enabled() -> bool:
    return (os.getenv("ENABLE_SEARCH_SNIPPET_RESOLVER") or "").strip().lower() in ("1", "true", "yes", "on")


def _candidate(name, title, source, confidence, evidence, evidence_url=None, email=None) -> dict:
    from agents.marketing.buyer_gate import classify_buyer_title
    return {"name": name, "title": title, "source": source, "confidence": confidence,
            "title_verdict": classify_buyer_title(title).verdict if title else "ambiguous",
            "evidence": (evidence or "")[:300] or None, "evidence_url": evidence_url,
            "email": email, "email_grade": None}


# ── sources ───────────────────────────────────────────────────────────────

def from_company_pages(lead: dict, http_get=None) -> list[dict]:
    from agents.marketing.contact_discovery import from_company_pages as walk
    c = walk(lead.get("domain"), http_get=http_get, max_pages=5)
    if not (c and c.is_usable):
        return []
    url = f"https://{lead['domain']}{(c.source or '').split(':', 1)[-1]}"
    return [_candidate(c.name, c.title, "company_page", "medium", c.evidence, url)]


def _gh_get(path: str, http_get, token: Optional[str]):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "THD-Agentic-Systems/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = http_get(f"{GITHUB_API}{path}", headers=headers, timeout=15)
    return r.json() if getattr(r, "status_code", 0) == 200 else None


def github_org_for(lead: dict, http_get, token) -> Optional[str]:
    """The company's GitHub org, accepted only when the org's own blog or
    email is on the lead's domain -- a name match alone would hand us a
    stranger's org."""
    from agents.marketing.buyer_gate import registrable_domain
    from agents.marketing.company_classification import normalise_company_name

    domain = registrable_domain(lead.get("domain"))
    if not domain:
        return None
    sld = domain.split(".")[0]
    for login in dict.fromkeys([sld, normalise_company_name(lead.get("company")), f"{sld}hq", f"{sld}-hq",
                                f"{sld}inc", f"get{sld}"]):
        if not login:
            continue
        org = _gh_get(f"/orgs/{login}", http_get, token)
        if not org:
            continue
        sites = [org.get("blog") or "", org.get("email") or ""]
        if any(registrable_domain(s.split("@")[-1]) == domain for s in sites if s):
            return org.get("login") or login
    return None


def from_github(lead: dict, http_get=None, token: Optional[str] = None) -> list[dict]:
    from agents.marketing.company_classification import text_mentions_company

    http_get = http_get or httpx.get
    token = token if token is not None else os.getenv("GITHUB_TOKEN")
    org = github_org_for(lead, http_get, token)
    if not org:
        return []
    members = _gh_get(f"/orgs/{org}/public_members?per_page=100", http_get, token) or []
    out = []
    for m in members[:GITHUB_MAX_MEMBERS]:
        u = _gh_get(f"/users/{m.get('login')}", http_get, token)
        if not u or not u.get("name"):
            continue
        bio = " ".join(x for x in (u.get("bio"), u.get("company")) if x)
        t = _BIO_TITLE.search(bio or "")
        if not t:
            continue
        # The bio must tie the title to THIS company (or the profile's
        # company field must be it); "ex-CTO at OtherCo" is not our buyer.
        if not (text_mentions_company(u.get("company"), lead.get("company"))
                or text_mentions_company(u.get("bio"), lead.get("company"))
                or f"@{org}".lower() in (bio or "").lower()):
            continue
        out.append(_candidate(u["name"], t.group(1), "github", "medium",
                              f"GitHub @{u.get('login')} (member of {org}): {bio}", u.get("html_url")))
    return out


def from_search_snippets(lead: dict, scraper, db) -> list[dict]:
    """Behind ENABLE_SEARCH_SNIPPET_RESOLVER. One Brave query; result TITLES
    only. Every query is recorded under the buyer_snippet allocation."""
    from agents.marketing.brave_budget import check_source_budget, record_source_usage
    from agents.marketing.company_classification import text_mentions_company
    from agents.marketing.contact_discovery import _contact_from_text

    if not snippet_resolver_enabled() or scraper is None:
        return []
    if not check_source_budget(db, "buyer_snippet", 1).allowed:
        return []
    company = lead.get("company") or ""
    try:
        items = scraper._search(f'"CTO" "{company}"')
    finally:
        record_source_usage(db, "buyer_snippet", 1)
    out = []
    for it in items:
        title = it.get("title") or ""
        if not text_mentions_company(title, company):
            continue
        c = _contact_from_text(title, "search_snippet")
        if c and c.is_usable:
            out.append(_candidate(c.name, c.title, "search_snippet", "low", title, it.get("link")))
    return out[:3]


# ── email ─────────────────────────────────────────────────────────────────

def grade_contact_email(first: str, last: str, domain: str, *, known_email: Optional[str] = None,
                        db=None, finder=None, verifier=None) -> dict:
    """{email, email_status, email_grade} -- the same grading a human-saved
    contact gets (buyer_research._grade_email_for_lead uses this too):
    pattern + SMTP RCPT, then a catch-all probe on any 'verified' hit."""
    from agents.marketing.lead_qualification import grade_email, is_role_address
    from core.email_finder import find_email, verify_email

    finder = finder or find_email
    verifier = verifier or verify_email
    if known_email:
        v = verifier(known_email, domain=domain)
        status, email = v.status, known_email
    else:
        result = finder(first, last, domain, supabase_client=db, max_candidates=2)
        status, email = result.verification_status, result.email
    catch_all = None
    if status == "verified" and email:
        probe = verifier(f"no-such-mailbox-{abs(hash(domain)) % 10**8}@{domain}", domain=domain)
        catch_all = probe.status in ("verified", "catch_all")
    grade = grade_email(status, domain_is_catch_all=catch_all, role_address=is_role_address(email))
    return {"email": email, "email_status": status, "email_grade": grade}


# ── per-lead resolution ──────────────────────────────────────────────────

def _split(name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    from agents.marketing.company_first_sourcing import split_contact_name
    return split_contact_name(name)


def rank_candidates(cands: list[dict]) -> list[dict]:
    """Deduped by name, then: buyer-gate ALLOW first, then confidence, then
    the brief's source order. A candidate with no name sorts last -- there is
    no one to address."""
    seen: dict[str, dict] = {}
    for c in cands:
        key = re.sub(r"\W+", "", (c.get("name") or "").lower()) or f"_{len(seen)}"
        if key not in seen or (c.get("title_verdict") == "allow" and seen[key].get("title_verdict") != "allow"):
            seen[key] = c
    return sorted(seen.values(), key=lambda c: (
        not c.get("name"), c.get("title_verdict") != "allow", CONFIDENCE_RANK.get(c.get("confidence"), 3),
        SOURCE_ORDER.index(c["source"]) if c.get("source") in SOURCE_ORDER else 9))


@dataclass
class ResolverStats:
    leads: int = 0
    auto_resolved: int = 0
    prefilled: int = 0
    manual: int = 0
    candidates_by_source: dict[str, int] = field(default_factory=dict)
    email_attempts: int = 0
    email_grades: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, int] = field(default_factory=dict)
    examples: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def resolve_lead(lead: dict, *, db, http_get=None, github_get=None, scraper=None, grader=None,
                 stats: Optional[ResolverStats] = None) -> dict:
    """Gather, rank, try email, decide. Returns the mse_leads update."""
    from agents.marketing.buyer_gate import ALLOW, registrable_domain

    stats = stats or ResolverStats()
    grader = grader or grade_contact_email
    cands: list[dict] = [c for c in (lead.get("buyer_candidates") or []) if c.get("source") == "form_d"]
    tried = {"form_d": len(cands)}
    for source, fn in (("company_page", lambda: from_company_pages(lead, http_get)),
                       ("github", lambda: from_github(lead, github_get or http_get)),
                       ("hn_poster", lambda: [c for c in (lead.get("buyer_candidates") or [])
                                              if c.get("source") == "hn_poster"]),
                       ("search_snippet", lambda: from_search_snippets(lead, scraper, db))):
        try:
            found = fn()
        except Exception as exc:
            stats.source_errors[source] = stats.source_errors.get(source, 0) + 1
            log.warning("[BuyerResolver] %s failed for %s: %s", source, lead.get("company"), exc)
            found = []
        tried[source] = len(found)
        cands += found
    for c in cands:
        stats.candidates_by_source[c["source"]] = stats.candidates_by_source.get(c["source"], 0) + 1

    ranked = rank_candidates(cands)
    domain = lead.get("domain")
    chosen, attempts = None, 0
    for c in ranked:
        if attempts >= MAX_EMAIL_ATTEMPTS_PER_LEAD or c.get("title_verdict") != ALLOW or not domain:
            continue
        first, last = _split(c.get("name"))
        known = c.get("email") if c.get("email") and registrable_domain(c["email"]) == registrable_domain(domain) \
            else None
        if not known and not (first and last):
            continue
        attempts += 1
        stats.email_attempts += 1
        try:
            g = grader(first or "", last or "", domain, known_email=known, db=db)
        except Exception as exc:
            stats.source_errors["email"] = stats.source_errors.get("email", 0) + 1
            log.warning("[BuyerResolver] email grading failed for %s: %s", lead.get("company"), exc)
            continue
        c.update(email=g["email"], email_grade=g["email_grade"], email_status=g["email_status"])
        stats.email_grades[g["email_grade"]] = stats.email_grades.get(g["email_grade"], 0) + 1
        if g["email_grade"] == "valid":
            chosen = c
            break

    now = datetime.now(timezone.utc).isoformat()
    resolution = {"checked_at": now, "sources_tried": tried, "candidates": len(ranked)}
    update: dict[str, Any] = {"buyer_candidates": ranked or None, "buyer_resolved_at": now}
    if chosen:
        first, last = _split(chosen["name"])
        update.update({
            "first_name": first, "last_name": last, "title": chosen["title"],
            "email": chosen["email"], "email_status": chosen.get("email_status"),
            "email_grade": "valid", "contact_status": "found", "status": "pending_dm",
            "lead_route": "outbound_email", "buyer_suggestion": chosen,
        })
        resolution["outcome"] = OUTCOME_RESOLVED
        stats.auto_resolved += 1
    elif ranked and ranked[0].get("name"):
        update["buyer_suggestion"] = ranked[0]
        resolution["outcome"] = OUTCOME_PREFILLED
        stats.prefilled += 1
    else:
        resolution["outcome"] = OUTCOME_MANUAL
        stats.manual += 1
    update["buyer_resolution"] = resolution
    if len(stats.examples) < 25 and resolution["outcome"] != OUTCOME_MANUAL:
        pick = update.get("buyer_suggestion") or {}
        stats.examples.append({"company": lead.get("company"), "product_id": lead.get("product_id"),
                               "outcome": resolution["outcome"], "name": pick.get("name"),
                               "title": pick.get("title"), "source": pick.get("source"),
                               "confidence": pick.get("confidence"), "email_grade": pick.get("email_grade"),
                               "evidence_url": pick.get("evidence_url")})
    return update


def find_the_buyer_backlog(db, *, product_ids, force: bool = False, now: Optional[datetime] = None) -> list[dict]:
    rows = (db.table("mse_leads").select("*").in_("product_id", list(product_ids))
            .eq("contact_status", "pending").eq("status", "company_qualified").execute().data or [])
    if force:
        return rows
    cutoff = (now or datetime.now(timezone.utc)) - RECHECK_AFTER
    out = []
    for r in rows:
        at = r.get("buyer_resolved_at")
        if not at or datetime.fromisoformat(str(at).replace("Z", "+00:00")) < cutoff:
            out.append(r)
    return out


def run_buyer_resolver(db, *, product_ids=None, force: bool = False, limit: int = 60, http_get=None,
                       github_get=None, scraper=None, grader=None) -> dict:
    from agents.marketing.mkt_lead_finder import CLOUD_DECODED_PRODUCT_ID, THDAGENTIC_CONSULTING_PRODUCT_ID

    pids = product_ids or [CLOUD_DECODED_PRODUCT_ID, THDAGENTIC_CONSULTING_PRODUCT_ID]
    if scraper is None and snippet_resolver_enabled():
        from agents.marketing.mkt_lead_finder import _this_months_brave_query_count
        from scrapers.brave_search import BraveSearchScraper
        scraper = BraveSearchScraper(query_count=_this_months_brave_query_count(db))
    stats = ResolverStats()
    for lead in find_the_buyer_backlog(db, product_ids=pids, force=force)[:limit]:
        stats.leads += 1
        update = resolve_lead(lead, db=db, http_get=http_get, github_get=github_get, scraper=scraper,
                              grader=grader, stats=stats)
        db.table("mse_leads").update(update).eq("id", lead["id"]).execute()
        try:
            db.table("audit_log").insert({
                "agent_id": "mkt-buyer-resolver", "action": "resolve_buyer",
                "outcome": "win" if update["buyer_resolution"]["outcome"] != OUTCOME_MANUAL else "lose",
                "product_id": lead.get("product_id"),
                "metadata": {"lead_id": lead["id"], "company": lead.get("company"), **update["buyer_resolution"]},
            }).execute()
        except Exception as exc:
            log.error("[BuyerResolver] audit write failed: %s", exc)
    log.warning("[BuyerResolver] %s", {k: v for k, v in stats.as_dict().items() if k != "examples"})
    return {"status": "complete", "leads_written": 0, "funnel_stats": stats.as_dict()}
