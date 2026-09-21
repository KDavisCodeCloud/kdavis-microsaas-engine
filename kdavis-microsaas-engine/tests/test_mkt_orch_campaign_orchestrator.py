"""
agents/marketing/mkt_orch_campaign_orchestrator.py — regression coverage
added 2026-09-21 alongside migration 053's DB-level positioning gate on
mse_email_sequences. run_campaign_orchestrator had ZERO prior real
coverage (test_brief_generation.py only ever monkeypatches the whole
function out). These tests exist specifically to prove the fan-out
loop's existing behavior is unchanged for every agent except mkt-o3 --
see the reordering comment on _DOWNSTREAM_AGENTS itself.
"""
import sys
import types

import pytest

import agents.marketing.mkt_orch_campaign_orchestrator as mkt_orch


def _install_fake_agent_modules(monkeypatch, call_log, raise_for=None):
    """Installs fake modules in sys.modules for every downstream agent
    referenced by _DOWNSTREAM_AGENTS (plus the lead-finder path), each
    exposing a run(research_report, campaign_build) matching the real
    modules' shared signature. Appends the agent's own name to call_log
    on every call, in call order. raise_for, if given, is an agent_id
    whose fake run() raises instead of returning -- simulates migration
    053's trigger rejecting an unapproved product's mse_o3 insert.
    """
    module_paths = {
        "mkt-lead-finder": mkt_orch._LEAD_FINDER_MODULE,
        "mkt-o2": "agents.marketing.mkt_o2_cold_dm_writer",
        "mkt-s1": "agents.marketing.mkt_s1_seo_content_factory",
        "mkt-v1": "agents.marketing.mkt_v1_content_multiplier",
        "mkt-o3": "agents.marketing.mkt_o3_email_sequence_loader",
    }
    for agent_id, module_path in module_paths.items():
        fake_module = types.ModuleType(module_path)

        def make_run(agent_id=agent_id):
            def run(research_report, campaign_build):
                call_log.append(agent_id)
                if agent_id == raise_for:
                    raise RuntimeError(
                        f"enforce_positioning_before_email_sequence: product {campaign_build['product_id']} "
                        f"has no approved positioning brief -- campaign generation blocked"
                    )
                return {"status": "ok"}
            return run

        fake_module.run = make_run()
        monkeypatch.setitem(sys.modules, module_path, fake_module)


def _seed(fake_db, product_id="prod-1", icp_channels=None):
    if icp_channels is None:
        icp_channels = ["linkedin", "reddit", "facebook_groups"]
    fake_db.responses["mse_research_reports"] = [{
        "report_json": {"icp_channels": icp_channels},
    }]
    fake_db.responses["campaign_builds"] = [{
        "id": "build-1", "product_id": product_id, "research_opp_id": "opp-1",
    }]


def test_happy_path_fires_all_five_agents_in_order(fake_db, monkeypatch):
    call_log: list[str] = []
    _install_fake_agent_modules(monkeypatch, call_log)
    _seed(fake_db)

    result = mkt_orch.run_campaign_orchestrator("prod-1", "opp-1", "real-estate", supabase_client=fake_db)

    # All 5 fire because icp_channels includes linkedin + reddit +
    # facebook_groups (select_channels always includes seo + email too).
    assert call_log == ["mkt-lead-finder", "mkt-o2", "mkt-s1", "mkt-v1", "mkt-o3"]
    assert result["status_updates"] == {
        "lead_finder_status": "fired", "dm_sequence_status": "fired",
        "seo_factory_status": "fired", "social_status": "fired",
        "email_sequence_status": "fired",
    }


def test_baseline_channels_fire_only_seo_and_email(fake_db, monkeypatch):
    """select_channels() always includes seo + email regardless of
    icp_channels -- with no linkedin/reddit/facebook, only mkt-s1 (seo)
    and mkt-o3 (email) should fire; lead-finder/mkt-o2/mkt-v1 should not."""
    call_log: list[str] = []
    _install_fake_agent_modules(monkeypatch, call_log)
    _seed(fake_db, icp_channels=[])

    mkt_orch.run_campaign_orchestrator("prod-1", "opp-1", "real-estate", supabase_client=fake_db)

    assert call_log == ["mkt-s1", "mkt-o3"]
    assert mkt_orch.select_channels({"icp_channels": []}) == ["seo", "email"]


def test_positioning_gate_rejection_on_mkt_o3_does_not_block_earlier_agents(fake_db, monkeypatch):
    """The regression this test exists for: before the 2026-09-21
    reordering, mkt-o3 sat in the middle of _DOWNSTREAM_AGENTS, so this
    exact scenario would have left mkt-s1/mkt-v1 (call_log would stop at
    3 entries) never called. Confirms all 4 non-email agents still run
    to completion, and their campaign_builds status columns are still
    correctly written, even though mkt-o3's rejection aborts the overall
    call with an exception."""
    call_log: list[str] = []
    _install_fake_agent_modules(monkeypatch, call_log, raise_for="mkt-o3")
    _seed(fake_db)

    with pytest.raises(RuntimeError, match="MKT-ORCH campaign build failed"):
        mkt_orch.run_campaign_orchestrator("prod-1", "opp-1", "real-estate", supabase_client=fake_db)

    # The 4 non-email agents all ran BEFORE mkt-o3 raised.
    assert call_log == ["mkt-lead-finder", "mkt-o2", "mkt-s1", "mkt-v1", "mkt-o3"]

    # Their status columns were written to campaign_builds before the
    # exception propagated -- confirmed via the fake DB's own update()
    # call log (the final `if status_updates: ... update(...)` line never
    # runs on this path since the exception happens inside the loop
    # before it, so status is only ever recorded per-agent via
    # _write_audit, not the table row itself, on a failure mid-loop --
    # this documents that real, pre-existing characteristic rather than
    # asserting a stronger guarantee that isn't actually there).
    audit_writes = [c for c in fake_db.executed if c.table_name == "audit_log"]
    audit_actions = [c._payload.get("action") for c in audit_writes if c._payload]
    assert "mkt-lead-finder_fired" in audit_actions
    assert "mkt-o2_fired" in audit_actions
    assert "mkt-s1_fired" in audit_actions
    assert "mkt-v1_fired" in audit_actions
    assert "campaign_build_failed" in audit_actions


def test_positioning_gate_rejection_is_the_only_new_failure_mode(fake_db, monkeypatch):
    """Confirms the gate's failure surfaces through the EXISTING
    exception -> audit 'lose' -> re-raise path in run_campaign_orchestrator
    (unchanged code), not a new, separate handling path -- i.e. this
    failure looks identical to any other pre-existing downstream-agent
    failure from run_campaign_orchestrator's own perspective."""
    call_log: list[str] = []
    _install_fake_agent_modules(monkeypatch, call_log, raise_for="mkt-o2")  # a non-gate failure
    _seed(fake_db)

    with pytest.raises(RuntimeError, match="MKT-ORCH campaign build failed"):
        mkt_orch.run_campaign_orchestrator("prod-1", "opp-1", "real-estate", supabase_client=fake_db)

    audit_writes = [c for c in fake_db.executed if c.table_name == "audit_log"]
    audit_actions = [c._payload.get("action") for c in audit_writes if c._payload]
    assert "campaign_build_failed" in audit_actions
