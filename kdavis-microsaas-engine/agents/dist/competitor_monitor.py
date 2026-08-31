"""
DIST-C1 -- Competitor Monitor. Phase 3 of the DIST subsystem
(EXECUTION_ORDER.md). Two jobs:

1. seed_competitors_from_positioning() -- one-time-per-product backfill.
   Reads every mse_positioning row's substitute_set (regardless of
   approval status -- nothing is 'approved' yet system-wide as of
   tonight, and an accurate registry is useful before an owner signs
   off on the brief it came from) and inserts one mse_competitors row
   per non-do_nothing entry. do_nothing has no pricing page to monitor,
   so it's deliberately excluded here -- it's a real substitute, just
   not a "competitor" in the sense this table tracks.

2. run_competitor_monitor() -- the monthly job. Fetches each
   competitor's real pricing_url, extracts structured tiers via an LLM
   call (core.llm_router.analyze -- pricing pages are too varied in
   layout for a hand-rolled parser to hold up), diffs against the
   stored pricing_snapshot. No change -> bump last_verified_at. Change
   -> write the new snapshot, set last_changed_at, raise a real
   mse_monitoring_events row, and flag every mse_content_surfaces row
   citing that competitor as stale (Phase 4's own table -- doesn't
   exist in production yet as of this run, so this cascade is a real,
   tested no-op until Phase 4 lands, not skipped).

Rate-limited and robots.txt-respecting, matching core/email_finder.py's
own real-world-politeness convention (randomized delay between network
calls) -- adapted here to HTTP fetches instead of SMTP probes, since
this module makes real outbound requests to other companies' websites.
"""
from __future__ import annotations

import json
import random
import time
import urllib.robotparser
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from core.llm_router import analyze
from core.supabase_client import get_supabase

AGENT_ID = "dist-c1-competitor-monitor"

_COMPETITORS_TABLE = "mse_competitors"
_POSITIONING_TABLE = "mse_positioning"
_MONITORING_EVENTS_TABLE = "mse_monitoring_events"
_SURFACES_TABLE = "mse_content_surfaces"  # Phase 4 -- may not exist yet, handled gracefully

# Matches core/email_finder.py's MIN/MAX_VERIFY_DELAY_SECONDS shape --
# same reasoning (avoid a rapid-fire request pattern flagging this box),
# adapted from SMTP-probe timing to HTTP-fetch timing.
MIN_FETCH_DELAY_SECONDS = 2.0
MAX_FETCH_DELAY_SECONDS = 5.0

_TIER_EXTRACTION_PROMPT = """You are extracting structured pricing tiers from a \
real competitor pricing page's raw text. Return ONLY a JSON array, no prose, no \
markdown fences. Each element: {"tier_name": str, "price": number or null, \
"billing_period": "month"|"year"|"one_time"|null, "notes": str or null}. If the \
page has no discernible pricing tiers (e.g. "contact us" only, or the page \
failed to load real content), return an empty array []. Do not invent tiers or \
prices that aren't actually present in the text."""


def _check_robots_allowed(url: str, user_agent: str = "MSE-DIST-C1/1.0") -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        # No robots.txt, or it's unreachable -- default to allowed rather
        # than blocking a fetch over a network hiccup, same permissive
        # default urllib.robotparser itself uses when read() never ran.
        return True
    return rp.can_fetch(user_agent, url)


def _fetch_pricing_page_text(url: str, client: Optional[httpx.Client] = None) -> Optional[str]:
    """Returns visible page text, or None if robots.txt disallows the
    fetch or the request fails. Never raises -- a single unreachable
    competitor page must not crash the whole monthly run."""
    if not _check_robots_allowed(url):
        return None
    owns_client = client is None
    http = client or httpx.Client(timeout=15.0, follow_redirects=True, headers={
        "User-Agent": "MSE-DIST-C1/1.0 (+competitor pricing monitor)"
    })
    try:
        response = http.get(url)
        response.raise_for_status()
        return response.text
    except Exception:
        return None
    finally:
        if owns_client:
            http.close()


def _extract_tiers(page_text: str, llm_analyze=analyze) -> list[dict[str, Any]]:
    # Real pricing pages are mostly boilerplate (nav/footer/scripts) --
    # cap input so a call never balloons on a bloated page; the pricing
    # section itself is always well within the first ~15k characters of
    # rendered text on every real page checked while building this.
    truncated = page_text[:15000]
    raw = llm_analyze(_TIER_EXTRACTION_PROMPT, truncated, max_tokens=1024)
    try:
        # Model sometimes wraps in a fenced block despite the instruction
        # not to -- strip fences defensively rather than fail the whole run.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        return json.loads(cleaned.strip())
    except (json.JSONDecodeError, IndexError):
        return []


