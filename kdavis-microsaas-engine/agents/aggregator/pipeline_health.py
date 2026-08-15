"""
Pipeline health auto-recalibration (Kelvin's rule, 2026-08-15).

Verdict and Dispatch are both stateless per-call LLM invocations — neither
has any memory of prior submissions. This module is that memory: it
computes the real build rate (BUILD/CONDITIONAL as a % of the last N
real evaluated submissions, merged from opportunity_pipeline +
opportunity_pipeline_rejections and ordered by real timestamp) and, when
it drops below the 10% sustainability floor with one failure category
clearly dominant (>40% of the window), auto-applies the one defined
recalibration for that category. Every trigger — acted on or not — is
logged as a row in mse_pipeline_recalibrations, which is also the
runtime source of truth agents/aggregator/agent.py and
agents/orchestrator/agent.py read from (get_active_recalibration_text)
to inject the corresponding instruction into Verdict's or Dispatch's
system prompt at call time. Deliberately not a self-modifying prompt.md
file on disk — a DB row is fully reversible (deactivate it), fully
logged (the row itself is the log), and needs no redeploy to take effect
or roll back. This mirrors how the 2026-07-20 kill-rate fix changed
Dispatch's real behavior, just through a safer mechanism.

Two of the eight failure categories are PERMANENT hard stops with no
recalibration action defined at all, by design, per Kelvin's explicit
rule ("never loosen the core disqualification criteria... these are hard
stops"): THIRD_PARTY_APPROVAL_GATE and MID_ACQUISITION_PLATFORM. A
dominant category here is still logged (mse_pipeline_recalibrations row,
target='none') for visibility into what's actually killing submissions,
but no instruction is ever injected anywhere for these two. API_CAPABILITY
DOES have a defined action — the hard-stop *check* itself is never
loosened, but what Dispatch researches for changes (steer toward
read-only concepts that don't predictably fail it), which is not the
same thing as loosening the check.
"""
import logging
from typing import Optional

from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

ROLLING_WINDOW = 10
KILL_RATE_TRIGGER_THRESHOLD = 0.10  # build rate below this triggers review
DOMINANT_CATEGORY_THRESHOLD = 0.40  # a category must kill >40% of the window to count as "dominant"
_MIN_DISTINCT_TOOLS = 3  # fewer distinct existing_tool names than this across the window reads as pool exhaustion

_QUALIFYING_STATUSES = {"READY_TO_BUILD", "validated"}
_STEP_TO_CATEGORY = {"1": "PAIN_NOT_CONFIRMED", "2": "GAP_NOT_IDENTIFIED", "3": "MRR_FLOOR"}

_RESEARCH_POOL_ACTION = (
    "PIPELINE HEALTH RECALIBRATION (auto-applied): the research pool looks "
    "exhausted — fewer real submissions than the review window, or the same "
    "handful of anchor tools repeating across the recent window. Add "
    "Reddit, AppSumo, and job-posting sources to the search strategy before "
    "the next batch, alongside the existing G2/Capterra/forum sources."
)

# category -> (target, instruction text injected into that target's system
# prompt). No entry for THIRD_PARTY_APPROVAL_GATE or MID_ACQUISITION_PLATFORM
# — see module docstring.
_RECALIBRATION_ACTIONS: dict[str, tuple[str, str]] = {
    "API_CAPABILITY": (
        "dispatch",
        "PIPELINE HEALTH RECALIBRATION (auto-applied): the API-capability hard "
        "stop has been killing over 40% of recent submissions. Expand research "
        "to include 'read-only reporting' product concepts — dashboards, "
        "alerts, exports, and analytics layers that only READ from the "
        "existing_tool's API, requiring no write/action capability the "
        "platform doesn't support. This does not loosen the API-capability "
        "check itself — it steers away from proposing concepts that "
        "predictably fail it.",
    ),
    "INTEGRATION_DEPENDENCY": (
        "verdict",
        "PIPELINE HEALTH RECALIBRATION (auto-applied): integration dependency "
        "count has been killing over 40% of recent submissions. Recalibrated "
        "soft criterion: a solution concept with exactly ONE required "
        "integration to a widely-adopted platform (Stripe, Gmail, Slack, or "
        "an equivalently ubiquitous connection — judge this the same way "
        "you judge existing_tool's own market position) is CONDITIONAL, not "
        "DO_NOT_BUILD, all else equal. Two or more required integrations, or "
        "a single integration to a niche/less-adopted platform, is still "
        "DO_NOT_BUILD. This does not touch the three permanent hard stops "
        "(third-party approval gate, mid-acquisition platform, API cannot "
        "perform core action) — those apply regardless of integration count.",
    ),
    "MRR_FLOOR": (
        "verdict",
        "PIPELINE HEALTH RECALIBRATION (auto-applied): the MRR floor check has "
        "been killing over 40% of recent submissions. Before rejecting on "
        "price alone, verify the current market rate more thoroughly — "
        "search for the existing_tool's actual current pricing (not "
        "training-data memory), check for recent price increases, and check "
        "whether a higher price tier than Dispatch proposed is realistic for "
        "the ICP before concluding the floor cannot clear. This does not "
        "loosen the $3,500 absolute floor or any price-adjusted floor value "
        "— it improves the accuracy of the price research feeding into that "
        "unchanged floor.",
    ),
}


