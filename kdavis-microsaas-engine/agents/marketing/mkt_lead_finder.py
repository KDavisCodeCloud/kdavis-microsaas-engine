"""
MKT Lead Finder — self-hosted lead generation, replacing MKT-O1 (Apollo)
as the lead source for every MSE product (2026-08-14). Apollo's Free plan
has no API access; this is a low/zero-ongoing-cost replacement built
entirely on public data: Brave Search API results
(scrapers/brave_search.py, official API, never scraped HTML — see that
module's own docstring for why), public professional license/registry
databases per vertical (scrapers/verticals/), and email pattern-finding +
real SMTP verification (core/email_finder.py). No paid third-party lead
API, no LinkedIn scraping.

Source 1 was Google Custom Search until 2026-09-18 — switched to Brave
because Google discontinued "Search the entire web" for newly-created
Programmable Search Engines (2026-01-20), which broke this module's
open-web queries (arbitrary company/brokerage/directory sites, not a
fixed domain list). See
knowledge/sops/devops/2026-09-18-brave-search-replaces-google-cse.md
(kdavis-agentic-platform) for the full incident record.
scrapers/google_search.py is kept, dormant, not deleted.

Orchestrates all applicable sources for a product's ICP config
(mse_icp_configs), deduplicates against existing mse_leads rows (by
linkedin_url and email — same app-level + real-constraint belt-and-
suspenders pattern as mkt_li_intake.py), verifies every found email via
core/email_finder.py, and writes results to mse_leads. Only Sources 1 and
2 (brave_search, real_estate_db) land here — LinkedIn-collected leads
(manual CSV, engager lists) still go through mse_linkedin_leads via
mkt_li_intake.py, unchanged. Only the lead source changes; the rest of the
pipeline (MKT-O2, MKT-O5, HITL, CAN-SPAM) stays exactly as-is.

Real, stated tradeoff: SMTP verification is throttled to 10-20/hour per
core/email_finder.py's own rate limiting, to avoid getting an IP flagged
by mail servers — a full run toward icp_config's target_count (e.g. 100
leads) can take multiple hours if many candidates need more than one
pattern tried. This is why n8n/lead_finder_workflow.json runs weekly, not
on a live user-facing request path.

Brave Search quota tracking: BraveSearchScraper enforces a hard
FREE_TIER_MONTHLY_CAP (900, under Brave's ~1,000-query/~$5 monthly free
credit — Brave retired its free tier in Feb 2026, this is real money
past the cap, not just a courtesy limit like Google's was), but that cap
is a per-API-key MONTHLY total shared across every product a weekly run
touches — an in-process counter reset to 0 on every function call would
let multiple products in the same n8n run collectively exceed it.
run_lead_finder_for_product (the real production entry point) reads this
month's already-used count from usage_events before scraping and writes
back the new total after, so the cap is actually enforced across an
entire month's calls, not just within one. find_leads (the lower-level
"run once against this ICP" utility, useful standalone/in tests) does
not do this cross-call bookkeeping — callers hitting it directly and
repeatedly in the same month are responsible for their own quota
awareness.
"""

import logging
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from core.email_finder import MAX_VERIFY_DELAY_SECONDS, MIN_VERIFY_DELAY_SECONDS, find_email, verify_email
from core.supabase_client import get_supabase
from scrapers.base import RawLead
from scrapers.brave_search import MAX_DELAY_SECONDS, MAX_QUERIES_PER_CALL, MIN_DELAY_SECONDS, BraveSearchScraper
from scrapers.verticals import get_vertical_scraper

log = logging.getLogger(__name__)

AGENT_ID = "mkt-lead-finder"

# Rough per-step time estimates for the ETA shown on the dashboard's
# progress bar -- not exact (the search phase's real cost depends on how
# many queries a location actually needs, capped by MAX_QUERIES_PER_CALL),
# but grounded in this module's own real, already-enforced delay
# constants rather than a guess.
_AVG_VERIFY_SECONDS = (MIN_VERIFY_DELAY_SECONDS + MAX_VERIFY_DELAY_SECONDS) / 2
_AVG_SEARCH_SECONDS_PER_LOCATION = MAX_QUERIES_PER_CALL * ((MIN_DELAY_SECONDS + MAX_DELAY_SECONDS) / 2)

