"""
agents/marketing/brave_budget.py

Monthly Brave budget guard (Kelvin's decision 1e, 2026-10-02).

WHY A GUARD AND NOT JUST THE EXISTING CAP. scrapers/brave_search.py already
enforces FREE_TIER_MONTHLY_CAP (900) -- but it enforces it per QUERY, at the
moment the 901st one is attempted, by returning an empty result list. That is
the worst possible place to find out: a run that trips it does not fail, it
quietly finds nothing, and the funnel_stats show a legitimate-looking zero.
The budget is also shared across every product, so one Sunday sweep can strand
the rest of the month.

So this stops a run BEFORE it starts, at a reserve threshold rather than at
the wall: if the projected spend would push the month past 80% (720/900), the
run is refused with a reason that names the numbers. The remaining 180 queries
stay available for the manual, targeted work that actually closes deals --
domain lookups, a founder-band check, a single buyer search.

Reported in the weekly digest either way, because a budget that silently stops
work is the same failure mode as one that silently overspends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

# Mirrors scrapers.brave_search.FREE_TIER_MONTHLY_CAP. Imported lazily in
# monthly_cap() so this module stays importable without the scraper.
DEFAULT_MONTHLY_CAP = 900

# Stop at 80%, keeping ~180 queries in reserve for targeted manual work.
BUDGET_STOP_FRACTION = 0.80

# What one product's sweep costs, measured from the 2026-10-01/02 runs:
# 34 and 34 queries_total, 30 and 28 of them Brave. Rounded up, because a
# guard that underestimates is not a guard.
ESTIMATED_QUERIES_PER_PRODUCT = 35


def monthly_cap() -> int:
    try:
        from scrapers.brave_search import FREE_TIER_MONTHLY_CAP
        return int(FREE_TIER_MONTHLY_CAP)
    except Exception:
        return DEFAULT_MONTHLY_CAP


def stop_threshold(cap: Optional[int] = None) -> int:
    return int((cap if cap is not None else monthly_cap()) * BUDGET_STOP_FRACTION)


@dataclass
class BudgetDecision:
    """allowed=False is a REFUSAL to start, not a failure of a started run."""

    allowed: bool
    used: int
    cap: int
    threshold: int
    projected: int
    reason: str

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.used)

    @property
    def remaining_to_threshold(self) -> int:
        return max(0, self.threshold - self.used)

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "used": self.used,
            "cap": self.cap,
            "threshold": self.threshold,
            "projected": self.projected,
            "remaining": self.remaining,
            "remaining_to_threshold": self.remaining_to_threshold,
            "percent_used": round(100.0 * self.used / self.cap, 1) if self.cap else 0.0,
            "reason": self.reason,
        }


def check_budget(
    db: Any,
    *,
    estimated_queries: int = ESTIMATED_QUERIES_PER_PRODUCT,
    cap: Optional[int] = None,
) -> BudgetDecision:
    """May a run costing roughly `estimated_queries` start right now?

    Fails CLOSED on a read error. Everywhere else in this codebase a broken
    read fails open so a transient fault cannot halt the pipeline -- but the
    thing being protected here is a metered budget shared across a month, and
    "we could not tell how much we have spent" is not a licence to spend more.
    """
    real_cap = cap if cap is not None else monthly_cap()
    threshold = stop_threshold(real_cap)
    try:
        from agents.marketing.mkt_lead_finder import _this_months_brave_query_count
        used = int(_this_months_brave_query_count(db))
    except Exception as exc:
        log.error("[BraveBudget] could not read this month's usage: %s", exc)
        return BudgetDecision(
            allowed=False, used=-1, cap=real_cap, threshold=threshold, projected=-1,
            reason=f"refusing to start: could not read this month's Brave usage ({exc})",
        )

    projected = used + max(0, int(estimated_queries))
    if used >= threshold:
        return BudgetDecision(
            False, used, real_cap, threshold, projected,
            f"already at {used}/{real_cap} Brave queries this month, at or past the "
            f"{int(BUDGET_STOP_FRACTION * 100)}% stop threshold ({threshold})",
        )
    if projected > threshold:
        return BudgetDecision(
            False, used, real_cap, threshold, projected,
            f"a ~{estimated_queries}-query run would reach {projected}/{real_cap}, past the "
            f"{int(BUDGET_STOP_FRACTION * 100)}% stop threshold ({threshold}); "
            f"{threshold - used} queries left before it",
        )
    return BudgetDecision(
        True, used, real_cap, threshold, projected,
        f"ok: {used}/{real_cap} used, a ~{estimated_queries}-query run projects "
        f"{projected}, under the {threshold} stop threshold",
    )


# ── Per-source allocation (lead-source expansion, 2026-11-15) ──────────────
#
# The brief: "900 queries a month. Allocate them per source and log usage per
# run. Never exceed 300 in one run." The 80% stop threshold above still
# governs the whole month; these allocations divide the part under it so one
# source cannot spend another's share. Every usage event a source writes
# carries {"source": <key>}; events written before this change (or by code
# paths that predate it) have no source and count against the month only.
#
# Sized from the 2026-11-15 baseline (docs/lead-sources/00-baseline.md):
#   ats_scout       CD + CO weekly scouts. With discovery moved out they spend
#                   only domain + buyer lookups: ~20/run x 2 x 4.3 weeks.
#   slug_discovery  weekly new-slug discovery, ~36/week.
#   wellfound       monthly company-name signal (Phase 4).
#   partner_scout   monthly partner lane (unchanged, <=20).
#   buyer_snippet   the optional search-snippet buyer resolver. Its flag
#                   (ENABLE_SEARCH_SNIPPET_RESOLVER) is OFF by default, so this
#                   share is unused unless Kelvin turns it on.
# 200 + 160 + 40 + 20 + 60 = 480, leaving 240 of headroom under the 720
# threshold and the 180 reserve above it.
SOURCE_ALLOCATIONS: dict[str, int] = {
    "ats_scout": 200,
    "slug_discovery": 160,
    "wellfound": 40,
    "partner_scout": 20,
    "buyer_snippet": 60,
}
MAX_QUERIES_PER_RUN = 300


def source_usage(db, source: str, month: Optional[str] = None) -> int:
    """This month's Brave queries recorded under `source`."""
    from datetime import date
    month = month or date.today().isoformat()[:7]
    rows = (db.table("usage_events").select("metadata")
            .eq("event_type", "brave_search_queries_used").execute().data or [])
    return sum(int((r.get("metadata") or {}).get("count") or 0) for r in rows
               if isinstance(r.get("metadata"), dict) and r["metadata"].get("month") == month
               and r["metadata"].get("source") == source)


