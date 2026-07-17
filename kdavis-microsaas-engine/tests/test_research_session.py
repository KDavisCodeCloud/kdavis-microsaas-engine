"""Coverage for the research-session completion signal fix (2026-07-16):
node_summarize computed the exact session_summary shape the frontend
needed but never persisted it anywhere, so /research/session/{id} could
never report real completion. Fixed by persisting it to usage_events and
having the GET route surface it."""
import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.research as research_router
import agents.orchestrator.agent as orchestrator_agent

client = TestClient(app)


def _auth_header() -> dict:
    token = jwt.encode(
        {"sub": "operator-1", "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_session_summary_null_while_running(monkeypatch, fake_db):
    fake_db.responses["opportunity_pipeline"] = [{"id": "opp-1", "vertical": "v", "solution_concept": "s", "conservative_mrr_potential": 4000, "build_confidence_score": 80, "status": "validated"}]
    fake_db.responses["usage_events"] = []  # no completion event yet
    monkeypatch.setattr(research_router, "get_supabase", lambda: fake_db)

    resp = client.get("/research/session/sess-1", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_summary"] is None
    assert len(body["opportunities"]) == 1


def test_session_summary_present_once_complete(monkeypatch, fake_db):
    summary = {"session_id": "sess-1", "verticals_scanned": 6, "ready_to_build": 1, "validated_pending_review": 2, "watch_list": 0, "rejected": 3, "top_opportunity": "X", "recommended_first_build": "X"}
    fake_db.responses["opportunity_pipeline"] = []
    fake_db.responses["usage_events"] = [{"metadata": summary}]
    monkeypatch.setattr(research_router, "get_supabase", lambda: fake_db)

    resp = client.get("/research/session/sess-1", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json()["session_summary"] == summary


def test_node_summarize_persists_completion_event(fake_db):
    state = {
        "session_id": "sess-1",
        "verticals": ["Healthcare / Medical Front Desk"],
        "raw_findings": [{"solution_concept": "X", "build_confidence_score": 90}],
        "aggregated_results": [{"solution_concept": "X", "status": "READY_TO_BUILD", "vertical": "Healthcare / Medical Front Desk"}],
        "session_summary": {},
        "status": "writing",
    }

    result = _run_with_fake_db(fake_db, state)

    events = [c for c in fake_db.executed if c.table_name == "usage_events"]
    assert len(events) == 1
    assert events[0]._payload["event_type"] == "research_session_complete"
    assert events[0]._payload["metadata"]["session_id"] == "sess-1"
    assert events[0]._payload["metadata"]["ready_to_build"] == 1
    assert result["session_summary"]["recommended_first_build"] == "X"


def _run_with_fake_db(fake_db, state):
    orig = orchestrator_agent.get_supabase
    orchestrator_agent.get_supabase = lambda: fake_db
    try:
        return orchestrator_agent.node_summarize(state)
    finally:
        orchestrator_agent.get_supabase = orig
