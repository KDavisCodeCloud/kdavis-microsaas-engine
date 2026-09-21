"""
api/routers/marketing_internal.py — the CEO Decoded dashboard's email
approval queue proxy target. Dashboard-session-JWT auth, same shape as
tests/test_product_marketing_routes.py.
"""
import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.marketing_internal as marketing_internal_router

client = TestClient(app)


def _auth_header(role: str = "admin", sub: str = "operator-1") -> dict:
    token = jwt.encode(
        {"sub": sub, "app_metadata": {"role": role}, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _seed(fake_db, sequences=None, products=None, positioning=None):
    fake_db.responses["mse_email_sequences"] = sequences or []
    fake_db.responses["mse_products"] = products or []
    fake_db.responses["mse_positioning"] = positioning or []


# ── auth ──────────────────────────────────────────────────────────────

def test_list_templates_requires_admin_role():
    resp = client.get("/marketing/internal/email-templates", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_list_templates_requires_auth_at_all():
    resp = client.get("/marketing/internal/email-templates")
    assert resp.status_code == 401


# ── list ──────────────────────────────────────────────────────────────

def test_list_templates_serializes_real_schema(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(
        fake_db,
        sequences=[{
            "id": "seq-1",
            "product_id": "prod-a",
            "campaign_build_id": "camp-1",
            "status": "pending_hitl",
            "origin": "generated",
            "source_script": None,
            "grounding_sources": [],
            "emails": [{"subject": "Welcome", "preheader": "Get started"}],
            "hitl_approved_by": None,
            "hitl_approved_at": None,
            "created_at": "2026-09-21T00:00:00Z",
        }],
        products=[{"id": "prod-a", "slug": "tradesdesk", "name": "TradesDesk"}],
    )

    resp = client.get("/marketing/internal/email-templates", headers=_auth_header())

    assert resp.status_code == 200
    body = resp.json()["templates"]
    assert len(body) == 1
    row = body[0]
    assert row["template_key"] == "seq-1"
    assert row["subject"] == "Welcome"
    assert row["preheader"] == "Get started"
    assert row["status"] == "pending_hitl"
    assert row["origin"] == "generated"
    assert row["product"] == {"id": "prod-a", "slug": "tradesdesk", "name": "TradesDesk", "resolved": True}


def test_list_templates_marks_unresolved_product(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(
        fake_db,
        sequences=[{
            "id": "seq-1", "product_id": "orphan-id", "campaign_build_id": "camp-1",
            "status": "pending_hitl", "emails": [], "created_at": "2026-09-21T00:00:00Z",
        }],
        products=[],
    )

    resp = client.get("/marketing/internal/email-templates", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json()["templates"][0]["product"]["resolved"] is False


def test_list_templates_rejects_invalid_status_filter(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    resp = client.get("/marketing/internal/email-templates?status=bogus", headers=_auth_header())
    assert resp.status_code == 400


# ── get one ───────────────────────────────────────────────────────────

def test_get_template_404_when_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(fake_db, sequences=[])
    resp = client.get("/marketing/internal/email-templates/missing", headers=_auth_header())
    assert resp.status_code == 404


def test_get_template_includes_full_steps(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(
        fake_db,
        sequences=[{
            "id": "seq-1", "product_id": "prod-a", "campaign_build_id": "camp-1",
            "status": "pending_hitl",
            "emails": [{"subject": "Welcome"}, {"subject": "Day 2"}],
            "created_at": "2026-09-21T00:00:00Z",
        }],
    )
    resp = client.get("/marketing/internal/email-templates/seq-1", headers=_auth_header())
    assert resp.status_code == 200
    assert len(resp.json()["steps"]) == 2


# ── approve ───────────────────────────────────────────────────────────

def test_approve_requires_admin_role():
    resp = client.post("/marketing/internal/email-templates/seq-1/approve", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_approve_404_when_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(fake_db, sequences=[])
    resp = client.post("/marketing/internal/email-templates/seq-1/approve", headers=_auth_header())
    assert resp.status_code == 404


def test_approve_409_when_already_activated(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(fake_db, sequences=[{"id": "seq-1", "status": "activated"}])
    resp = client.post("/marketing/internal/email-templates/seq-1/approve", headers=_auth_header())
    assert resp.status_code == 409


def test_approve_happy_path(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(fake_db, sequences=[{"id": "seq-1", "status": "pending_hitl"}])
    fake_db.responses["mse_email_sequences"] = [{"id": "seq-1", "status": "pending_hitl"}]

    resp = client.post("/marketing/internal/email-templates/seq-1/approve", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json() == {"status": "activated", "template_key": "seq-1"}
    updates = [c for c in fake_db.executed if c.table_name == "mse_email_sequences" and c._payload and c._payload.get("status") == "activated"]
    assert updates, "expected an update() setting status=activated"
    assert updates[0]._payload["hitl_approved_by"] == "operator-1"


# ── retire ────────────────────────────────────────────────────────────

def test_retire_404_when_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    fake_db.responses["mse_email_sequences"] = []
    resp = client.post("/marketing/internal/email-templates/seq-1/retire", headers=_auth_header())
    assert resp.status_code == 404


def test_retire_happy_path(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    fake_db.responses["mse_email_sequences"] = [{"id": "seq-1", "status": "activated"}]
    resp = client.post("/marketing/internal/email-templates/seq-1/retire", headers=_auth_header())
    assert resp.status_code == 200
    assert resp.json() == {"status": "retired", "template_key": "seq-1"}


# ── campaign-status / email-metrics ─────────────────────────────────────

def test_campaign_status_shape(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(
        fake_db,
        products=[{"id": "prod-a", "slug": "tradesdesk", "name": "TradesDesk", "status": "active"}],
        positioning=[{"product_id": "prod-a", "status": "approved", "version": 2}],
        sequences=[{"product_id": "unrelated-id", "status": "pending_hitl"}],
    )
    resp = client.get("/marketing/internal/campaign-status", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["products"] == [{
        "product_id": "prod-a", "slug": "tradesdesk", "name": "TradesDesk",
        "product_status": "active", "positioning_status": "approved", "positioning_version": 2,
    }]
    assert body["email_sequences_total"] == 1
    assert body["email_sequences_unattributable"] == 1


def test_email_metrics_returns_explicit_nulls_not_fabricated_zeros(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_internal_router, "get_supabase", lambda: fake_db)
    _seed(fake_db, sequences=[{"status": "pending_hitl"}, {"status": "pending_hitl"}, {"status": "activated"}])
    resp = client.get("/marketing/internal/email-metrics", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["sequences_by_status"] == {"pending_hitl": 2, "activated": 1}
    assert body["sends"] is None
    assert body["clicks"] is None