def classify_result(row: dict) -> str:
    """
    Attributes a single evaluated submission to exactly one failure
    category, using Verdict's own sequential step structure (it stops at
    the first step that fails, per its own prompt rules) so a submission
    is never double-counted across categories.
    """
    if row.get("status") == "killed_below_floor":
        return "MRR_FLOOR"

    v2 = row.get("verdict_v2_output") or {}
    failed_step = str(v2.get("failed_at_step") or "")

    if failed_step == "2.5":
        reason = v2.get("step_2_5_failure_reason")
        if reason == "API_CANNOT_PERFORM_CORE_ACTION":
            return "API_CAPABILITY"
        if reason in ("THIRD_PARTY_APPROVAL_GATE", "MID_ACQUISITION_PLATFORM"):
            return reason
        return "OTHER"
    if failed_step in _STEP_TO_CATEGORY:
        return _STEP_TO_CATEGORY[failed_step]

    # A code-level floor-check demotion (agent.py's own price-adjusted-floor
    # re-verification) never sets failed_at_step at all — Step 3 said it
    # cleared, code disagreed. Still a math/floor failure.
    if row.get("status") == "rejected" and v2.get("floor_cleared") is False:
        return "MRR_FLOOR"

    # Integration-dependency soft-rejects: Steps 1-3 all passed (no
    # failed_at_step), but Verdict's own reasoning named this as the reason
    # via the (also new) integration_dependency_count field.
    integration_count = v2.get("integration_dependency_count")
    if row.get("status") in ("rejected", "watch") and integration_count is not None and integration_count >= 1:
        return "INTEGRATION_DEPENDENCY"

    return "OTHER"


def get_recent_window(supabase_client=None, window: int = ROLLING_WINDOW) -> list[dict]:
    """
    Merges opportunity_pipeline (READY_TO_BUILD/validated/watch/rejected)
    and opportunity_pipeline_rejections (killed_below_floor, archived
    separately per the hard MRR gate) into one normalized, time-ordered
    list — the real rolling window across the factory's whole history,
    not just one batch. Over-fetches (3x window) from each table before
    merging since the two tables' relative recency isn't known in advance.
    """
    db = supabase_client if supabase_client is not None else get_supabase()

    pipeline_rows = (
        db.table("opportunity_pipeline")
        .select("status,vertical,solution_concept,rejection_reason,verdict_v2_output,created_at")
        .order("created_at", desc=True)
        .limit(window * 3)
        .execute()
        .data
        or []
    )
    rejection_rows = (
        db.table("opportunity_pipeline_rejections")
        .select("original_opportunity,archived_at")
        .order("archived_at", desc=True)
        .limit(window * 3)
        .execute()
        .data
        or []
    )

    normalized: list[dict] = []
    for row in pipeline_rows:
        normalized.append({
            "status": row.get("status"),
            "vertical": row.get("vertical"),
            "solution_concept": row.get("solution_concept"),
            "rejection_reason": row.get("rejection_reason"),
            "verdict_v2_output": row.get("verdict_v2_output") or {},
            "timestamp": row.get("created_at"),
        })
    for row in rejection_rows:
        orig = row.get("original_opportunity") or {}
        normalized.append({
            "status": "killed_below_floor",
            "vertical": orig.get("vertical"),
            "solution_concept": orig.get("solution_concept"),
            "rejection_reason": orig.get("rejection_reason"),
            "verdict_v2_output": orig.get("verdict_v2_output") or {},
            "timestamp": row.get("archived_at"),
        })

    normalized.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return normalized[:window]


def compute_build_rate(rows: list[dict]) -> float:
    if not rows:
        return 1.0  # no data at all is not itself a low-build-rate signal -- avoid a false trigger on an empty/fresh factory
    qualifying = sum(1 for r in rows if r.get("status") in _QUALIFYING_STATUSES)
    return qualifying / len(rows)


