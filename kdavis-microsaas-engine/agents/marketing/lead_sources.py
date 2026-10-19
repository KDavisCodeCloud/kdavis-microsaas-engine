"""
agents/marketing/lead_sources.py

Production entry points for the 2026-10-18 lead-targeting extensions. Each one
records a real mse_lead_finder_runs row with funnel_stats, writes its leads
through the same conflict-tolerant insert as scraper v2, and writes an
audit_log entry win or lose -- the same contract as run_scraper_v2_scout, so
the Loop Map's "find" and "qualify" desks read these runs exactly the way they
read the weekly ATS run.

  run_hn_scout         Hacker News hiring threads   -> consulting, source=hn_thread
  run_partner_scout    Brave + firms' own sites      -> partners, source=partner_signal
  run_vc_scout         curated funds' own pages      -> partners, source=vc_operating_partner
                                                       (+ portfolio store, funded_by tags)
  run_pain_backfill    pain phrases on leads that predate them (free: ATS JSON APIs)
  run_pain_rescan      weekly: every lead re-read against the current phrase list
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from core.supabase_client import get_supabase_for_job

log = logging.getLogger(__name__)

AGENT_ID = "mkt-lead-sources"


def _emit(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({"tenant_id": None, "event_type": event_type, "metadata": metadata}).execute()


def _audit(db, outcome: str, product_id: str, action: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID, "action": action, "outcome": outcome,
        "product_id": product_id, "metadata": metadata,
    }).execute()


def _start_run(db, product_id: str, source: str, run_id: Optional[str]) -> str:
    if run_id:
        db.table("mse_lead_finder_runs").update(
            {"status": "running", "sources_used": [source]}).eq("id", run_id).execute()
        return run_id
    row = db.table("mse_lead_finder_runs").insert(
        {"product_id": product_id, "status": "running", "sources_used": [source]}).execute()
    if not row.data:
        raise RuntimeError(f"failed to create a {source} run row for product {product_id}")
    return row.data[0]["id"]


def _finish_run(db, run_id: str, *, written: int, funnel: dict, error: Optional[str] = None) -> None:
    payload = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "funnel_stats": funnel,
        "current_step": None,
        "estimated_seconds_remaining": 0 if not error else None,
    }
    if error:
        payload.update(status="failed", error_message=error[:1000])
    else:
        payload.update(status="complete", leads_found=written, leads_verified=0)
    db.table("mse_lead_finder_runs").update(payload).eq("id", run_id).execute()


def _write_rows(db, rows: list[dict], source: str) -> list[dict]:
    """Through scraper v2's column allowlist and conflict-tolerant insert, so
    one duplicate cannot discard a batch and an unknown key cannot 400 it."""
    from agents.marketing.company_first_sourcing import MSE_LEADS_WRITABLE_COLUMNS
    from agents.marketing.mkt_lead_finder import _insert_leads_tolerating_conflicts, _log_found_activities

    clean = []
    for r in rows:
        unknown = set(r) - MSE_LEADS_WRITABLE_COLUMNS
        if unknown:
            log.warning("[LeadSources] dropping unknown mse_leads key(s) %s", sorted(unknown))
        clean.append({k: v for k, v in r.items()
                      if k in MSE_LEADS_WRITABLE_COLUMNS and v is not None and v != []})
    inserted = _insert_leads_tolerating_conflicts(db, clean, source)
    if inserted:
        _log_found_activities(db, clean[0]["product_id"], inserted)
    return inserted


def _run(db, product_id: str, source: str, run_id: Optional[str],
         work: Callable[[], tuple[list[dict], dict]]) -> dict:
    """Shared run lifecycle: row, work, write, finish, audit -- win or lose."""
    run_id = _start_run(db, product_id, source, run_id)
    _emit(db, f"{source}_run_started", {"product_id": product_id, "run_id": run_id})
    try:
        rows, funnel = work()
        inserted = _write_rows(db, rows, source)
        funnel["leads_qualified"] = len(rows)
        funnel["leads_written"] = len(inserted)
        _finish_run(db, run_id, written=len(inserted), funnel=funnel)
    except Exception as exc:
        _finish_run(db, run_id, written=0, funnel={"error": str(exc)[:500]}, error=str(exc))
        _audit(db, "lose", product_id, f"{source}_run", {"run_id": run_id, "error": str(exc)})
        _emit(db, f"{source}_run_failed", {"product_id": product_id, "run_id": run_id, "error": str(exc)})
        raise RuntimeError(f"{source} run failed for product {product_id}: {exc}") from exc
    _audit(db, "win", product_id, f"{source}_run",
           {"run_id": run_id, "leads_written": len(inserted), "funnel_stats": funnel})
    _emit(db, f"{source}_run_completed",
          {"product_id": product_id, "run_id": run_id, "leads_written": len(inserted)})
    return {"run_id": run_id, "status": "complete", "product_id": product_id,
            "leads_written": len(inserted), "funnel_stats": funnel}


def _record_brave(db, n: int) -> None:
    if n:
        _emit(db, "brave_search_queries_used", {"month": date.today().isoformat()[:7], "count": n})


# ── Hacker News ──────────────────────────────────────────────────────────

def run_hn_scout(supabase_client: Optional[Any] = None, *, months: int = 1,
                 run_id: Optional[str] = None, http_get=None) -> dict:
    from agents.marketing.hn_scout import find_hn_leads
    from agents.marketing.mkt_lead_finder import (
        THDAGENTIC_CONSULTING_PRODUCT_ID,
        _existing_job_signal_companies,
        _existing_job_signal_domains,
        _get_icp_config,
    )
    from agents.marketing.pain_phrases import PainConfig, boost_intent, match_pain_phrases

    db = supabase_client if supabase_client is not None else get_supabase_for_job()
    product_id = THDAGENTIC_CONSULTING_PRODUCT_ID

    def work():
        rows, stats = find_hn_leads(
            product_id, http_get=http_get, months=months,
            existing_companies=_existing_job_signal_companies(db),
            existing_domains=_existing_job_signal_domains(db),
        )
        pain = PainConfig.from_icp(_get_icp_config(db, product_id))
        with_pain = 0
        for r in rows:
            m = match_pain_phrases(r.get("signal_text"), pain.phrases)
            if m:
                with_pain += 1
                r["pain_phrases"] = m.phrases
                r["pain_evidence"] = m.evidence
                r["intent_score"], _ = boost_intent(r.get("intent_score") or 0.0, m, pain)
                r["score_reasons"] = list(r.get("score_reasons") or []) + [
                    f"pain phrases in post: {', '.join(m.phrases)}"]
        funnel = stats.as_dict()
        funnel["pain_phrase_leads"] = with_pain
        return rows, funnel

    return _run(db, product_id, "hn_thread", run_id, work)


# ── Partner lane ─────────────────────────────────────────────────────────

def run_partner_scout(supabase_client: Optional[Any] = None, *, max_queries: int = 30,
                      max_firms: int = 25, run_id: Optional[str] = None, http_get=None,
                      scraper=None, sleep=None, segments: Optional[list[str]] = None,
                      page_offset: int = 0) -> dict:
    from agents.marketing.company_classification import normalise_company_name
    from agents.marketing.mkt_lead_finder import _get_icp_config, _this_months_brave_query_count
    from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID, PartnerStats, find_partner_leads
    from scrapers.brave_search import BraveSearchScraper

    db = supabase_client if supabase_client is not None else get_supabase_for_job()
    stats = PartnerStats()

    def work():
        icp = _get_icp_config(db, PARTNERS_PRODUCT_ID) or {}
        templates = [t for t in (icp.get("search_templates") or []) if isinstance(t, dict)
                     and (not segments or t.get("segment") in segments)]
        if not templates:
            raise RuntimeError("partner ICP has no search_templates -- nothing to search for")
        brave = scraper or BraveSearchScraper(query_count=_this_months_brave_query_count(db))
        if not getattr(brave, "api_key", None):
            raise RuntimeError("BRAVE_API_KEY is not set; the partner lane cannot search")
        existing = (db.table("mse_leads").select("company,domain,linkedin_url")
                    .eq("product_id", PARTNERS_PRODUCT_ID).execute().data or [])
        all_urls = {r["linkedin_url"] for r in
                    (db.table("mse_leads").select("linkedin_url").execute().data or []) if r.get("linkedin_url")}
        try:
            rows, _ = find_partner_leads(
                templates, scraper=brave, http_get=http_get, max_queries=max_queries, max_firms=max_firms,
                existing_domains={r["domain"] for r in existing if r.get("domain")},
                existing_companies={normalise_company_name(r["company"]) for r in existing if r.get("company")},
                existing_linkedin_urls=all_urls, stats=stats, sleep=sleep, page_offset=page_offset,
            )
        finally:
            _record_brave(db, stats.queries + stats.queries_contact)
        return rows, stats.as_dict()

    return _run(db, PARTNERS_PRODUCT_ID, "partner_signal", run_id, work)


# ── VC operating partners ────────────────────────────────────────────────

def run_vc_scout_entry(supabase_client: Optional[Any] = None, *, max_probes: int = 60,
                       run_id: Optional[str] = None, http_get=None, sleep=None) -> dict:
    from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID
    from agents.marketing.vc_partners import run_vc_scout

    db = supabase_client if supabase_client is not None else get_supabase_for_job()

    def work():
        rows, stats = run_vc_scout(db, http_get=http_get, max_probes=max_probes, sleep=sleep)
        return rows, stats.as_dict()

    return _run(db, PARTNERS_PRODUCT_ID, "vc_operating_partner", run_id, work)


# ── Pain-phrase backfill / rescan ────────────────────────────────────────

def _job_id(url: str) -> str:
    """The last path segment of a posting URL: greenhouse's numeric id,
    ashby's uuid, lever's uuid. Enough to find a lead's own posting on its
    board without caring which host spelling the URL used."""
    return (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _lead_text(lead: dict, client, cache: dict, out: dict, max_boards: int) -> Optional[str]:
    """The text a lead's pain phrases are read from, or None when nothing
    could be read (the row is then left alone and retried next time).

    HN leads: the stored post. Job-signal leads: their own posting when it is
    still on the board, else the whole board -- the posting text was never
    stored, and a closed posting's sibling roles are still this company."""
    from scrapers.ats_boards import board_ref_from_url

    if lead.get("source") == "hn_thread":
        return lead.get("signal_text") or None
    ref = board_ref_from_url(lead.get("job_posting_url") or "")
    if ref is None:
        out["no_board"] += 1
        return None
    if ref.cache_key not in cache:
        if out["boards_read"] + out["boards_failed"] >= max_boards:
            return None
        postings = client.fetch_board(ref)
        if client.board_status.get(ref.cache_key) == "failed":
            out["boards_failed"] += 1
            cache[ref.cache_key] = []
        else:
            out["boards_read"] += 1
            cache[ref.cache_key] = postings
    postings = cache[ref.cache_key]
    if not postings:
        return None
    want = _job_id(lead.get("job_posting_url") or "")
    own = [p for p in postings if want and _job_id(p.url or "") == want]
    if own:
        out["own_posting"] += 1
        return own[0].description_text or None
    out["whole_board"] += 1
    return " \n".join(p.description_text or "" for p in postings) or None


