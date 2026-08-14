"""
api/routers/linkedin_intake.py — same PUBLIC_PATHS/MARKETING_API_KEY
regression shape as tests/test_marketing_routes.py (that file exists
because routes documented as public were once never actually added to
PUBLIC_PATHS, so verify_jwt() rejected them before their own API-key
check ever ran — this repeats that same check for the /marketing/linkedin/*
prefix).
"""
import io

from fastapi.testclient import TestClient

from api.main import app
from api.middleware.tenant_context import tenant_context_middleware
import api.routers.linkedin_intake as linkedin_intake_router

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-marketing-api-key"}


def test_linkedin_prefix_is_covered_by_a_public_path_check_source():
    import inspect
    source = inspect.getsource(tenant_context_middleware)
    assert "/marketing/linkedin/" in source


def test_intake_rejects_missing_auth_header():
    resp = client.post("/marketing/linkedin/intake", json={"leads": [], "source": "linkedin_manual"})
    assert resp.status_code == 401


def test_intake_rejects_wrong_api_key():
    resp = client.post(
        "/marketing/linkedin/intake",
        json={"leads": [], "source": "linkedin_manual"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


def test_intake_json_body_path(monkeypatch):
    monkeypatch.setattr(
        linkedin_intake_router, "run_li_intake",
        lambda leads, source, product_id=None: {"added": len(leads), "duplicates_skipped": 0},
    )

    resp = client.post(
        "/marketing/linkedin/intake",
        json={"leads": [{"linkedin_url": "https://linkedin.com/in/jane"}], "source": "linkedin_manual", "product_id": "prod-1"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"added": 1, "duplicates_skipped": 0}


def test_intake_rejects_invalid_source(monkeypatch):
    monkeypatch.setattr(linkedin_intake_router, "run_li_intake", lambda **k: {"added": 0, "duplicates_skipped": 0})

    resp = client.post(
        "/marketing/linkedin/intake",
        json={"leads": [], "source": "apollo"},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_intake_csv_upload_path(monkeypatch):
    captured = {}

    def fake_run_li_intake(leads, source, product_id=None):
        captured["leads"] = leads
        captured["source"] = source
        captured["product_id"] = product_id
        return {"added": len(leads), "duplicates_skipped": 0}

    monkeypatch.setattr(linkedin_intake_router, "run_li_intake", fake_run_li_intake)

    csv_content = "first_name,last_name,linkedin_url\nJane,Doe,https://linkedin.com/in/janedoe\n"
    resp = client.post(
        "/marketing/linkedin/intake",
        files={"file": ("leads.csv", io.BytesIO(csv_content.encode()), "text/csv")},
        data={"source": "linkedin_manual", "product_id": "prod-1"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"added": 1, "duplicates_skipped": 0}
    assert captured["source"] == "linkedin_manual"
    assert captured["product_id"] == "prod-1"
    assert captured["leads"][0]["linkedin_url"] == "https://linkedin.com/in/janedoe"


def test_list_leads_requires_auth():
    resp = client.get("/marketing/linkedin/leads")
    assert resp.status_code == 401


def test_list_leads_filters_by_status_and_source(fake_db, monkeypatch):
    fake_db.responses["mse_linkedin_leads"] = [{"id": "lead-1", "status": "pending_dm", "source": "linkedin_engager"}]
    monkeypatch.setattr(linkedin_intake_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/linkedin/leads?status=pending_dm&source=linkedin_engager", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"leads": [{"id": "lead-1", "status": "pending_dm", "source": "linkedin_engager"}]}


def test_mark_sent_requires_auth():
    resp = client.post("/marketing/linkedin/leads/lead-1/mark-sent")
    assert resp.status_code == 401


def test_mark_sent_updates_lead(fake_db, monkeypatch):
    fake_db.responses["mse_linkedin_leads"] = [{"id": "lead-1", "status": "contacted"}]
    monkeypatch.setattr(linkedin_intake_router, "get_supabase", lambda: fake_db)

    resp = client.post("/marketing/linkedin/leads/lead-1/mark-sent", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"status": "contacted", "id": "lead-1"}
    updates = [c for c in fake_db.executed if c.table_name == "mse_linkedin_leads" and c.calls[0][0] == "update"]
    assert updates[0]._payload["status"] == "contacted"


def test_mark_sent_404_when_already_contacted(fake_db, monkeypatch):
    fake_db.responses["mse_linkedin_leads"] = []
    monkeypatch.setattr(linkedin_intake_router, "get_supabase", lambda: fake_db)

    resp = client.post("/marketing/linkedin/leads/lead-1/mark-sent", headers=AUTH)
    assert resp.status_code == 404
