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


def test_review_rejects_stores_decision_comment_and_audit(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = [{"id": "opp-1", "human_review_status": "rejected"}]

    resp = client.post(
        "/pipeline/opp-1/review",
        json={"decision": "rejected", "comment": "Overlaps with an existing tool we already tried"},
        headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 200

    updates = [c for c in fake_db.executed if c.table_name == "opportunity_pipeline" and c.calls[0][0] == "update"]
    assert updates[-1]._payload["human_review_status"] == "rejected"
    assert updates[-1]._payload["human_review_comment"] == "Overlaps with an existing tool we already tried"
    assert updates[-1]._payload["human_reviewed_by"] == "kelvin"

    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "lose"
    assert audits[-1]._payload["metadata"]["decision"] == "rejected"


def test_review_approves_records_win_outcome(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = [{"id": "opp-1", "human_review_status": "approved"}]

    resp = client.post(
        "/pipeline/opp-1/review", json={"decision": "approved"}, headers=_auth_header(sub="kelvin"),
    )

    assert resp.status_code == 200
    audits = [c for c in fake_db.executed if c.table_name == "audit_log"]
    assert audits[-1]._payload["outcome"] == "win"


def test_review_404s_when_opportunity_not_found(monkeypatch, fake_db):
    monkeypatch.setattr(pipeline_router, "get_supabase", lambda: fake_db)
    fake_db.responses["opportunity_pipeline"] = []

    resp = client.post(
        "/pipeline/opp-missing/review", json={"decision": "rejected"}, headers=_auth_header(),
    )

    assert resp.status_code == 404
