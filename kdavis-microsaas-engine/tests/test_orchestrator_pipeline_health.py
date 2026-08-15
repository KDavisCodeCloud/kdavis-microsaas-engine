"""
agents/orchestrator/agent.py's pipeline-health wiring (2026-08-15, Kelvin's
rule) -- the new check_pipeline_health graph node, and Dispatch-side
recalibration-text injection in _run_one_vertical's stub research path.
pytest-asyncio runs in auto mode (pytest.ini) so async defs need no marker.
"""
from unittest.mock import MagicMock

import agents.orchestrator.agent as orch
from tests.conftest import FakeSupabase


def _base_state(**overrides) -> dict:
    state = {
        "session_id": "session-1", "verticals": ["Healthcare / Medical Front Desk"],
        "raw_findings": [], "aggregated_results": [], "session_summary": {}, "status": "complete",
    }
    state.update(overrides)
    return state


def test_check_pipeline_health_node_logs_a_usage_event_when_recalibration_fires(monkeypatch):
    fake_db = FakeSupabase(responses={"usage_events": [{"id": "evt-1"}]})
    monkeypatch.setattr(orch, "get_supabase", lambda: fake_db)
    monkeypatch.setattr(
        "agents.aggregator.pipeline_health.check_and_recalibrate",
        lambda: {"dominant_category": "MRR_FLOOR", "target": "verdict", "build_rate_pct": 0.0},
    )

    result = orch.node_check_pipeline_health(_base_state())

    assert result == _base_state()  # state passes through unchanged
    inserts = [c for c in fake_db.executed if c.table_name == "usage_events" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["event_type"] == "pipeline_health_recalibration"
    assert inserts[0]._payload["metadata"]["dominant_category"] == "MRR_FLOOR"


def test_check_pipeline_health_node_no_event_when_healthy(monkeypatch):
    fake_db = FakeSupabase(responses={"usage_events": []})
    monkeypatch.setattr(orch, "get_supabase", lambda: fake_db)
    monkeypatch.setattr("agents.aggregator.pipeline_health.check_and_recalibrate", lambda: None)

    orch.node_check_pipeline_health(_base_state())

    inserts = [c for c in fake_db.executed if c.table_name == "usage_events" and c.calls[0][0] == "insert"]
    assert inserts == []


def test_check_pipeline_health_node_never_raises_on_failure(monkeypatch):
    fake_db = FakeSupabase(responses={"usage_events": [{"id": "evt-1"}]})
    monkeypatch.setattr(orch, "get_supabase", lambda: fake_db)

    def raise_error():
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr("agents.aggregator.pipeline_health.check_and_recalibrate", raise_error)

    result = orch.node_check_pipeline_health(_base_state())  # must not raise
    assert result == _base_state()

    inserts = [c for c in fake_db.executed if c.table_name == "usage_events" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["event_type"] == "pipeline_health_check_failed"
    assert "DB unreachable" in inserts[0]._payload["metadata"]["error"]


async def test_dispatch_recalibration_text_is_prepended_to_verticals_system_prompt(monkeypatch):
    monkeypatch.setattr(
        "agents.aggregator.pipeline_health.get_active_recalibration_text",
        lambda target: "DISPATCH RECALIBRATION MARKER",
    )
    captured = {}

    def fake_analyze(system, user, max_tokens=None, model=None):
        captured["system"] = system
        return "[]"

    monkeypatch.setattr(orch, "analyze_with_web_search", fake_analyze)

    await orch._run_one_vertical("Healthcare / Medical Front Desk")

    assert "DISPATCH RECALIBRATION MARKER" in captured["system"]


async def test_no_dispatch_recalibration_leaves_system_prompt_as_base(monkeypatch):
    monkeypatch.setattr("agents.aggregator.pipeline_health.get_active_recalibration_text", lambda target: "")
    captured = {}

    def fake_analyze(system, user, max_tokens=None, model=None):
        captured["system"] = system
        return "[]"

    monkeypatch.setattr(orch, "analyze_with_web_search", fake_analyze)

    await orch._run_one_vertical("Healthcare / Medical Front Desk")

    assert captured["system"] == orch._SYSTEM_PROMPT


async def test_dispatch_recalibration_lookup_failure_does_not_block_research(monkeypatch):
    def raise_error(target):
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr("agents.aggregator.pipeline_health.get_active_recalibration_text", raise_error)
    captured = {}

    def fake_analyze(system, user, max_tokens=None, model=None):
        captured["system"] = system
        return "[]"

    monkeypatch.setattr(orch, "analyze_with_web_search", fake_analyze)

    result = await orch._run_one_vertical("Healthcare / Medical Front Desk")  # must not raise
    assert result == {"vertical": "Healthcare / Medical Front Desk", "findings": []}
    assert captured["system"] == orch._SYSTEM_PROMPT
