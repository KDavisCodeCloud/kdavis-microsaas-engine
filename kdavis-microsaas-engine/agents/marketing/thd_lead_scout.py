"""
THD Consulting Lead Scout — finds SMBs that show public signals of poor
security hygiene (no dedicated IT, no MSP relationship, no security
certifications) for Kelvin's own IT-security-implementation service line.

Sourcing follows agents/marketing/mkt_lead_finder.py's exact house rule,
reused rather than re-litigated: Brave Search (scrapers/brave_search.py,
official API, shares the same BRAVE_API_KEY / ~900-query monthly free-
credit cap with every other lead-finder consumer — see
_this_months_brave_query_count below) as Source 1, one vertical scraper
(scrapers/verticals/trades.py, construction) as an optional Source 2, and
core/email_finder.py (pattern-guessing + real SMTP verification) for
contact email. Switched from Google Custom Search 2026-09-18 (Kelvin's
explicit instruction: one Brave key for all lead sourcing, no
exceptions) — Google discontinued "Search the entire web" for newly-
created Programmable Search Engines on 2026-01-20, breaking the open-web
queries this module also depends on. NO LinkedIn/Indeed/Glassdoor/Yelp/
Clutch scraping and NO Apollo/Hunter integration — both explicitly ruled
out already in this codebase (mkt_lead_finder.py's own module docstring:
"No paid third-party lead API, no LinkedIn scraping") and in
kdavis-agentic-platform's api/routes/outreach.py compliance boundary.
Google Places/Apollo/Hunter API keys named in the original task spec are
deliberately not wired for the same reason Apollo was already dropped
here: no free tier, recurring cost for a feature that already has a
low/zero-cost, real-API equivalent.

Unlike mkt_lead_finder.py, this module also SCORES each candidate (1-10,
signal_breakdown) before writing it — the MSE lead finder scores lead
*email confidence*, not "does this company need our service," a dimension
that doesn't exist there and does here. Only leads clearing
THD_MIN_SIGNAL_SCORE are written to thd_consulting_leads at all — same
"never insert what doesn't clear a threshold" principle as the MSE
Verdict gate (agents/aggregator/agent.py) uses for opportunity scoring.
"""

import logging
import os
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Optional

from core.email_finder import find_email, verify_email
from core.supabase_client import get_supabase
from scrapers.base import RawLead
from scrapers.brave_search import BraveSearchScraper
from scrapers.company_signals import fetch_company_signals
from scrapers.verticals.trades import TradesScraper

log = logging.getLogger(__name__)

AGENT_ID = "thd-lead-scout"
PRODUCT_ID = "thd_consulting"

_CONFIDENCE_BY_STATUS = {"verified": 0.95, "catch_all": 0.4, "unverified": 0.2, "invalid": 0.0}

# Industries this service targets, and the vertical scraper (if any) each
# one can also draw from. Only "construction" has a real Source-2 scraper
# today (scrapers/verticals/trades.py) — every other industry gets Source 1
# (Brave Search) only, same graceful-degradation behavior
# mkt_lead_finder.py already uses for verticals with no registered scraper.
TARGET_INDUSTRIES: dict[str, Optional[str]] = {
    "food_production": None,
    "manufacturing": None,
    "logistics": None,
    "professional_services": None,
    "healthcare_adjacent": None,
    "construction": "trades",
    "real_estate": None,
}

