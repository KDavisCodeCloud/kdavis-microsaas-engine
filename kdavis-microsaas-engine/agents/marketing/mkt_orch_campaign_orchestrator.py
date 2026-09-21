"""
MKT-ORCH Campaign Orchestrator.

Triggered when a product's research opportunity moves to approved
(Supabase DB trigger -> n8n webhook -> api/routers/marketing.py ->
run_campaign_orchestrator). Approval is the trigger: the campaign is
built fresh from the research report at approval time, because the ICP
is unknown until then.

Reads the approved product's research_report.json, runs select_channels()
to decide which channels apply, creates a campaign_builds row, and fans
out to the downstream agents: the lead source (mkt_lead_finder by
default — see below), MKT-O2 (Cold DM Sequence Writer), MKT-O3 (Email
Sequence Loader), MKT-S1 (SEO Content Factory), and MKT-V1 (Content
Multiplier, gated on reddit/facebook_groups being in the product's
icp_channels). Each still fires via a dynamic import attempt with a
graceful "not yet built — pending" fallback on ModuleNotFoundError
(matching agents/orchestrator/agent.py's existing pattern for vertical
intel agents) so a future downstream agent can be added to
_DOWNSTREAM_AGENTS ahead of actually being built.

Lead source routing (2026-08-14): MKT-O1 (Apollo) is deprecated — Apollo's
Free plan has no API access (agents/marketing/mkt_o1_apollo_list_builder.py's
own DEPRECATED flag) — and agents/marketing/mkt_lead_finder.py (self-hosted,
zero-cost, Google Custom Search + public license databases + SMTP email
verification) replaces it as the default. A campaign can still explicitly
opt back into MKT-O1 (e.g. once Apollo is upgraded to a paid plan again)
by setting research_report["use_legacy_apollo"] = true — see
_resolve_lead_source_module below. campaign_builds.apollo_status is left
alone (untouched column, still meaningful if MKT-O1 ever fires again);
the lead-finder path writes to the new lead_finder_status column instead
(migration 20260814000024_lead_finder.sql).
"""

from typing import Any, Callable, Optional

from core.supabase_client import get_supabase

AGENT_ID = "mkt-orch"

_LEAD_FINDER_MODULE = "agents.marketing.mkt_lead_finder"
_LEGACY_APOLLO_MODULE = "agents.marketing.mkt_o1_apollo_list_builder"


def _resolve_lead_source_module(research_report: dict) -> str:
    """mkt_lead_finder is the default lead source (see module docstring)
    — a campaign only routes back to the deprecated Apollo builder if its
    research_report explicitly asks for it, the "future Apollo upgrade
    path" this repo's task spec anticipated."""
    if research_report.get("use_legacy_apollo"):
        return _LEGACY_APOLLO_MODULE
    return _LEAD_FINDER_MODULE


# downstream agent -> (module path, campaign_builds status column, gate predicate)
# gate predicate takes the channels list from select_channels() and returns
# whether this agent should fire for this product. seo/email always fire
# (select_channels always includes them); the rest are conditional. The
# lead-source entry's module path is a placeholder here — the real path is
# resolved per-campaign by _resolve_lead_source_module (its
# research_report may ask for the deprecated Apollo builder instead), see
# the dispatch loop in run_campaign_orchestrator below.
# Order matters (2026-09-21): mkt-o3 is LAST, deliberately. Migration
# 053 added a DB-level trigger that makes mkt-o3's insert raise for any
# product without an approved mse_positioning row -- and this loop (see
# run_campaign_orchestrator below) has no per-agent isolation: an
# uncaught exception from one agent aborts every agent still to come.
# Before this reordering, mkt-o3 sat in the middle, so a positioning-gate
# rejection (now the normal case for every product except
# small-portfolio-hub, the only one with an approved brief as of
# 2026-09-21) would have silently prevented mkt-s1 (SEO) and mkt-v1
# (social) from ever firing for that campaign build, even though neither
# has anything to do with email or positioning. Running mkt-o3 last means
# every other agent's existing behavior is provably unchanged by the new
# gate -- they always run to completion before mkt-o3 gets a chance to
# raise. See tests/test_mkt_orch_campaign_orchestrator.py.
_DOWNSTREAM_AGENTS: list[tuple[str, str, str, Callable[[list[str]], bool]]] = [
    ("mkt-lead-finder", _LEAD_FINDER_MODULE, "lead_finder_status",
     lambda channels: "linkedin_dm" in channels),
    ("mkt-o2", "agents.marketing.mkt_o2_cold_dm_writer", "dm_sequence_status",
     lambda channels: "linkedin_dm" in channels),
    ("mkt-s1", "agents.marketing.mkt_s1_seo_content_factory", "seo_factory_status",
     lambda channels: "seo" in channels),
    ("mkt-v1", "agents.marketing.mkt_v1_content_multiplier", "social_status",
     lambda channels: "reddit" in channels or "facebook" in channels),
    ("mkt-o3", "agents.marketing.mkt_o3_email_sequence_loader", "email_sequence_status",
     lambda channels: "email" in channels),
]