_CONFIDENCE_BY_STATUS = {"verified": 0.95, "catch_all": 0.4, "unverified": 0.2, "invalid": 0.0}


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "lead_finder_run",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _log_found_activities(db, product_id: str, inserted_rows: list[dict]) -> None:
    """DIST Phase 8 (mse_activities, migration 20260831000034): one
    append-only 'found' entry per lead, so decoded-empire-os's leads page
    has a real first-touch timestamp to show instead of just created_at.
    Best-effort -- a logging failure here must never fail a real lead-
    finder run that already succeeded at the thing that actually matters
    (finding and saving the lead itself)."""
    if not inserted_rows:
        return
    rows = [
        {
            "product_id": product_id,
            "subject_type": "lead",
            "subject_id": row["id"],
            "kind": "found",
            "body": f"found via {row.get('source') or 'unknown source'}",
            "actor": AGENT_ID,
        }
        for row in inserted_rows
    ]
    try:
        db.table("mse_activities").insert(rows).execute()
    except Exception as exc:
        log.warning("mse_activities logging failed for %d found leads: %s", len(rows), exc)


def _get_icp_config(db, product_id: str) -> Optional[dict]:
    result = (
        db.table("mse_icp_configs")
        .select("*")
        .eq("product_id", product_id)
        .maybe_single()
        .execute()
    )
    return result.data if result is not None else None


def _existing_identifiers(db) -> tuple[set, set]:
    """Every linkedin_url and email already in mse_leads, for app-level
    dedup — mirrors mkt_li_intake.py's existing pattern exactly."""
    rows = db.table("mse_leads").select("linkedin_url, email").execute().data or []
    urls = {r["linkedin_url"] for r in rows if r.get("linkedin_url")}
    emails = {r["email"] for r in rows if r.get("email")}
    return urls, emails


def _dedupe_raw_leads(raw_leads: list[RawLead], existing_urls: set, existing_emails: set) -> tuple[list[RawLead], int]:
    """Drops any RawLead whose linkedin_url or email already exists in
    mse_leads, or that repeats within this same batch. Returns
    (deduplicated_leads, duplicate_count)."""
    seen_urls: set = set()
    seen_emails: set = set()
    unique: list[RawLead] = []
    duplicates = 0

    for lead in raw_leads:
        url_dupe = bool(lead.linkedin_url) and (lead.linkedin_url in existing_urls or lead.linkedin_url in seen_urls)
        email_dupe = bool(lead.email) and (lead.email in existing_emails or lead.email in seen_emails)
        if url_dupe or email_dupe:
            duplicates += 1
            continue
        if lead.linkedin_url:
            seen_urls.add(lead.linkedin_url)
        if lead.email:
            seen_emails.add(lead.email)
        unique.append(lead)

    return unique, duplicates


def _this_months_brave_query_count(db) -> int:
    """Monthly, not daily -- Brave's free credit (see module docstring)
    resets on a monthly billing cycle, unlike Google CSE's old daily quota."""
    this_month = date.today().isoformat()[:7]  # "YYYY-MM"
    rows = (
        db.table("usage_events")
        .select("metadata")
        .eq("event_type", "brave_search_queries_used")
        .execute()
        .data
        or []
    )
    return sum(
        row["metadata"].get("count", 0)
        for row in rows
        if isinstance(row.get("metadata"), dict) and row["metadata"].get("month") == this_month
    )


def _verify_lead_email(lead: dict, db) -> dict:
    """Mutates and returns `lead` with email/email_status/confidence_score
    resolved: verifies a scraped-in-hand email directly, or pattern-finds
    one from name+domain when none was scraped. Never sends anything —
    this only ever probes/reads via SMTP RCPT TO, matching
    core/email_finder.py's own contract."""
    email = lead.get("email")
    domain = lead.get("domain") or (email.split("@", 1)[1] if email and "@" in email else None)
    lead["domain"] = domain

    if email:
        verification = verify_email(email, domain=domain)
        lead["email_status"] = verification.status
        lead["confidence_score"] = max(lead.get("confidence_score") or 0.0, _CONFIDENCE_BY_STATUS[verification.status])
    elif domain and lead.get("name"):
        parts = lead["name"].split()
        first_name, last_name = parts[0], (parts[-1] if len(parts) > 1 else "")
        result = find_email(first_name, last_name, domain, supabase_client=db)
        lead["email"] = result.email
        lead["email_status"] = result.verification_status
        lead["confidence_score"] = max(lead.get("confidence_score") or 0.0, result.confidence_score)
    else:
        lead["email_status"] = "unverified"

    return lead


