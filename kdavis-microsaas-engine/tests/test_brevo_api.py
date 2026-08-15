"""
api/routers/brevo.py — POST /marketing/brevo/lists, GET
/marketing/brevo/lists/{product_id}, POST /marketing/brevo/enroll. Same
MARKETING_API_KEY auth shape as tests/test_leads_api.py; see that file's
module docstring for why the /marketing/brevo prefix check exists in
tenant_context.py at all.
"""
from fastapi.testclient import TestClient

import agents.marketing.mkt_o3_email_sequence_loader as mkt_o3
import api.routers.brevo as brevo_router
from api.main import app
from api.middleware.tenant_context import tenant_context_middleware

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-marketing-api-key"}


def test_brevo_prefix_is_covered_by_a_public_path_check_source():
    import inspect
    source = inspect.getsource(tenant_context_middleware)
    assert "/marketing/brevo" in source


def test_upsert_list_requires_auth():
    resp = client.post("/marketing/brevo/lists", json={"product_id": "prod-1", "brevo_list_id": 7})
    assert resp.status_code == 401


def test_upsert_list_stores_mapping_correctly(fake_db, monkeypatch):
    fake_db.responses["mse_brevo_lists"] = [
        {"product_id": "prod-1", "brevo_list_id": 7, "list_name": "Showing Signal Trial Nurture"}
    ]
    monkeypatch.setattr(brevo_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/marketing/brevo/lists",
        json={"product_id": "prod-1", "brevo_list_id": 7, "list_name": "Showing Signal Trial Nurture"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"product_id": "prod-1", "brevo_list_id": 7, "list_name": "Showing Signal Trial Nurture"}

    upserts = [c for c in fake_db.executed if c.table_name == "mse_brevo_lists" and c.calls[0][0] == "upsert"]
    assert upserts[0]._payload == {"product_id": "prod-1", "brevo_list_id": 7, "list_name": "Showing Signal Trial Nurture"}
    assert upserts[0].calls[0][2] == "product_id"  # on_conflict


def test_upsert_list_500_when_write_fails(fake_db, monkeypatch):
    fake_db.responses["mse_brevo_lists"] = []
    monkeypatch.setattr(brevo_router, "get_supabase", lambda: fake_db)

    resp = client.post(
        "/marketing/brevo/lists", json={"product_id": "prod-1", "brevo_list_id": 7}, headers=AUTH,
    )
    assert resp.status_code == 500


def test_get_list_requires_auth():
    resp = client.get("/marketing/brevo/lists/prod-1")
    assert resp.status_code == 401


def test_get_list_returns_correct_list_id(fake_db, monkeypatch):
    fake_db.responses["mse_brevo_lists"] = [{"product_id": "prod-1", "brevo_list_id": 42, "list_name": "X"}]
    monkeypatch.setattr(brevo_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/brevo/lists/prod-1", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["brevo_list_id"] == 42


def test_get_list_404_when_not_registered(fake_db, monkeypatch):
    fake_db.responses["mse_brevo_lists"] = []
    monkeypatch.setattr(brevo_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/brevo/lists/prod-1", headers=AUTH)
    assert resp.status_code == 404


def test_enroll_requires_auth():
    resp = client.post("/marketing/brevo/enroll", json={"product_id": "prod-1", "email": "a@b.com"})
    assert resp.status_code == 401


def test_enroll_calls_enroll_trial_in_sequence_and_returns_result(monkeypatch):
    captured = {}

    def fake_enroll(**kwargs):
        captured.update(kwargs)
        return mkt_o3.EnrollmentResult(
            status="enrolled", sequence_drafted=True, enrolled=True, sequence_id="seq-1", brevo_list_id=7,
        )

    monkeypatch.setattr(brevo_router, "enroll_trial_in_sequence", fake_enroll)

    resp = client.post(
        "/marketing/brevo/enroll",
        json={
            "product_id": "prod-1", "email": "jane@example.com", "first_name": "Jane",
            "last_name": "Doe", "plan_tier": "solo", "trial_start": "2026-08-14",
        },
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "enrolled", "sequence_drafted": True, "enrolled": True,
        "sequence_id": "seq-1", "brevo_list_id": 7, "error": None,
    }
    assert captured["product_id"] == "prod-1"
    assert captured["email"] == "jane@example.com"