def check_source_budget(db: Any, source: str, estimated_queries: int, *,
                        cap: Optional[int] = None) -> BudgetDecision:
    """May `source` start a run costing ~`estimated_queries` right now?

    Refuses when (in order): the run alone exceeds MAX_QUERIES_PER_RUN, the
    month-wide check_budget refuses, or the source's own allocation would be
    exceeded. Fails closed on a read error, like check_budget."""
    if estimated_queries > MAX_QUERIES_PER_RUN:
        return BudgetDecision(False, -1, cap or monthly_cap(), stop_threshold(cap), -1,
                              f"a {estimated_queries}-query run exceeds the {MAX_QUERIES_PER_RUN}-per-run cap")
    overall = check_budget(db, estimated_queries=estimated_queries, cap=cap)
    if not overall.allowed:
        return overall
    allocation = SOURCE_ALLOCATIONS.get(source)
    if allocation is None:
        return BudgetDecision(False, overall.used, overall.cap, overall.threshold, overall.projected,
                              f"refusing to start: Brave source {source!r} has no allocation")
    try:
        used_by_source = source_usage(db, source)
    except Exception as exc:
        return BudgetDecision(False, overall.used, overall.cap, overall.threshold, -1,
                              f"refusing to start: could not read {source} usage ({exc})")
    if used_by_source + estimated_queries > allocation:
        return BudgetDecision(
            False, overall.used, overall.cap, overall.threshold, overall.projected,
            f"{source} has used {used_by_source}/{allocation} of its monthly Brave allocation; "
            f"a ~{estimated_queries}-query run would exceed it")
    return BudgetDecision(
        True, overall.used, overall.cap, overall.threshold, overall.projected,
        f"ok: {source} {used_by_source}/{allocation} this month; month {overall.used}/{overall.cap}")


def record_source_usage(db, source: str, count: int, **extra) -> None:
    """The one place a per-source Brave spend is written (usage_events)."""
    from datetime import date
    if not count:
        return
    db.table("usage_events").insert({
        "tenant_id": None, "event_type": "brave_search_queries_used",
        "metadata": {"month": date.today().isoformat()[:7], "count": int(count), "source": source, **extra},
    }).execute()


def usage_by_source(db, month: Optional[str] = None) -> dict[str, int]:
    """{source: queries} for the month; untagged events under 'unattributed'."""
    from datetime import date
    month = month or date.today().isoformat()[:7]
    rows = (db.table("usage_events").select("metadata")
            .eq("event_type", "brave_search_queries_used").execute().data or [])
    out: dict[str, int] = {}
    for r in rows:
        m = r.get("metadata")
        if isinstance(m, dict) and m.get("month") == month:
            key = m.get("source") or "unattributed"
            out[key] = out.get(key, 0) + int(m.get("count") or 0)
    return out