def rejection_distribution(rows: list[dict]) -> dict[str, float]:
    """Percentage is of the FULL window (matching "killing >40%" of
    submissions, not 40% of just the rejects) — the two converge closely
    whenever build rate is actually low, but this is the literal reading."""
    if not rows:
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("status") in _QUALIFYING_STATUSES:
            continue
        cat = classify_result(row)
        counts[cat] = counts.get(cat, 0) + 1
    total = len(rows)
    return {cat: count / total for cat, count in counts.items()}


def research_pool_exhausted(rows: list[dict], window: int) -> bool:
    if len(rows) < window:
        return True  # fewer real submissions than the review window -- Dispatch isn't producing volume, not a scoring problem
    distinct_tools = {
        ((r.get("verdict_v2_output") or {}).get("existing_tool") or {}).get("name")
        for r in rows
    }
    distinct_tools.discard(None)
    return len(distinct_tools) <= _MIN_DISTINCT_TOOLS


def _write_recalibration(db, *, window: int, build_rate: float, dominant_category: str,
                          category_pct: float, action_taken: str, target: str, sample: list[dict]) -> dict:
    # Replace, don't stack -- a fresh trigger for the same target supersedes
    # whatever instruction was previously active there, keeping exactly one
    # active recalibration per target at a time.
    if target != "none":
        db.table("mse_pipeline_recalibrations").update({"active": False}).eq("target", target).eq("active", True).execute()

    sample_summary = [
        {"vertical": r.get("vertical"), "solution_concept": r.get("solution_concept"), "status": r.get("status")}
        for r in sample
    ]
    row = {
        "window_size": window,
        "build_rate_pct": round(build_rate * 100, 1),
        "dominant_category": dominant_category,
        "category_pct": round(category_pct * 100, 1),
        "action_taken": action_taken,
        "target": target,
        "active": target != "none",  # a 'none' (no defined action) row is logged but never injectable
        "sample": sample_summary,
    }
    result = db.table("mse_pipeline_recalibrations").insert(row).execute()
    log.warning(
        "[pipeline_health] build rate %.1f%% over last %d submissions, dominant category %s (%.1f%%) -> target=%s",
        build_rate * 100, window, dominant_category, category_pct * 100, target,
    )
    return result.data[0] if result.data else row


def check_and_recalibrate(supabase_client=None, window: int = ROLLING_WINDOW) -> Optional[dict]:
    """
    The main entry point — called once per research run (see
    agents/orchestrator/agent.py's check_pipeline_health graph node),
    after that run's results have been written. Returns the recalibration
    row if one was triggered (acted on or merely logged), None if the
    pipeline is healthy or no single category dominates yet.
    """
    db = supabase_client if supabase_client is not None else get_supabase()
    rows = get_recent_window(db, window=window)

    build_rate = compute_build_rate(rows)
    if build_rate >= KILL_RATE_TRIGGER_THRESHOLD:
        return None

    if research_pool_exhausted(rows, window):
        return _write_recalibration(
            db, window=window, build_rate=build_rate,
            dominant_category="RESEARCH_POOL_EXHAUSTED", category_pct=1.0,
            action_taken=_RESEARCH_POOL_ACTION, target="dispatch", sample=rows,
        )

    distribution = rejection_distribution(rows)
    if not distribution:
        return None
    dominant_category, dominant_pct = max(distribution.items(), key=lambda kv: kv[1])
    if dominant_pct < DOMINANT_CATEGORY_THRESHOLD:
        return None  # below the health floor, but nothing specific enough to act on yet

    action = _RECALIBRATION_ACTIONS.get(dominant_category)
    target = action[0] if action else "none"
    action_taken = action[1] if action else (
        f"No defined recalibration for {dominant_category} — logged for visibility only. "
        f"This is a permanent hard-stop category per Kelvin's rule; it is never auto-loosened."
    )
    return _write_recalibration(
        db, window=window, build_rate=build_rate, dominant_category=dominant_category,
        category_pct=dominant_pct, action_taken=action_taken, target=target, sample=rows,
    )


def get_active_recalibration_text(target: str, supabase_client=None) -> str:
    """Read by agents/aggregator/agent.py (target='verdict') and
    agents/orchestrator/agent.py (target='dispatch') to inject the current
    live recalibration instruction into that agent's system prompt at
    call time. Returns "" when nothing is active — the normal case."""
    db = supabase_client if supabase_client is not None else get_supabase()
    result = (
        db.table("mse_pipeline_recalibrations")
        .select("action_taken")
        .eq("target", target)
        .eq("active", True)
        .order("triggered_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0]["action_taken"] if rows else ""