def _pain_update(lead: dict, match, pain, *, label: str) -> Optional[dict]:
    """The mse_leads update for one scanned lead, or None if nothing changed.

    Only phrases the lead does not already carry are added, and intent rises
    by exactly the boost those new phrases add under the cap -- a rescan
    after the list grows never boosts the same phrase twice."""
    old = list(lead.get("pain_phrases") or [])
    new = [p for p in match.phrases if p not in old]
    if not new:
        return None if lead.get("pain_phrases") is not None else {"pain_phrases": []}
    applied = min(pain.max_boost, pain.per_match * len(old))
    total = min(pain.max_boost, pain.per_match * (len(old) + len(new)))
    delta = round(total - applied, 3)
    evidence = list(lead.get("pain_evidence") or []) + [e for e in match.evidence if e["phrase"] in new]
    update: dict = {"pain_phrases": old + new, "pain_evidence": evidence}
    if lead.get("intent_score") is not None and delta > 0:
        update["intent_score"] = round(min(1.0, float(lead["intent_score"]) + delta), 3)
    reason = f"pain phrases in JD (+{delta:.2f}): {', '.join(new)} ({label})" if delta else \
        f"pain phrases in JD (boost already at cap): {', '.join(new)} ({label})"
    update["score_reasons"] = list(lead.get("score_reasons") or []) + [reason]
    return update


