import pytest

from agents.factory.brief_dispatcher import dispatch_brief


def _seed_brief(fake_db, brief_id="brief-1", markdown="# Build Freight Audit Copilot"):
    fake_db.responses["mse_build_briefs"] = [{
        "id": brief_id,
        "product_slug": "freight-audit-copilot",
        "claude_code_brief": {"markdown": markdown},
    }]


def test_missing_brief_raises(fake_db):
    fake_db.responses["mse_build_briefs"] = []
    with pytest.raises(ValueError, match="No mse_build_briefs row"):
        dispatch_brief("missing-id", "operator-1", supabase_client=fake_db, llm_analyze=lambda s, u, **k: "unused")


def test_missing_code_brief_markdown_raises(fake_db):
    fake_db.responses["mse_build_briefs"] = [{"id": "brief-1", "product_slug": "x", "claude_code_brief": {}}]
    with pytest.raises(ValueError, match="no claude_code_brief.markdown"):
        dispatch_brief("brief-1", "operator-1", supabase_client=fake_db, llm_analyze=lambda s, u, **k: "unused")


def test_happy_path_writes_dispatched_result(fake_db):
    _seed_brief(fake_db)
    captured = {}

    def fake_llm(system, user, max_tokens=4096):
        captured["system"] = system
        captured["user"] = user
        return "Feasible. Start with the ingestion pipeline."

    result = dispatch_brief("brief-1", "operator-42", supabase_client=fake_db, llm_analyze=fake_llm)

    assert result["dispatch_status"] == "dispatched"
    assert result["result"]["response"] == "Feasible. Start with the ingestion pipeline."
    assert captured["user"] == "# Build Freight Audit Copilot"

    updates = [q for q in fake_db.executed if q.table_name == "mse_build_briefs" and ("update", q._payload) in q.calls]
    assert len(updates) == 1
    payload = updates[0]._payload
    assert payload["dispatch_status"] == "dispatched"
    assert payload["dispatched_by"] == "operator-42"
    assert payload["dispatch_result"]["response"] == "Feasible. Start with the ingestion pipeline."

    audit_inserts = [q for q in fake_db.executed if q.table_name == "audit_log"]
    assert len(audit_inserts) == 1
    assert audit_inserts[0]._payload["outcome"] == "win"
    assert audit_inserts[0]._payload["agent_id"] == "factory-brief-dispatcher"


def test_llm_failure_writes_failed_status_and_reraises(fake_db):
    _seed_brief(fake_db)

    def failing_llm(system, user, max_tokens=4096):
        raise RuntimeError("simulated API outage")

    with pytest.raises(RuntimeError, match="Brief dispatch failed"):
        dispatch_brief("brief-1", "operator-42", supabase_client=fake_db, llm_analyze=failing_llm)

    updates = [q for q in fake_db.executed if q.table_name == "mse_build_briefs" and ("update", q._payload) in q.calls]
    assert len(updates) == 1
    assert updates[0]._payload["dispatch_status"] == "failed"
    assert "simulated API outage" in updates[0]._payload["dispatch_result"]["error"]

    audit_inserts = [q for q in fake_db.executed if q.table_name == "audit_log"]
    assert len(audit_inserts) == 1
    assert audit_inserts[0]._payload["outcome"] == "lose"
