import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.pipeline as pipeline_router

client = TestClient(app)


def _auth_header(role: str = "admin", sub: str = "kelvin") -> dict:
    token = jwt.encode(
        {"sub": sub, "app_metadata": {"role": role}, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_review_requires_admin_role():
    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "rejected"}, headers=_auth_header(role="marketing"),
    )
    assert resp.status_code == 403


def test_review_requires_auth_at_all():
    resp = client.post("/pipeline/opp-1/review", json={"decision": "rejected"})
    assert resp.status_code == 401


def test_reject_archives_full_row_then_deletes_it(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    original_row = {
        "id": "opp-1", "solution_concept": "Some Product",
        "verdict_v2_output": {"verdict": "CONDITIONAL", "final_mrr_floor": 4200},
    }
    fake_db.responses["opportunity_pipeline"] = [original_row]

    resp = client.post(
        "/pipeline/opp-1/review",
        json={"decision": "rejected", "comment": "Overlaps with an existing tool we already tried"},
        headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "opportunity_id": "opp-1"}

    # Full row (including the agent's research reasoning) archived before delete.
    archive_inserts = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline_rejections" and c.calls[0][0] == "insert"]
    assert len(archive_inserts) == 1
    assert archive_inserts[0]._payload["original_opportunity"] == original_row
    assert archive_inserts[0]._payload["rejected_by"] == "kelvin"
    assert archive_inserts[0]._payload["rejection_comment"] == "Overlaps with an existing tool we already tried"

    # Then actually deleted from opportunity_pipeline, not just flagged.
    deletes = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "delete"]
    assert len(deletes) == 1

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert audits[-1]._payload["metadata"]["decision"] == "rejected"


def test_reject_404s_when_opportunity_not_found(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = []

    resp = client.post(
        "/pipeline/opp-missing/review", json={"decision": "rejected"}, headers=_auth_header(),
    )

    assert resp.status_code == 404
    # Nothing should be archived or deleted for a row that was never found.
    assert not [c for c in fake_db.executed if c.table_name == "opportunity_pipeline_rejections"]


def test_approve_pushes_into_the_build_queue_and_records_win(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = [{"id": "opp-1", "status": "READY_TO_BUILD", "human_review_status": "approved"}]

    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "approved", "comment": "This is the one"}, headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 200

    updates = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "update"]
    assert updates[-1]._payload["status"] == "READY_TO_BUILD"
    assert updates[-1]._payload["human_review_status"] == "approved"
    assert updates[-1]._payload["human_review_comment"] == "This is the one"
    assert updates[-1]._payload["human_reviewed_by"] == "kelvin"

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "win"

    # Approve must never touch the delete/archive path.
    assert not [c for c in fake_db.executed if c.table_name == "opportunity_pipeline_rejections"]
    assert not [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "delete"]


def test_approve_404s_when_opportunity_not_found(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = []

    resp = client.post(
        "/pipeline/opp-missing/review", json={"decision": "approved"}, headers=_auth_header(),
    )

    assert resp.status_code == 404
