"""
MKT-LI Intake — LinkedIn manual-outreach lead intake.

Apollo.io is suspended (Free plan 403s on every call, upgrade deferred
indefinitely) — MKT-O1 is effectively dead for now, not removed, just
routed around. The active first-customer acquisition strategy is Kelvin's
own 1-2 hours/day on LinkedIn: he exports a CSV from a LinkedIn search, or
pastes a list of profiles who liked/commented on one of his posts, and
this agent normalizes that into mse_linkedin_leads (see
supabase/migrations/20260814000023_linkedin_leads.sql for why this is a
new table rather than reusing mse_apollo_leads — that table's
campaign_build_id is NOT NULL/FK'd to a MKT-ORCH campaign run, which these
leads were never part of).

This agent never sends anything to LinkedIn and never scrapes LinkedIn —
every lead's profile data is handed to it already-collected by Kelvin
(CSV export or pasted profile data), matching the deliberate manual-DM-only
ToS decision documented across this repo's marketing engine.
"""

import csv
import io
from typing import Any, Optional

from core.supabase_client import get_supabase

AGENT_ID = "mkt-li-intake"

VALID_SOURCES = {"linkedin_manual", "linkedin_engager"}


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "linkedin_lead_intake",
        "outcome": outcome,
        "product_id": metadata.get("product_id"),
        "metadata": metadata,
    }).execute()


def parse_csv_leads(raw_csv: str) -> list[dict]:
    """
    Parses the CSV shape a LinkedIn search export/paste produces: columns
    first_name, last_name, title, company, linkedin_url, location. Missing
    optional columns are fine — DictReader leaves them out of each row
    rather than erroring, and _normalize_lead below treats every field as
    optional except linkedin_url.
    """
    reader = csv.DictReader(io.StringIO(raw_csv))
    return [dict(row) for row in reader]


def _normalize_lead(raw: dict, source: str, product_id: Optional[str]) -> Optional[dict]:
    linkedin_url = (raw.get("linkedin_url") or "").strip()
    if not linkedin_url:
        return None
    return {
        "product_id": product_id,
        "first_name": raw.get("first_name") or None,
        "last_name": raw.get("last_name") or None,
        "title": raw.get("title") or None,
        "company": raw.get("company") or None,
        "linkedin_url": linkedin_url,
        "location": raw.get("location") or None,
        "source": source,
        # Engager-only context (source_post_url the post they engaged
        # with, interaction_type like/comment, interaction_note either
        # their actual comment text or the post's topic) — accepted for
        # linkedin_manual leads too and simply stored null, since a manual
        # CSV lead never carries this.
        "source_post_url": raw.get("source_post_url") or raw.get("post_url") or None,
        "interaction_type": raw.get("interaction_type") or None,
        "interaction_note": raw.get("interaction_note") or raw.get("post_topic") or None,
        "status": "pending_dm",
    }


def run_li_intake(
    leads: list[dict],
    source: str,
    product_id: Optional[str] = None,
    supabase_client: Optional[Any] = None,
) -> dict:
    """
    Normalizes and dedupes a batch of LinkedIn leads (already-collected
    profile data — CSV rows or pasted engager profiles, never scraped) and
    writes new ones to mse_linkedin_leads with status='pending_dm'.

    Dedup is on linkedin_url against every existing lead in the table
    (not scoped to this batch or this product — the same person shouldn't
    get a duplicate row just because Kelvin uploaded two different lists
    that both included them) plus within the batch itself. Returns
    {"added": int, "duplicates_skipped": int} — never raises for a
    duplicate, that's an expected, normal outcome of manual list-building,
    not a failure.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got {source!r}")

    db = supabase_client if supabase_client is not None else get_supabase()

    existing_urls = {
        row["linkedin_url"]
        for row in (db.table("mse_linkedin_leads").select("linkedin_url").execute().data or [])
        if row.get("linkedin_url")
    }

    to_insert: list[dict] = []
    seen_in_batch: set[str] = set()
    duplicates = 0
    skipped_no_url = 0

    for raw in leads:
        normalized = _normalize_lead(raw, source, product_id)
        if normalized is None:
            skipped_no_url += 1
            continue
        url = normalized["linkedin_url"]
        if url in existing_urls or url in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(url)
        to_insert.append(normalized)

    inserted: list[dict] = []
    if to_insert:
        result = db.table("mse_linkedin_leads").insert(to_insert).execute()
        inserted = result.data or []

    metadata = {
        "product_id": product_id, "source": source, "submitted": len(leads),
        "added": len(inserted), "duplicates_skipped": duplicates, "skipped_no_url": skipped_no_url,
    }
    _write_audit(db, "win", metadata)
    _emit_event(db, "linkedin_lead_intake_completed", metadata)

    return {"added": len(inserted), "duplicates_skipped": duplicates}