def scan_pain_phrases(db, product_id: str, *, rescan: bool, client=None, http_get=None, sleep=None,
                      max_boards: int = 150, cache: Optional[dict] = None) -> dict:
    """Match the product's pain phrases against its leads and write what is new.

    rescan=False touches only leads never scanned (pain_phrases IS NULL).
    rescan=True re-reads every lead, so phrases added to the ICP since a lead
    was scanned are found too."""
    from agents.marketing.mkt_lead_finder import _get_icp_config
    from agents.marketing.pain_phrases import PainConfig, match_pain_phrases
    from scrapers.ats_boards import AtsBoardClient

    client = client or AtsBoardClient(http_get=http_get, sleep=sleep)
    cache = cache if cache is not None else {}
    out = {"scanned": 0, "matched": 0, "newly_matched_leads": 0, "boards_read": 0, "boards_failed": 0,
           "no_board": 0, "own_posting": 0, "whole_board": 0, "phrases_configured": 0, "by_phrase": {},
           "new_by_phrase": {}, "updated_lead_ids": []}
    pain = PainConfig.from_icp(_get_icp_config(db, product_id))
    out["phrases_configured"] = len(pain.phrases)
    if not pain.phrases:
        return out
    q = (db.table("mse_leads")
         .select("id,company,source,job_posting_url,signal_text,intent_score,score_reasons,"
                 "pain_phrases,pain_evidence")
         .eq("product_id", product_id))
    if not rescan:
        q = q.is_("pain_phrases", "null")
    for lead in q.execute().data or []:
        text = _lead_text(lead, client, cache, out, max_boards)
        if not text:
            continue
        out["scanned"] += 1
        match = match_pain_phrases(text, pain.phrases)
        for ph in match.phrases:
            out["by_phrase"][ph] = out["by_phrase"].get(ph, 0) + 1
        if match:
            out["matched"] += 1
        update = _pain_update(lead, match, pain, label="rescan" if rescan else "backfill")
        if update is None:
            continue
        added = [p for p in update.get("pain_phrases") or [] if p not in (lead.get("pain_phrases") or [])]
        if added:
            out["newly_matched_leads"] += 1
            out["updated_lead_ids"].append(lead["id"])
            for ph in added:
                out["new_by_phrase"][ph] = out["new_by_phrase"].get(ph, 0) + 1
        db.table("mse_leads").update(update).eq("id", lead["id"]).execute()
    return out


