"""
DIST Task 5 -- Deploy-Freshness Monitor (2026-08-31).

Real motivating incident, twice now: Railway's mse-api auto-deploy
silently broke on 2026-08-23 and ran 8 days behind (caught by accident
overnight on 2026-08-30/31 while debugging something else) -- and broke
AGAIN by the time this exact module was being built (last real deploy
attempt 05:29 UTC, ~11 hours and ~10 commits before this was caught and
fixed live via connect-service-source + redeploy). Nothing alerted
either time. This module is what alerts next time, on any service, not
just mse-api.

Each service entry below is a real, enumerated Railway/Vercel service
(see the module-level SERVICES list) with the real repo it's supposed to
be tracking. A service with no determinable git-commit metadata (e.g. a
Railway service deployed via bare `railway up`/CLI redeploy instead of
the GitHub integration) is reported as "can't determine freshness" --
never silently treated as fresh.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.supabase_client import get_supabase

AGENT_ID = "dist-deploy-freshness-monitor"
STALE_HOURS = 24


@dataclass
class ServiceTarget:
    name: str
    platform: str  # "railway" | "vercel"
    repo: str  # "KDavisCodeCloud/<repo>"
    branch: str


# The real, enumerated portfolio as of 2026-08-31 -- confirmed via
# mcp__railway__list-projects/list-services and the Vercel API, not
# guessed. Railway infra-only services (Postgres, n8n) are excluded --
# they don't track an application git repo, "freshness" isn't a
# meaningful concept for them.
SERVICES: list[ServiceTarget] = [
    ServiceTarget("small-portfolio-hub/backend", "railway", "KDavisCodeCloud/small-portfolio-hub", "main"),
    ServiceTarget("thd-consulting-platform/marketing", "railway", "KDavisCodeCloud/thd-consulting-platform", "main"),
    ServiceTarget("thd-consulting-platform/monitoring", "railway", "KDavisCodeCloud/thd-consulting-platform", "main"),
    ServiceTarget("thd-consulting-platform/offboarding", "railway", "KDavisCodeCloud/thd-consulting-platform", "main"),
    ServiceTarget("thd-consulting-platform/contracts", "railway", "KDavisCodeCloud/thd-consulting-platform", "main"),
    ServiceTarget("thd-consulting-platform/onboarding", "railway", "KDavisCodeCloud/thd-consulting-platform", "main"),
    ServiceTarget("thd-consulting-platform/client-portal-api", "railway", "KDavisCodeCloud/thd-consulting-platform", "main"),
    ServiceTarget("thd-consulting-platform/audit-framework", "railway", "KDavisCodeCloud/thd-consulting-platform", "main"),
    ServiceTarget("decoded-empire-orchestrator", "railway", "KDavisCodeCloud/decoded-empire-orchestrator", "main"),
    # kdavis-agentic-platform's Railway service has no GitHub-integration
    # deploy history at all (its last deployment carries no commitHash,
    # reason="redeploy" only -- a bare CLI redeploy). ceo-dashboard's real,
    # actively-updated surface is the Vercel-deployed Next.js frontend,
    # tracked separately below. Kept in the list (not silently dropped)
    # specifically so the monitor reports it as un-checkable rather than
    # omitting a real deployed service from coverage entirely.
    ServiceTarget("kdavis-agentic-platform (railway)", "railway", "KDavisCodeCloud/kdavis-agentic-platform", "master"),
    ServiceTarget("showing-signal", "railway", "KDavisCodeCloud/showing-signal", "master"),
    ServiceTarget("mse-api", "railway", "KDavisCodeCloud/kdavis-microsaas-engine", "main"),
    ServiceTarget("decoded-six", "railway", "KDavisCodeCloud/decoded-six", "master"),
    ServiceTarget("small-portfolio-hub (vercel)", "vercel", "KDavisCodeCloud/small-portfolio-hub", "main"),
    ServiceTarget("thd-agentic-systems-website (vercel)", "vercel", "KDavisCodeCloud/thd-agentic-systems-website", "main"),
    ServiceTarget("ceo-dashboard (vercel)", "vercel", "KDavisCodeCloud/kdavis-agentic-platform", "master"),
    ServiceTarget("decoded-six (vercel)", "vercel", "KDavisCodeCloud/decoded-six", "master"),
]


def check_service_freshness(
    service: ServiceTarget,
    deployed_commit: Optional[str],
    deployed_at: Optional[datetime],
    head_commit: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Pure comparison, no I/O -- the real Railway/Vercel API calls and
    git ls-remote lookups happen in run_freshness_check below and get
    injected here as plain values, so this function (the actual
    stale/fresh logic) is fully unit-testable without real network calls."""
    now = now or datetime.now(timezone.utc)

    if deployed_commit is None or deployed_at is None:
        return {
            "service": service.name,
            "status": "unknown",
            "reason": "no_commit_metadata -- not deployed via a trackable git integration",
        }

    if deployed_commit == head_commit:
        return {"service": service.name, "status": "fresh", "deployed_commit": deployed_commit}

    age_hours = (now - deployed_at).total_seconds() / 3600
    if age_hours < STALE_HOURS:
        # Behind HEAD, but within normal deploy-propagation lag -- a push
        # a few minutes before a freshness check runs shouldn't false-alarm.
        return {
            "service": service.name,
            "status": "behind_but_recent",
            "deployed_commit": deployed_commit,
            "head_commit": head_commit,
            "age_hours": round(age_hours, 1),
        }

    return {
        "service": service.name,
        "status": "stale",
        "deployed_commit": deployed_commit,
        "head_commit": head_commit,
        "age_hours": round(age_hours, 1),
    }


