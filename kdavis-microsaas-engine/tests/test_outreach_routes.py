import time
from datetime import datetime, timedelta, timezone

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

    # approve now does a SELECT (to read lead_source) before the UPDATE --
    # narrow to the update call specifically rather than assuming index 0.
    seq_updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"]
    assert seq_updates[0]._payload["status"] == "approved_hitl"
    assert ("status", "pending_hitl") in seq_updates[0]._filters

    events = [c for c in fake_db.executed if c.table_name == "agent_events"]
    assert events[0]._payload["verdict"] == "pass"


# ── Finding 3 (2026-09-02 HITL audit): footer preview endpoint ──────────

def test_preview_email_sequence_includes_real_compliance_footer(monkeypatch, fake_db):
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-1", "lead_id": "lead-1", "lead_finder_lead_id": None, "lead_source": "apollo",
        "touch_1": "You're leaving $4k/mo on the table.", "touch_2": "Following up.",
    }]
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.get("/outreach/dm-sequences/seq-1/preview", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body["has_footer"] is True
    assert "[test address]" in body["touch_1"]
    assert "unsubscribe?email=lead%40example.com" in body["touch_1"]
    assert "You're leaving $4k/mo" in body["touch_1"]  # the real copy is still there, not replaced
    assert "[test address]" in body["touch_2"]


def test_preview_linkedin_sequence_has_no_footer(monkeypatch, fake_db):
    """LinkedIn sequences never reach MKT-O5, so a footer here would
    promise something that will never actually happen."""
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-2", "lead_source": "linkedin_manual",
        "touch_1": "Saw your post about scaling ops.", "touch_2": "Following up.",
    }]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.get("/outreach/dm-sequences/seq-2/preview", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()
    assert body["has_footer"] is False
    assert body["touch_1"] == "Saw your post about scaling ops."
    assert "[test address]" not in body["touch_1"]


def test_preview_requires_admin_role():
    resp = client.get("/outreach/dm-sequences/seq-1/preview", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_preview_404_for_unknown_sequence(monkeypatch, fake_db):
    fake_db.responses["mse_dm_sequences"] = []
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.get("/outreach/dm-sequences/does-not-exist/preview", headers=_auth_header())
    assert resp.status_code == 404


def test_approve_sequence_sets_seven_day_expiry(monkeypatch, fake_db):
    """Finding 2 (2026-09-02 HITL audit): an approval with no expiry could
    sit indefinitely and fire whenever the hourly sender next ran. Every
    approved_hitl approval must get a real expiry ~7 days out."""
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-1", "status": "approved_hitl"}]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    before = datetime.now(timezone.utc)
    resp = client.post(
        "/outreach/dm-sequences/seq-1/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )
    after = datetime.now(timezone.utc)

    assert resp.status_code == 200
    seq_updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"]
    expires_at = datetime.fromisoformat(seq_updates[0]._payload["hitl_approved_expires_at"])
    assert before + timedelta(days=7) <= expires_at <= after + timedelta(days=7)


def test_approve_linkedin_sequence_does_not_set_expiry(monkeypatch, fake_db):
    """approved_manual (LinkedIn) is never polled by MKT-O5 at all, so an
    expiry on it would never be checked by anything -- must stay unset
    rather than implying a freshness guarantee that doesn't exist."""
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-2", "status": "pending_hitl", "lead_source": "linkedin_engager"}]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/outreach/dm-sequences/seq-2/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    seq_updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"]
    assert "hitl_approved_expires_at" not in seq_updates[0]._payload


def test_approve_linkedin_sequence_uses_approved_manual_status(monkeypatch, fake_db):
    # LinkedIn-sourced sequences must never reach 'approved_hitl' --
    # mkt_o5_sequence_sender.py polls that exact status and would try to
    # email a lead with no email on file. 'approved_manual' is the
    # LinkedIn-only terminal state instead.
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-2", "status": "pending_hitl", "lead_source": "linkedin_engager"}]
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/outreach/dm-sequences/seq-2/approve",
        json={"resolved_by": "kelvin"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "approved_manual", "id": "seq-2"}

    seq_updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"]
    assert seq_updates[0]._payload["status"] == "approved_manual"


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