def run_pain_backfill(supabase_client: Optional[Any] = None, *, http_get=None, sleep=None,
                      max_boards: int = 150) -> dict:
    """Scan leads that predate the pain-phrase list (pain_phrases IS NULL)."""
    from agents.marketing.mkt_lead_finder import CLOUD_DECODED_PRODUCT_ID, THDAGENTIC_CONSULTING_PRODUCT_ID
    from scrapers.ats_boards import AtsBoardClient

    db = supabase_client if supabase_client is not None else get_supabase_for_job()
    client, cache = AtsBoardClient(http_get=http_get, sleep=sleep), {}
    per = {pid: scan_pain_phrases(db, pid, rescan=False, client=client, cache=cache, max_boards=max_boards)
           for pid in (THDAGENTIC_CONSULTING_PRODUCT_ID, CLOUD_DECODED_PRODUCT_ID)}
    _audit(db, "win", THDAGENTIC_CONSULTING_PRODUCT_ID, "pain_phrase_backfill", per)
    return per


def run_pain_rescan(supabase_client: Optional[Any] = None, *, product_id: str,
                    run_id: Optional[str] = None, http_get=None, sleep=None, max_boards: int = 300) -> dict:
    """Weekly rescan of one product's leads (runs after the Sunday scout).

    Records an mse_lead_finder_runs row (sources_used=['pain_rescan'],
    leads_found=0 -- it finds no companies) so the Loop Map's Qualify desk can
    show it, plus audit_log win or lose."""
    db = supabase_client if supabase_client is not None else get_supabase_for_job()
    run_id = _start_run(db, product_id, "pain_rescan", run_id)
    try:
        out = scan_pain_phrases(db, product_id, rescan=True, http_get=http_get, sleep=sleep,
                                max_boards=max_boards)
    except Exception as exc:
        _finish_run(db, run_id, written=0, funnel={"error": str(exc)[:500]}, error=str(exc))
        _audit(db, "lose", product_id, "pain_rescan_run", {"run_id": run_id, "error": str(exc)})
        raise RuntimeError(f"pain rescan failed for product {product_id}: {exc}") from exc
    funnel = {k: v for k, v in out.items() if k != "updated_lead_ids"}
    _finish_run(db, run_id, written=0, funnel=funnel)
    _audit(db, "win", product_id, "pain_rescan_run", {"run_id": run_id, **out})
    return {"run_id": run_id, "status": "complete", "product_id": product_id, **out}