def raise_stale_alert(result: dict[str, Any], supabase_client: Optional[Any] = None) -> None:
    """Writes one mse_monitoring_events row for a stale service, same
    shape/severity-tier convention as Phase 2's indexation circuit
    breaker (agents/dist/indexation_monitor.py's check_stale_and_alert) --
    matched deliberately, not reinvented. Dedups against an already-open
    event for the same service via the `context` column, same pattern."""
    db = supabase_client or get_supabase()

    existing = (
        db.table("mse_monitoring_events")
        .select("id")
        .eq("product_slug", "dist-infra")
        .eq("context", result["service"])
        .eq("status", "open")
        .execute()
    )
    if existing.data:
        return

    db.table("mse_monitoring_events").insert(
        {
            "product_slug": "dist-infra",
            "product_name": "DIST Infrastructure",
            "run_type": "nightly",
            "severity": "P2",
            "triggered_thresholds": [
                {"metric": "deploy_age_hours", "value": result["age_hours"], "threshold": STALE_HOURS}
            ],
            "recommended_action": (
                f"{result['service']} is running commit {result['deployed_commit'][:10]} "
                f"({result['age_hours']}h old) while origin/{'/'.join(['main'])} HEAD is "
                f"{result['head_commit'][:10]} -- auto-deploy has likely broken. Check the "
                "service's GitHub integration (Railway: connect-service-source; Vercel: "
                "re-link the project) and redeploy."
            ),
            "requires_human_decision": True,
            "context": result["service"],
            "status": "open",
        }
    ).execute()


def run_freshness_check(
    services: list[ServiceTarget],
    get_deployed_state: Callable[[ServiceTarget], tuple[Optional[str], Optional[datetime]]],
    get_head_commit: Callable[[str, str], str],
    supabase_client: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Real orchestration: for each service, fetch its real deployed
    commit/timestamp and the real origin HEAD for its repo/branch, compare,
    and alert on anything stale. get_deployed_state/get_head_commit are
    injected (Railway MCP calls, Vercel API calls, git ls-remote) so this
    is testable without real network access -- same dependency-injection
    convention as agents/dist/indexation_monitor.py."""
    db = supabase_client or get_supabase()
    results = []
    for service in services:
        deployed_commit, deployed_at = get_deployed_state(service)
        head_commit = get_head_commit(service.repo, service.branch)
        result = check_service_freshness(service, deployed_commit, deployed_at, head_commit, now=now)
        if result["status"] == "stale":
            raise_stale_alert(result, supabase_client=db)
        results.append(result)
    return results
