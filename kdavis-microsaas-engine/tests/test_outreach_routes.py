import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.outreach as outreach_router

client = TestClient(app)


def _auth_header(role: str = "admin") -> dict:
    token = jwt.encode(
        {
            "sub": "operator-1",
            "app_metadata": {"role": role},
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_approve_sequence_requires_admin_role(monkeypatch, fake_db):
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/outreach/dm-sequences/seq-1/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(role="marketing"),
    )
    assert resp.status_code == 403


def test_approve_sequence_requires_auth_at_all():
    resp = client.post("/outreach/dm-sequences/seq-1/approve", json={})
    assert resp.status_code == 401


def test_approve_sequence_updates_pending_row(monkeypatch, fake_db):
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-1", "status": "approved_hitl"}]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/outreach/dm-sequences/seq-1/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "approved_hitl", "id": "seq-1"}

    seq_updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences"]
    assert seq_updates[0]._payload["status"] == "approved_hitl"
    assert ("status", "pending_hitl") in seq_updates[0]._filters

    events = [c for c in fake_db.executed if c.table_name == "agent_events"]
    assert events[0]._payload["verdict"] == "pass"


def test_approve_sequence_404_when_not_pending(monkeypatch, fake_db):
    fake_db.responses["mse_dm_sequences"] = []
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/outreach/dm-sequences/seq-1/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )
    assert resp.status_code == 404


def test_reject_sequence_updates_pending_row(monkeypatch, fake_db):
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-1", "status": "rejected_hitl"}]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/outreach/dm-sequences/seq-1/reject",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    events = [c for c in fake_db.executed if c.table_name == "agent_events"]
    assert events[0]._payload["verdict"] == "flagged"


def test_mark_lead_contacted_requires_admin_role():
    resp = client.post(
        "/outreach/leads/lead-1/mark-contacted",
        headers=_auth_header(role="rnd"),
    )
    assert resp.status_code == 403


def test_mark_lead_contacted_updates_lead(monkeypatch, fake_db):
    fake_db.responses["mse_apollo_leads"] = [{"id": "lead-1", "linkedin_contacted_at": "2026-07-16T00:00:00Z"}]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post("/outreach/leads/lead-1/mark-contacted", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json() == {"status": "contacted", "id": "lead-1"}

    lead_updates = [c for c in fake_db.executed if c.table_name == "mse_apollo_leads"]
    assert lead_updates[0]._payload["linkedin_contacted_at"] is not None
    assert ("linkedin_contacted_at", "null") in lead_updates[0]._filters


def test_mark_lead_contacted_404_when_already_contacted(monkeypatch, fake_db):
    fake_db.responses["mse_apollo_leads"] = []
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post("/outreach/leads/lead-1/mark-contacted", headers=_auth_header())
    assert resp.status_code == 404