# ── Second ICP, added 2026-09-16: cloud/AI infrastructure consulting ──────
#
# Same "THD Consulting" business, a second, higher-tier ICP alongside the
# SMB security-hygiene targeting above -- NOT a replacement, and this file's
# own scoring/scraping machinery (score_lead, find_and_score_leads,
# run_lead_scout, thd_consulting_leads) is untouched and still only ever
# serves TARGET_INDUSTRIES above. This dict is reference/definition only --
# Kelvin's own task spec named MKT-O2 (agents/marketing/mkt_o2_cold_dm_writer.py)
# and mse_leads specifically for this ICP's actual DM-writing and lead
# pipeline, which already exist and already serve every other MSE product,
# rather than extending thd_consulting_leads' separate table/scoring model
# to a fundamentally different buyer (CTOs/VPs Eng at funded startups, not
# SMB owners/office managers). The real, live config lives in
# mse_icp_configs (product_id = mse_products.slug='thdagentic-consulting',
# see supabase/migrations/20260916000045_consulting_infra_icp.sql) -- this
# constant exists so a human reading this file sees both THD Consulting
# ICPs side by side, matching Kelvin's explicit "second branch/config in
# thd_lead_scout.py" instruction.
INFRA_CONSULTING_ICP = {
    "mse_products_slug": "thdagentic-consulting",
    "channel": "linkedin_dm",  # drafted only -- no LinkedIn messaging API exists anywhere in this codebase; a human sends manually, same as every other linkedin_manual/linkedin_engager sequence
    "target_titles": ["CTO", "VP Engineering", "Engineering Director", "Head of Platform", "Founder+CTO"],
    "target_company_size": {"min": 20, "max": 200},
    "target_signals": [
        "job postings for cloud architect / AI infrastructure / platform engineer roles",
        "recent Series A or Series B funding announcement",
        "LinkedIn posts from CTOs/VPs Eng mentioning cloud migration, infrastructure scaling, or AI implementation challenges",
    ],
    "exclude": [
        "over 500 employees (procurement cycle too long)",
        "no tech presence",
        "already using Palantir/Databricks at scale (not the right fit)",
    ],
    # Job-posting signal sourcing is Brave Search ONLY (see
    # agents/marketing/mkt_lead_finder.py's find_job_posting_signals) --
    # explicitly not LinkedIn Jobs/Indeed scraping, per Kelvin's own
    # confirmation (2026-09-16) not to override this file's and
    # mkt_lead_finder.py's existing no-scraping compliance rule. Was
    # "google_custom_search" until the 2026-09-18 Brave switch.
    "job_posting_source": "brave_search",
}

# The non-IT person actually holding access at a company like this — matches
# the spec's own contact target ("Owner or operations manager... not IT
# staff"). Also doubles as evidence for scoring: a search that turns up an
# "IT Manager" or "Network Administrator" title for this company is itself
# a low-signal result (see score_lead's dedicated-IT penalty).
_CONTACT_TITLES = ["owner", "office manager", "operations manager", "general manager"]

_SEARCH_TEMPLATE = '"{title}" "{location}" small business contact -jobs -indeed.com -linkedin.com'

_DEFAULT_MIN_SIGNAL_SCORE = int(os.environ.get("THD_MIN_SIGNAL_SCORE", "6"))


def score_lead(industry: str, employee_count_estimate: Optional[str], signals) -> tuple[int, dict]:
    """Pure, deterministic point system — no LLM call, so results are
    reproducible and free to run at volume. `signals` is a
    scrapers.company_signals.CompanySignals (or any object with the same
    attributes). Returns (score clamped to [1, 10], breakdown dict)."""
    breakdown: dict[str, int] = {}

    if industry in TARGET_INDUSTRIES:
        breakdown["target_industry"] = 2
    if employee_count_estimate == "25-150":
        breakdown["employee_range_match"] = 2
    if not signals.reachable:
        breakdown["site_unreachable_no_disqualifying_signal"] = 1
    else:
        if not signals.mentions_msp:
            breakdown["no_msp_mentioned"] = 2
        else:
            breakdown["msp_mentioned"] = -3
        if signals.mentions_security_cert:
            breakdown["security_cert_mentioned"] = -3
        if signals.mentions_dedicated_it:
            breakdown["dedicated_it_mentioned"] = -2
        if signals.outdated_stack_hint:
            breakdown["outdated_stack_detected"] = 1

    raw_score = 5 + sum(breakdown.values())  # 5 = neutral baseline
    score = max(1, min(10, raw_score))
    return score, breakdown


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({"tenant_id": None, "event_type": event_type, "metadata": metadata}).execute()


def _write_audit(db, outcome: str, metadata: dict) -> None:
    # audit_log.product_id is a UUID column FK-shaped for real MSE products
    # (supabase/migrations/20260709000005_marketing_engine.sql) — thd_consulting
    # isn't one, so the label goes in metadata instead of forcing a bad cast.
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "lead_scout_run",
        "outcome": outcome,
        "product_id": None,
        "metadata": {**metadata, "product_id": PRODUCT_ID},
    }).execute()


def _this_months_brave_query_count(db) -> int:
    """Reads the SAME usage_events rows mkt_lead_finder.py writes
    (event_type='brave_search_queries_used') — the ~900-query monthly
    free-credit cap is shared across every consumer of BRAVE_API_KEY, MSE
    products and THD Consulting alike. Two independent counters would let
    the two together blow past the real cap and start billing. Monthly,
    not daily -- Brave's free credit resets on a monthly billing cycle
    (2026-09-18 switch from Google CSE's old daily quota)."""
    this_month = date.today().isoformat()[:7]  # "YYYY-MM"
    rows = db.table("usage_events").select("metadata").eq("event_type", "brave_search_queries_used").execute().data or []
    return sum(
        row["metadata"].get("count", 0)
        for row in rows
        if isinstance(row.get("metadata"), dict) and row["metadata"].get("month") == this_month
    )