def select_channels(research_report: dict) -> list[str]:
    """Exact logic from Campaign-Orchestrator-and-Strategy-Specs.md."""
    icp_location = research_report["icp_channels"]
    channels = ["seo", "email"]
    if "linkedin" in icp_location:
        channels.append("linkedin_dm")
    if "reddit" in icp_location:
        channels.append("reddit")
    if "facebook_groups" in icp_location:
        channels.append("facebook")
    return channels


def _load_research_report(db, product_id: str) -> dict:
    result = (
        db.table("mse_research_reports")
        .select("report_json")
        .eq("product_id", product_id)
        .order("cycle_date", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise RuntimeError(f"MKT-ORCH found no research_report for product {product_id} — MKT-R1 must run first")
    return result.data[0]["report_json"]


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": metadata.pop("action", "campaign_orchestrator_run"),
        "outcome": outcome,
        "product_id": metadata.get("product_id"),
        "metadata": metadata,
    }).execute()


def _fire_agent(agent_id: str, module_path: str, research_report: dict, campaign_build: dict) -> str:
    """Returns the status string to write back to campaign_builds."""
    import importlib
    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError:
        print(f"{agent_id.upper()} not yet built — pending")
        return "pending"

    run_fn: Optional[Callable[..., Any]] = getattr(mod, "run", None)
    if run_fn is None:
        print(f"{agent_id.upper()} module found but has no run() — pending")
        return "pending"

    run_fn(research_report=research_report, campaign_build=campaign_build)
    return "fired"


def run_campaign_orchestrator(
    product_id: str,
    research_opp_id: str,
    vertical: str,
    supabase_client=None,
) -> dict:
    """Builds and fans out a campaign for a just-approved product. Raises
    on any failure — never fails silently."""
    db = supabase_client if supabase_client is not None else get_supabase()

    _emit_event(db, "campaign_build_started", {
        "product_id": product_id, "research_opp_id": research_opp_id, "vertical": vertical,
    })

    try:
        research_report = _load_research_report(db, product_id)
        channels = select_channels(research_report)

        build_row = (
            db.table("campaign_builds")
            .insert({"product_id": product_id, "research_opp_id": research_opp_id})
            .execute()
        )
        if not build_row.data:
            raise RuntimeError(f"MKT-ORCH failed to create campaign_builds row for product {product_id}")
        campaign_build = build_row.data[0]

        _write_audit(db, "win", {
            "action": "campaign_build_created", "product_id": product_id,
            "research_opp_id": research_opp_id, "channels": channels,
        })

        status_updates: dict[str, str] = {}
        for agent_id, module_path, status_column, should_fire in _DOWNSTREAM_AGENTS:
            if not should_fire(channels):
                continue

            if module_path == _LEAD_FINDER_MODULE:
                module_path = _resolve_lead_source_module(research_report)

            status = _fire_agent(agent_id, module_path, research_report, campaign_build)
            status_updates[status_column] = status
            _write_audit(db, "win", {
                "action": f"{agent_id}_fired", "product_id": product_id,
                "research_opp_id": research_opp_id, "status": status,
            })

        if status_updates:
            db.table("campaign_builds").update(status_updates).eq("id", campaign_build["id"]).execute()

    except Exception as exc:
        _write_audit(db, "lose", {
            "action": "campaign_build_failed", "product_id": product_id,
            "research_opp_id": research_opp_id, "error": str(exc),
        })
        raise RuntimeError(f"MKT-ORCH campaign build failed for product {product_id}: {exc}") from exc

    _emit_event(db, "campaign_build_completed", {
        "product_id": product_id, "research_opp_id": research_opp_id, "channels": channels,
    })

    return {"campaign_build_id": campaign_build["id"], "channels": channels, "status_updates": status_updates}