def find_leads(
    product_id: str,
    icp_config: dict,
    limit: int = 100,
    supabase_client: Optional[Any] = None,
    brave_query_count: int = 0,
    _stats: Optional[dict] = None,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> list[dict]:
    """
    Runs Source 1 (Brave Search) and, if icp_config["vertical"]
    has one registered, Source 2 (a public license/registry database
    scraper) across every location in the ICP config, deduplicates the
    raw results against each other and against every existing mse_leads
    row, then runs Source 3 (email pattern-finding + SMTP verification)
    on each deduplicated lead. Does NOT write to mse_leads or
    mse_lead_finder_runs itself — that's run_lead_finder_for_product's
    job, so this stays a pure "given this ICP, find leads" function
    callable on its own (including from tests) without a run-tracking row.

    `_stats`, if given, is written into with {"raw_found", "duplicates",
    "brave_query_count"} — an internal escape hatch so
    run_lead_finder_for_product can capture bookkeeping (dedup count,
    Brave quota usage) without widening this function's public return
    type past `list[dict]`.

    `on_progress`, if given, is called as (current_step_text,
    completed_steps, total_steps) at two granularities: once per location
    scraped (search phase — the finest granularity that doesn't require
    threading a callback through every concrete BaseScraper subclass's
    fixed `scrape(location, filters)` interface, which all 5 scrapers in
    this package implement identically), and once per deduplicated lead
    verified (verify phase — this is where a real run actually spends
    most of its wall-clock time, per core/email_finder.py's 3-6 minute
    per-lead SMTP throttle, so per-lead granularity matters most here).
    `total_steps` is revised upward once verification starts, since the
    real count of leads to verify isn't knowable until search + dedup
    finish.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    locations = icp_config.get("locations") or []
    filters = {
        "search_templates": icp_config.get("search_templates") or [],
        "job_titles": icp_config.get("job_titles") or [],
        "exclude_domains": icp_config.get("exclude_domains") or [],
        "license_types": icp_config.get("license_types"),
    }
    titles = filters["job_titles"] or [""]

    brave_scraper = BraveSearchScraper(query_count=brave_query_count)
    vertical_scraper_cls = get_vertical_scraper(icp_config.get("vertical", ""))
    vertical_scraper = vertical_scraper_cls() if vertical_scraper_cls else None

    search_steps = max(len(locations), 1)

    raw_leads: list[RawLead] = []
    for i, location in enumerate(locations):
        if on_progress:
            on_progress(
                f"Searching {location} for {len(titles)} job title(s) across "
                f"{len(filters['search_templates'])} query pattern(s)",
                i, search_steps,
            )
        raw_leads.extend(brave_scraper.scrape(location, filters))
        if vertical_scraper:
            raw_leads.extend(vertical_scraper.scrape(location, filters))
        if len(raw_leads) >= limit:
            break

    existing_urls, existing_emails = _existing_identifiers(db)
    deduped, duplicate_count = _dedupe_raw_leads(raw_leads, existing_urls, existing_emails)
    deduped = deduped[:limit]

    verify_total = search_steps + len(deduped)
    leads: list[dict] = []
    for i, raw in enumerate(deduped):
        if on_progress:
            label = raw.company or raw.domain or raw.name or raw.linkedin_url or "candidate"
            on_progress(
                f"Verifying email {i + 1}/{len(deduped)} — {label}",
                search_steps + i, verify_total,
            )
        leads.append(_verify_lead_email(asdict(raw), db))

    if on_progress:
        on_progress("Finalizing results", verify_total, verify_total)

    # Real bug found live 2026-09-21 (first real Brave Search run ever to
    # find actual leads -- Google CSE had been silently returning zero for
    # months, so this path was never exercised): _dedupe_raw_leads above
    # only catches a duplicate email/linkedin_url the SCRAPER already had
    # in hand. _verify_lead_email can attach a *new* email via
    # find_email()'s pattern-guessing (a raw lead that scraped with no
    # email at all, only a name+domain) -- that email was never checked
    # against existing_emails or against other leads in this same batch,
    # so two candidates resolving to the same guessed address (or one
    # resolving to an address already in mse_leads) hit
    # idx_mse_leads_email's real UNIQUE constraint on insert and failed
    # run_lead_finder_for_product's single bulk insert *atomically* --
    # losing every lead in that run, not just the colliding one. Same
    # "app-level + real-constraint belt-and-suspenders" dedup discipline
    # as _dedupe_raw_leads above, applied to the email verification only
    # attaches after that first pass already ran.
    seen_emails_this_batch: set[str] = set()
    post_verify_duplicates = 0
    final_leads: list[dict] = []
    for lead in leads:
        email = lead.get("email")
        if email and (email in existing_emails or email in seen_emails_this_batch):
            post_verify_duplicates += 1
            continue
        if email:
            seen_emails_this_batch.add(email)
        final_leads.append(lead)
    leads = final_leads

    if _stats is not None:
        _stats["raw_found"] = len(raw_leads)
        _stats["duplicates"] = duplicate_count + post_verify_duplicates
        _stats["brave_query_count"] = brave_scraper.query_count

    return leads


def run_lead_finder_for_product(product_id: str, supabase_client: Optional[Any] = None, run_id: Optional[str] = None) -> dict:
    """
    Production entry point (n8n/lead_finder_workflow.json and
    POST /marketing/leads/find both call this). Pulls this product's
    ICP config, runs find_leads with real cross-call Brave-quota
    bookkeeping (see module docstring), writes results to mse_leads,
    records a mse_lead_finder_runs row start-to-finish, and returns a
    summary. Raises if no ICP config exists for this product — nothing
    to search for without one (call POST /marketing/icp first).

    `run_id`, if given, skips creating a new mse_lead_finder_runs row and
    updates that existing one instead — api/routers/leads.py's
    POST /marketing/leads/find creates the row itself and returns its id
    immediately (this whole function can take hours — see the module
    docstring's SMTP-throttling tradeoff — so it runs as a FastAPI
    BackgroundTask, and the caller needs a real id to poll before that
    background work has even started, let alone finished).
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    icp_config = _get_icp_config(db, product_id)
    if not icp_config:
        if run_id:
            db.table("mse_lead_finder_runs").update({
                "status": "failed", "error_message": "no ICP config for this product",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute()
        raise RuntimeError(f"MKT-LEAD-FINDER found no ICP config for product {product_id} — POST /marketing/icp first")

    if run_id:
        db.table("mse_lead_finder_runs").update({"status": "running"}).eq("id", run_id).execute()
    else:
        run_row = db.table("mse_lead_finder_runs").insert({
            "product_id": product_id, "status": "running", "sources_used": [],
        }).execute()
        if not run_row.data:
            raise RuntimeError(f"MKT-LEAD-FINDER failed to create a run row for product {product_id}")
        run_id = run_row.data[0]["id"]

    _emit_event(db, "lead_finder_run_started", {"product_id": product_id, "run_id": run_id})

    locations_count = max(len(icp_config.get("locations") or []), 1)

    def _on_progress(step_text: str, completed: int, total: int) -> None:
        # Best-effort -- a progress-write failure must never abort the
        # actual lead-finding work. In the search phase (completed <
        # locations_count) the ETA is a rough upper bound from this
        # module's own enforced delay constants; once verification starts
        # it's a real estimate from a known remaining count and a known
        # per-lead delay range.
        remaining_search_locations = max(locations_count - completed, 0)
        remaining_verify_leads = max(total - locations_count, 0) - max(completed - locations_count, 0)
        eta_seconds = None
        if completed < locations_count:
            eta_seconds = round(
                remaining_search_locations * _AVG_SEARCH_SECONDS_PER_LOCATION
                + max(total - locations_count, 0) * _AVG_VERIFY_SECONDS
            )
        elif total > locations_count:
            eta_seconds = round(max(remaining_verify_leads, 0) * _AVG_VERIFY_SECONDS)
        try:
            db.table("mse_lead_finder_runs").update({
                "current_step": step_text,
                "completed_steps": completed,
                "total_steps": total,
                "estimated_seconds_remaining": eta_seconds,
            }).eq("id", run_id).execute()
        except Exception:
            log.warning("MKT-LEAD-FINDER progress write failed for run %s -- continuing", run_id, exc_info=True)

    _on_progress("Starting search…", 0, locations_count)

    try:
        limit = icp_config.get("target_count") or 100
        already_used_this_month = _this_months_brave_query_count(db)

        stats: dict = {}
        leads = find_leads(
            product_id, icp_config, limit=limit, supabase_client=db,
            brave_query_count=already_used_this_month, _stats=stats,
            on_progress=_on_progress,
        )

        queries_this_run = max(0, stats.get("brave_query_count", already_used_this_month) - already_used_this_month)
        if queries_this_run:
            _emit_event(db, "brave_search_queries_used", {"month": date.today().isoformat()[:7], "count": queries_this_run})

        rows = []
        for lead in leads:
            name = lead.get("name") or ""
            name_parts = name.split()
            rows.append({
                "product_id": product_id,
                "first_name": name_parts[0] if name_parts else None,
                "last_name": name_parts[-1] if len(name_parts) > 1 else None,
                "title": lead.get("title"),
                "company": lead.get("company"),
                "domain": lead.get("domain"),
                "email": lead.get("email"),
                "email_status": lead.get("email_status", "unverified"),
                "linkedin_url": lead.get("linkedin_url"),
                "source": lead.get("source"),
                "location": lead.get("location"),
                "confidence_score": lead.get("confidence_score"),
            })

        if rows:
            insert_result = db.table("mse_leads").insert(rows).execute()
            if not insert_result.data:
                raise RuntimeError("Insert into mse_leads returned no data")
            _log_found_activities(db, product_id, insert_result.data)

        verified_count = sum(1 for r in rows if r.get("email_status") == "verified")
        sources_used = sorted({r["source"] for r in rows if r.get("source")})

        db.table("mse_lead_finder_runs").update({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "leads_found": len(leads),
            "leads_verified": verified_count,
            "leads_deduplicated": stats.get("duplicates", 0),
            "sources_used": sources_used,
            "status": "complete",
            "current_step": None,
            "estimated_seconds_remaining": 0,
        }).eq("id", run_id).execute()

    except Exception as exc:
        db.table("mse_lead_finder_runs").update({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error_message": str(exc),
            "current_step": None,
            "estimated_seconds_remaining": None,
        }).eq("id", run_id).execute()
        _write_audit(db, "lose", product_id, {"run_id": run_id, "error": str(exc)})
        _emit_event(db, "lead_finder_run_failed", {"product_id": product_id, "run_id": run_id, "error": str(exc)})
        raise RuntimeError(f"MKT-LEAD-FINDER run failed for product {product_id}: {exc}") from exc

    _write_audit(db, "win", product_id, {"run_id": run_id, "leads_found": len(leads), "leads_verified": verified_count})
    _emit_event(db, "lead_finder_run_completed", {"product_id": product_id, "run_id": run_id, "leads_found": len(leads)})

    return {
        "run_id": run_id, "status": "complete", "leads_found": len(leads),
        "leads_verified": verified_count, "leads_deduplicated": stats.get("duplicates", 0),
        "sources_used": sources_used,
    }


def run(research_report: dict, campaign_build: dict) -> dict:
    """
    Adapter for MKT-ORCH's dynamic dispatch (agents/marketing/
    mkt_orch_campaign_orchestrator.py's _fire_agent() calls
    getattr(mod, "run")(research_report=..., campaign_build=...)) — this
    is the module MKT-ORCH now routes to by default in place of MKT-O1
    (see that file's _DOWNSTREAM_AGENTS and its "use_legacy_apollo"
    override). research_report isn't used here (unlike MKT-O1, which
    derived Apollo search params from it) — mkt_lead_finder reads its
    search config from mse_icp_configs instead, set once per product via
    POST /marketing/icp, not re-derived from research on every campaign
    build.
    """
    return run_lead_finder_for_product(product_id=campaign_build["product_id"])


# ── Job posting signal scraper, added 2026-09-16 for the infra-consulting
# ICP (mse_products.slug='thdagentic-consulting', see agents/marketing/
# thd_lead_scout.py's INFRA_CONSULTING_ICP for the human-readable ICP
# definition and supabase/migrations/20260916000045_consulting_infra_icp.sql
# for the real mse_icp_configs row this reads) ─────────────────────────────
#
# Brave Search ONLY — explicitly NOT LinkedIn Jobs or Indeed
# scraping. Kelvin's own confirmation (2026-09-16, and the 2026-09-18
# Google->Brave switch that followed): reuse the existing compliant
# pattern (scrapers/brave_search.py, official Brave API, never scraped
# HTML) rather than overriding this repo's standing "no LinkedIn/
# Indeed scraping" rule (this module's own docstring above, and
# thd_lead_scout.py's identical rule). A search_template like
# '"{title}" hiring "cloud architect" {location}' surfaces public job-board
# and company-career-page results indexed by Brave — the same lawful
# mechanism BraveSearchScraper already uses for every other MSE product,
# just aimed at job-posting-shaped queries instead of people-search queries.
#
# Honest limitation, stated plainly rather than faked: Brave's Search API
# does not reliably return a structured job-posting date or employee count
# for arbitrary third-party pages (nor did Google's). This extracts both on a
# best-effort basis (schema.org/OpenGraph metatags when the response
# includes them, or an explicit date/relative-time phrase in the result
# snippet) and never fabricates either — a candidate whose posting date
# can't be determined is dropped rather than assumed recent, since the
# spec's own "last 30 days" filter cannot be honestly applied to a result
# with no ascertainable date.

_RELATIVE_DAYS_RE = re.compile(r"(\d+)\s+day", re.IGNORECASE)
_RELATIVE_HOURS_RE = re.compile(r"\d+\s+hour", re.IGNORECASE)
_ABS_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_EMPLOYEE_RANGE_RE = re.compile(r"\b(\d{1,4})\s*[-–to]{1,3}\s*(\d{1,4})\s+employees\b", re.IGNORECASE)

JOB_POSTING_QUERY_TITLES = ["cloud architect", "platform engineer", "AI infrastructure", "MLOps engineer", "cloud migration"]


def _extract_posting_date(item: dict, today: date) -> Optional[date]:
    """Best-effort only -- see module-level note above. Checks (in order):
    an explicit ISO date in the snippet, item.get("pagemap") metatags for a
    published/updated-time field (a Google CSE-specific field -- always
    empty for Brave results, kept only because it's a free, harmless check
    and scrapers/google_search.py is still dormant-not-deleted), then a
    relative "N days/hours ago" phrase in the title+snippet. Returns
    None — never a guess — if nothing usable is found."""
    text = f"{item.get('title', '')} {item.get('snippet', '')}"

    abs_match = _ABS_DATE_RE.search(text)
    if abs_match:
        try:
            return datetime.strptime(abs_match.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass

    metatags = (item.get("pagemap", {}) or {}).get("metatags") or [{}]
    for key in ("article:published_time", "datepublished", "og:updated_time"):
        raw = metatags[0].get(key) if metatags else None
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
            except ValueError:
                continue

    if _RELATIVE_HOURS_RE.search(text):
        return today
    days_match = _RELATIVE_DAYS_RE.search(text)
    if days_match:
        return today - timedelta(days=int(days_match.group(1)))

    return None


def _extract_employee_estimate(item: dict) -> Optional[str]:
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    match = _EMPLOYEE_RANGE_RE.search(text)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _company_name_from_result(item: dict, domain: str) -> str:
    title = item.get("title", "")
    for sep in (" - ", " | ", " hiring ", " is hiring "):
        if sep in title:
            return title.split(sep)[0].strip()
    return domain


def find_job_posting_signals(
    product_id: str,
    icp_config: dict,
    max_age_days: int = 30,
    brave_query_count: int = 0,
    _stats: Optional[dict] = None,
) -> list[dict]:
    """
    Pure "given this ICP, find job-posting signals" function — does not
    write to mse_leads itself (same contract as find_leads above), so it
    stays independently testable/CLI-callable. Returns dicts shaped for
    mse_leads' job_posting_url/job_posting_title/job_posting_date columns
    (migration 20260916000045), source='job_posting_signal'.

    Filters: posting date within max_age_days (a candidate with no
    ascertainable date is dropped, not assumed-recent — see module note);
    company size under icp_config['max_company_size'] when an estimate was
    actually extracted from the result (undetectable size is NOT treated
    as a disqualifier — the spec's own "under 200 if detectable" wording).
    """
    scraper = BraveSearchScraper(query_count=brave_query_count)
    if not scraper.api_key:
        if _stats is not None:
            _stats["raw_found"] = 0
            _stats["brave_query_count"] = brave_query_count
        return []  # skip gracefully -- same shape as every other quota/credential-gated path in this codebase

    titles = icp_config.get("job_titles") or [""]
    locations = icp_config.get("locations") or [""]
    templates = icp_config.get("search_templates") or [
        f'"{{title}}" hiring "{kw}" {{location}}' for kw in JOB_POSTING_QUERY_TITLES
    ]
    max_company_size = icp_config.get("max_company_size")
    today = date.today()
    cutoff = today - timedelta(days=max_age_days)

    raw_found = 0
    seen_urls: set[str] = set()
    signals: list[dict] = []

    for template in templates:
        for title in titles:
            for location in locations:
                items = scraper._search(template.format(title=title, location=location))
                raw_found += len(items)
                for item in items:
                    link = item.get("link", "")
                    if not link or link in seen_urls:
                        continue
                    seen_urls.add(link)

                    posting_date = _extract_posting_date(item, today)
                    if posting_date is None or posting_date < cutoff:
                        continue

                    employee_estimate = _extract_employee_estimate(item)
                    if employee_estimate and max_company_size:
                        try:
                            upper = int(employee_estimate.split("-")[1])
                            if upper > max_company_size:
                                continue
                        except (ValueError, IndexError):
                            pass

                    domain = urlparse(link).netloc.replace("www.", "")
                    signals.append({
                        "product_id": product_id,
                        "company": _company_name_from_result(item, domain),
                        "domain": domain,
                        "title": title,
                        "source": "job_posting_signal",
                        "location": location,
                        "job_posting_url": link,
                        "job_posting_title": title,
                        "job_posting_date": posting_date.isoformat(),
                        "confidence_score": 0.5,
                    })

    if _stats is not None:
        _stats["raw_found"] = raw_found
        _stats["duplicates"] = raw_found - len(signals)
        _stats["brave_query_count"] = scraper.query_count

    return signals


def run_job_posting_signal_finder(product_id: str, supabase_client: Optional[Any] = None) -> dict:
    """
    Production entry point — pulls this product's mse_icp_configs row (same
    lookup as run_lead_finder_for_product), runs find_job_posting_signals
    with real cross-call Brave-quota bookkeeping, dedupes against existing
    mse_leads (by job_posting_url — a company can post more than one
    matching role, and re-surfacing the same posting isn't a new signal),
    and writes qualified rows to mse_leads with source='job_posting_signal'.
    Raises if no ICP config exists for this product, same fail-fast
    contract as run_lead_finder_for_product.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    icp_config = _get_icp_config(db, product_id)
    if not icp_config:
        raise RuntimeError(f"MKT-LEAD-FINDER (job postings) found no ICP config for product {product_id}")

    existing_urls = {
        r["job_posting_url"]
        for r in (db.table("mse_leads").select("job_posting_url").eq("source", "job_posting_signal").execute().data or [])
        if r.get("job_posting_url")
    }

    already_used_this_month = _this_months_brave_query_count(db)
    stats: dict = {}
    signals = find_job_posting_signals(product_id, icp_config, brave_query_count=already_used_this_month, _stats=stats)
    deduped = [s for s in signals if s["job_posting_url"] not in existing_urls]

    queries_this_run = max(0, stats.get("brave_query_count", already_used_this_month) - already_used_this_month)
    if queries_this_run:
        _emit_event(db, "brave_search_queries_used", {"month": date.today().isoformat()[:7], "count": queries_this_run})

    inserted = []
    if deduped:
        insert_result = db.table("mse_leads").insert(deduped).execute()
        if not insert_result.data:
            raise RuntimeError("Insert into mse_leads (job_posting_signal) returned no data")
        inserted = insert_result.data
        _log_found_activities(db, product_id, inserted)

    _write_audit(db, "win", product_id, {
        "raw_found": stats.get("raw_found", 0), "duplicates_within_run": stats.get("duplicates", 0),
        "duplicates_vs_existing": len(signals) - len(deduped), "leads_written": len(inserted),
    })
    _emit_event(db, "job_posting_signal_run_completed", {"product_id": product_id, "leads_written": len(inserted)})

    return {
        "status": "complete", "leads_found": len(signals), "leads_written": len(inserted),
        "raw_found": stats.get("raw_found", 0),
    }