def _existing_domains(db) -> set:
    rows = db.table("thd_consulting_leads").select("domain").execute().data or []
    return {r["domain"] for r in rows if r.get("domain")}


def _dedupe_by_domain(raw_leads: list[RawLead], existing_domains: set) -> tuple[list[RawLead], int]:
    seen: set = set()
    unique: list[RawLead] = []
    duplicates = 0
    for lead in raw_leads:
        if not lead.domain:
            duplicates += 1
            continue
        if lead.domain in existing_domains or lead.domain in seen:
            duplicates += 1
            continue
        seen.add(lead.domain)
        unique.append(lead)
    return unique, duplicates


def _resolve_contact_email(lead: dict, db) -> dict:
    """Mirrors mkt_lead_finder.py's _verify_lead_email exactly — verifies a
    scraped-in-hand email, or pattern-finds one from name+domain. Never
    sends anything, only ever probes/reads via SMTP RCPT TO."""
    email = lead.get("email")
    domain = lead["domain"]

    if email:
        verification = verify_email(email, domain=domain)
        lead["email_status"] = verification.status
        lead["confidence_score"] = _CONFIDENCE_BY_STATUS[verification.status]
    elif lead.get("name"):
        parts = lead["name"].split()
        first_name, last_name = parts[0], (parts[-1] if len(parts) > 1 else "")
        result = find_email(first_name, last_name, domain, supabase_client=db)
        lead["email"] = result.email
        lead["email_status"] = result.verification_status
        lead["confidence_score"] = result.confidence_score
    else:
        lead["email_status"] = "unverified"
        lead["confidence_score"] = 0.0

    return lead


