"""
MKT Lead Finder — self-hosted lead generation, replacing MKT-O1 (Apollo)
as the lead source for every MSE product (2026-08-14). Apollo's Free plan
has no API access; this is a zero-ongoing-cost replacement built entirely
on public data: Google Custom Search results (scrapers/google_search.py,
official API, never scraped HTML — see that module's own docstring for
why), public professional license/registry databases per vertical
(scrapers/verticals/), and email pattern-finding + real SMTP verification
(core/email_finder.py). No paid third-party lead API, no LinkedIn
scraping.

Orchestrates all applicable sources for a product's ICP config
(mse_icp_configs), deduplicates against existing mse_leads rows (by
linkedin_url and email — same app-level + real-constraint belt-and-
suspenders pattern as mkt_li_intake.py), verifies every found email via
core/email_finder.py, and writes results to mse_leads. Only Sources 1 and
2 (google_search, real_estate_db) land here — LinkedIn-collected leads
(manual CSV, engager lists) still go through mse_linkedin_leads via
mkt_li_intake.py, unchanged. Only the lead source changes; the rest of the
pipeline (MKT-O2, MKT-O5, HITL, CAN-SPAM) stays exactly as-is.

Real, stated tradeoff: SMTP verification is throttled to 10-20/hour per
core/email_finder.py's own rate limiting, to avoid getting an IP flagged
by mail servers — a full run toward icp_config's target_count (e.g. 100
leads) can take multiple hours if many candidates need more than one
pattern tried. This is why n8n/lead_finder_workflow.json runs weekly, not
on a live user-facing request path.

Google Custom Search quota tracking: GoogleSearchScraper enforces the
always-free 100 queries/day cap, but that cap is a per-API-key daily
total shared across every product a weekly run touches — an in-process
counter reset to 0 on every function call would let multiple products in
the same n8n run collectively exceed it. run_lead_finder_for_product
(the real production entry point) reads today's already-used count from
usage_events before scraping and writes back the new total after, so the
cap is actually enforced across an entire day's calls, not just within
one. find_leads (the lower-level "run once against this ICP" utility,
useful standalone/in tests) does not do this cross-call bookkeeping —
callers hitting it directly and repeatedly in the same day are
responsible for their own quota awareness.
"""

import logging
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Optional

from core.email_finder import find_email, verify_email
from core.supabase_client import get_supabase
from scrapers.base import RawLead
from scrapers.google_search import GoogleSearchScraper
from scrapers.verticals import get_vertical_scraper

log = logging.getLogger(__name__)

AGENT_ID = "mkt-lead-finder"

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


def _todays_google_query_count(db) -> int:
    today = date.today().isoformat()
    rows = (
        db.table("usage_events")
        .select("metadata")
        .eq("event_type", "google_cse_queries_used")
        .execute()
        .data
        or []
    )
    return sum(
        row["metadata"].get("count", 0)
        for row in rows
        if isinstance(row.get("metadata"), dict) and row["metadata"].get("date") == today
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
    google_daily_query_count: int = 0,
    _stats: Optional[dict] = None,
) -> list[dict]:
    """
    Runs Source 1 (Google Custom Search) and, if icp_config["vertical"]
    has one registered, Source 2 (a public license/registry database
    scraper) across every location in the ICP config, deduplicates the
    raw results against each other and against every existing mse_leads
    row, then runs Source 3 (email pattern-finding + SMTP verification)
    on each deduplicated lead. Does NOT write to mse_leads or
    mse_lead_finder_runs itself — that's run_lead_finder_for_product's
    job, so this stays a pure "given this ICP, find leads" function
    callable on its own (including from tests) without a run-tracking row.

    `_stats`, if given, is written into with {"raw_found", "duplicates",
    "google_daily_query_count"} — an internal escape hatch so
    run_lead_finder_for_product can capture bookkeeping (dedup count,
    Google quota usage) without widening this function's public return
    type past `list[dict]`.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    locations = icp_config.get("locations") or []
    filters = {
        "search_templates": icp_config.get("search_templates") or [],
        "job_titles": icp_config.get("job_titles") or [],
        "exclude_domains": icp_config.get("exclude_domains") or [],
        "license_types": icp_config.get("license_types"),
    }

    google_scraper = GoogleSearchScraper(daily_query_count=google_daily_query_count)
    vertical_scraper_cls = get_vertical_scraper(icp_config.get("vertical", ""))
    vertical_scraper = vertical_scraper_cls() if vertical_scraper_cls else None

    raw_leads: list[RawLead] = []
    for location in locations:
        raw_leads.extend(google_scraper.scrape(location, filters))
        if vertical_scraper:
            raw_leads.extend(vertical_scraper.scrape(location, filters))
        if len(raw_leads) >= limit:
            break

    existing_urls, existing_emails = _existing_identifiers(db)
    deduped, duplicate_count = _dedupe_raw_leads(raw_leads, existing_urls, existing_emails)
    deduped = deduped[:limit]

    leads = [_verify_lead_email(asdict(raw), db) for raw in deduped]

    if _stats is not None:
        _stats["raw_found"] = len(raw_leads)
        _stats["duplicates"] = duplicate_count
        _stats["google_daily_query_count"] = google_scraper.daily_query_count

    return leads


def run_lead_finder_for_product(product_id: str, supabase_client: Optional[Any] = None, run_id: Optional[str] = None) -> dict:
    """
    Production entry point (n8n/lead_finder_workflow.json and
    POST /marketing/leads/find both call this). Pulls this product's
    ICP config, runs find_leads with real cross-call Google-quota
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

    try:
        limit = icp_config.get("target_count") or 100
        already_used_today = _todays_google_query_count(db)

        stats: dict = {}
        leads = find_leads(
            product_id, icp_config, limit=limit, supabase_client=db,
            google_daily_query_count=already_used_today, _stats=stats,
        )

        queries_this_run = max(0, stats.get("google_daily_query_count", already_used_today) - already_used_today)
        if queries_this_run:
            _emit_event(db, "google_cse_queries_used", {"date": date.today().isoformat(), "count": queries_this_run})

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
        }).eq("id", run_id).execute()

    except Exception as exc:
        db.table("mse_lead_finder_runs").update({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error_message": str(exc),
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
