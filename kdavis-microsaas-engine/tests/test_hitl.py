import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.ceo as ceo_router

client = TestClient(app)


def _auth_header(role: str = "admin") -> dict:
    token = jwt.encode(
        {
            "sub": "operator-1",
            "role": role,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_approve_hitl_requires_auth():
    resp = client.post("/ceo/hitl/some-id/approve", json={})
    assert resp.status_code == 401


def test_approve_hitl_updates_pending_item(monkeypatch, fake_db):
    fake_db.responses["hitl_queue"] = [{"id": "item-1", "status": "approved"}]

    monkeypatch.setattr(ceo_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/ceo/hitl/item-1/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "approved", "id": "item-1"}

    hitl_calls = [c for c in fake_db.executed if c.table_name == "hitl_queue"]
    assert len(hitl_calls) == 1
    assert hitl_calls[0]._payload["status"] == "approved"
    assert hitl_calls[0]._payload["resolved_by"] == "kelvin"
    assert ("id", "item-1") in hitl_calls[0]._filters
    assert ("status", "pending") in hitl_calls[0]._filters

    events = [c for c in fake_db.executed if c.table_name == "agent_events"]
    assert len(events) == 1
    assert events[0]._payload["verdict"] == "pass"


def test_approve_hitl_404_when_already_resolved(monkeypatch, fake_db):
    fake_db.responses["hitl_queue"] = []  # .eq("status", "pending") matched nothing

    monkeypatch.setattr(ceo_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/ceo/hitl/item-1/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )

    assert resp.status_code == 404


def test_reject_hitl_updates_pending_item(monkeypatch, fake_db):
    fake_db.responses["hitl_queue"] = [{"id": "item-1", "status": "rejected"}]

    monkeypatch.setattr(ceo_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/ceo/hitl/item-1/reject",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "rejected", "id": "item-1"}

    events = [c for c in fake_db.executed if c.table_name == "agent_events"]
    assert events[0]._payload["verdict"] == "flagged"