def find_and_score_leads(
    filters: dict,
    supabase_client: Optional[Any] = None,
    brave_query_count: int = 0,
    _stats: Optional[dict] = None,
) -> list[dict]:
    """
    Given {"industries": [...], "locations": [...], "min_signal_score": int},
    runs Source 1 (+ Source 2 for any industry with a registered vertical
    scraper) across every industry x location combination, dedupes against
    existing thd_consulting_leads by domain, resolves a contact email and
    company-site signals for each survivor, scores it, and returns only the
    candidates that clear min_signal_score. Does NOT write to Supabase —
    mirrors mkt_lead_finder.find_leads' "pure function, caller persists"
    contract so this stays independently testable/CLI-callable.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    industries = filters.get("industries") or list(TARGET_INDUSTRIES.keys())
    locations = filters.get("locations") or []
    min_signal_score = filters.get("min_signal_score", _DEFAULT_MIN_SIGNAL_SCORE)
    employee_range = filters.get("employee_range", "25-150")

    brave_scraper = BraveSearchScraper(query_count=brave_query_count)
    trades_scraper = TradesScraper()

    search_filters = {
        "search_templates": [_SEARCH_TEMPLATE],
        "job_titles": _CONTACT_TITLES,
        "exclude_domains": set(),
    }

    raw_leads: list[RawLead] = []
    sources_used: set[str] = set()
    for industry in industries:
        vertical = TARGET_INDUSTRIES.get(industry)
        for location in locations:
            found = brave_scraper.scrape(location, search_filters)
            for lead in found:
                lead.location = f"{industry}:{location}"  # carries industry through to scoring below
            raw_leads.extend(found)
            if found:
                sources_used.add("brave_search")

            if vertical == "trades":
                trades_found = trades_scraper.scrape(location, search_filters)
                for lead in trades_found:
                    lead.location = f"{industry}:{location}"
                raw_leads.extend(trades_found)
                if trades_found:
                    sources_used.add("trades_db")

    existing_domains = _existing_domains(db)
    deduped, duplicate_count = _dedupe_by_domain(raw_leads, existing_domains)

    qualified: list[dict] = []
    for raw in deduped:
        lead = asdict(raw)
        industry, location = (lead["location"].split(":", 1) + [""])[:2] if lead.get("location") else ("", "")
        lead["industry"] = industry
        lead["location"] = location

        lead = _resolve_contact_email(lead, db)
        signals = fetch_company_signals(lead["domain"])
        score, breakdown = score_lead(industry, employee_range, signals)

        if score < min_signal_score:
            continue

        lead["signal_score"] = score
        lead["signal_breakdown"] = breakdown
        lead["employee_count_estimate"] = employee_range
        lead["company_name"] = lead.get("company") or lead["domain"]
        lead["website"] = f"https://{lead['domain']}"
        qualified.append(lead)

    if _stats is not None:
        _stats["raw_found"] = len(raw_leads)
        _stats["duplicates"] = duplicate_count
        _stats["brave_query_count"] = brave_scraper.query_count
        _stats["sources_used"] = sorted(sources_used)

    return qualified


def run_lead_scout(filters: dict, supabase_client: Optional[Any] = None, run_id: Optional[str] = None) -> dict:
    """Production entry point for POST /thd-consulting/scrape/find and
    scripts/run_thd_lead_scout.py alike. Writes qualified leads to
    thd_consulting_leads and records a thd_consulting_scrape_runs row
    start-to-finish. `run_id`, if given, updates that existing row instead
    of creating a new one — same reasoning as mkt_lead_finder.py's
    run_lead_finder_for_product (the caller already created the row
    synchronously so it has an id to return before this potentially
    long-running background task even starts)."""
    db = supabase_client if supabase_client is not None else get_supabase()

    if run_id:
        db.table("thd_consulting_scrape_runs").update({"status": "running"}).eq("id", run_id).execute()
    else:
        run_row = db.table("thd_consulting_scrape_runs").insert({
            "product_id": PRODUCT_ID, "filters": filters, "status": "running", "sources_used": [],
        }).execute()
        if not run_row.data:
            raise RuntimeError("THD-LEAD-SCOUT failed to create a scrape run row")
        run_id = run_row.data[0]["id"]

    _emit_event(db, "thd_lead_scout_run_started", {"run_id": run_id, "filters": filters})

    try:
        already_used_this_month = _this_months_brave_query_count(db)
        stats: dict = {}
        leads = find_and_score_leads(
            filters, supabase_client=db, brave_query_count=already_used_this_month, _stats=stats,
        )

        queries_this_run = max(0, stats.get("brave_query_count", already_used_this_month) - already_used_this_month)
        if queries_this_run:
            _emit_event(db, "brave_search_queries_used", {"month": date.today().isoformat()[:7], "count": queries_this_run})

        rows = [
            {
                "product_id": PRODUCT_ID,
                "company_name": lead["company_name"],
                "domain": lead["domain"],
                "industry": lead["industry"],
                "employee_count_estimate": lead["employee_count_estimate"],
                "location": lead["location"],
                "website": lead["website"],
                "contact_name": lead.get("name"),
                "contact_title": None,  # not reliably derivable without LinkedIn/job-posting scraping — left for manual fill-in
                "contact_email": lead.get("email"),
                "contact_email_status": lead.get("email_status", "unverified"),
                "contact_linkedin": lead.get("linkedin_url"),
                "contact_phone": None,  # no phone source wired (would require Google Places, deliberately not integrated — see module docstring)
                "signal_score": lead["signal_score"],
                "signal_breakdown": lead["signal_breakdown"],
                "source": lead.get("source"),
            }
            for lead in leads
        ]

        if rows:
            insert_result = db.table("thd_consulting_leads").insert(rows).execute()
            if not insert_result.data:
                raise RuntimeError("Insert into thd_consulting_leads returned no data")

        db.table("thd_consulting_scrape_runs").update({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "candidates_found": stats.get("raw_found", 0),
            "leads_qualified": len(rows),
            "leads_deduplicated": stats.get("duplicates", 0),
            "sources_used": stats.get("sources_used", []),
            "status": "complete",
        }).eq("id", run_id).execute()

    except Exception as exc:
        db.table("thd_consulting_scrape_runs").update({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error_message": str(exc)[:2000],
        }).eq("id", run_id).execute()
        _write_audit(db, "lose", {"run_id": run_id, "error": str(exc)[:500]})
        _emit_event(db, "thd_lead_scout_run_failed", {"run_id": run_id, "error": str(exc)[:500]})
        raise RuntimeError(f"THD-LEAD-SCOUT run failed: {exc}") from exc

    _write_audit(db, "win", {"run_id": run_id, "leads_qualified": len(rows)})
    _emit_event(db, "thd_lead_scout_run_completed", {"run_id": run_id, "leads_qualified": len(rows)})

    return {
        "run_id": run_id, "status": "complete", "leads_qualified": len(rows),
        "candidates_found": stats.get("raw_found", 0), "leads_deduplicated": stats.get("duplicates", 0),
        "sources_used": stats.get("sources_used", []),
    }