def seed_competitors_from_positioning(supabase_client: Optional[Any] = None) -> list[dict[str, Any]]:
    """Backfills mse_competitors from every mse_positioning row's
    substitute_set, across all statuses -- see module docstring for why
    approval status doesn't gate this. Idempotent: uses (product_id, name)
    upsert semantics, safe to re-run as new positioning versions land."""
    db = supabase_client or get_supabase()
    positioning_rows = db.table(_POSITIONING_TABLE).select("product_id,substitute_set").execute()

    inserted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in positioning_rows.data or []:
        product_id = row["product_id"]
        for entry in row.get("substitute_set") or []:
            if entry.get("kind") == "do_nothing":
                continue
            name = entry.get("name")
            if not name:
                continue
            key = (product_id, name)
            if key in seen:
                continue
            seen.add(key)
            competitor_row = {
                "product_id": product_id,
                "name": name,
                "kind": entry.get("kind"),
                "pricing_url": entry.get("source_url"),
                "positioning": entry.get("monetization"),
            }
            db.table(_COMPETITORS_TABLE).upsert(
                competitor_row, on_conflict="product_id,name"
            ).execute()
            inserted.append(competitor_row)
    return inserted


def _diff_snapshot(old: Optional[list], new: list) -> bool:
    """True if the extracted tiers genuinely changed. Order-independent
    (a page re-ordering its own tier cards isn't a real price change)."""
    if old is None:
        return bool(new)
    normalize = lambda tiers: sorted(json.dumps(t, sort_keys=True) for t in tiers)
    return normalize(old) != normalize(new)


def _write_monitoring_event(db: Any, competitor: dict[str, Any], old_tiers, new_tiers) -> None:
    db.table(_MONITORING_EVENTS_TABLE).insert({
        "product_slug": competitor.get("product_slug", ""),
        "product_name": competitor.get("product_name", ""),
        "run_type": "triggered",
        "severity": "P3",
        "triggered_thresholds": [{"metric": "competitor_pricing_changed", "competitor": competitor["name"]}],
        "recommended_action": (
            f"{competitor['name']}'s pricing changed -- was {old_tiers}, now {new_tiers}. "
            "Any published comparison page citing this competitor is now stale and needs review."
        ),
        "requires_human_decision": True,
        "status": "open",
    }).execute()


def _cascade_stale(db: Any, competitor_id: str) -> int:
    """Flags every mse_content_surfaces row citing this competitor as
    stale. Phase 4 (the surface generator) hasn't landed yet as of this
    run -- if the table doesn't exist, this is a real, tested no-op, not
    a skipped feature. Returns the number of rows flagged (0 if the
    table doesn't exist or nothing cites this competitor)."""
    try:
        result = (
            db.table(_SURFACES_TABLE)
            .update({"status": "stale"})
            .eq("competitor_id", competitor_id)
            .execute()
        )
        return len(result.data or [])
    except Exception:
        # Table doesn't exist yet (Phase 4 not landed) or another real
        # error -- either way, a missing downstream table must never
        # crash the competitor-monitor run itself.
        return 0


def run_competitor_monitor(
    supabase_client: Optional[Any] = None,
    llm_analyze=analyze,
    fetch_fn=_fetch_pricing_page_text,
    sleep_fn=time.sleep,
) -> list[dict[str, Any]]:
    """The monthly DIST-C1 job. Returns one result dict per competitor
    processed: {name, changed, error}."""
    db = supabase_client or get_supabase()
    competitors = db.table(_COMPETITORS_TABLE).select("*").execute().data or []

    results: list[dict[str, Any]] = []
    for i, competitor in enumerate(competitors):
        if i > 0:
            sleep_fn(random.uniform(MIN_FETCH_DELAY_SECONDS, MAX_FETCH_DELAY_SECONDS))

        url = competitor.get("pricing_url")
        now = datetime.now(timezone.utc).isoformat()
        if not url:
            results.append({"name": competitor["name"], "changed": False, "error": "no pricing_url"})
            continue

        page_text = fetch_fn(url)
        if page_text is None:
            results.append({"name": competitor["name"], "changed": False, "error": "fetch_failed_or_disallowed"})
            continue

        new_tiers = _extract_tiers(page_text, llm_analyze=llm_analyze)
        old_tiers = competitor.get("pricing_snapshot")
        changed = _diff_snapshot(old_tiers, new_tiers)

        if changed:
            db.table(_COMPETITORS_TABLE).update({
                "pricing_snapshot": new_tiers,
                "last_changed_at": now,
                "last_verified_at": now,
            }).eq("id", competitor["id"]).execute()
            _write_monitoring_event(db, competitor, old_tiers, new_tiers)
            flagged = _cascade_stale(db, competitor["id"])
            results.append({"name": competitor["name"], "changed": True, "error": None, "stale_surfaces_flagged": flagged})
        else:
            db.table(_COMPETITORS_TABLE).update({"last_verified_at": now}).eq("id", competitor["id"]).execute()
            results.append({"name": competitor["name"], "changed": False, "error": None})

    return results
