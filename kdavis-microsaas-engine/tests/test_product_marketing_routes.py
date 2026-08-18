"""
api/routers/product_marketing.py — POST /products/{id}/run-research and
/run-campaign. Dashboard-session-JWT auth (request.state.role/tenant_id,
same shape as tests/test_factory_routes.py's trigger_build), NOT the
MARKETING_API_KEY pattern api/routers/marketing.py's automation routes use.
"""
import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.product_marketing as product_marketing_router

client = TestClient(app)


def _auth_header(role: str = "admin", sub: str = "operator-1") -> dict:
    token = jwt.encode(
        {"sub": sub, "app_metadata": {"role": role}, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# ── auth ──────────────────────────────────────────────────────────────

def test_run_research_requires_admin_role():
    resp = client.post("/products/prod-1/run-research", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_run_research_requires_auth_at_all():
    resp = client.post("/products/prod-1/run-research")
    assert resp.status_code == 401


def test_run_campaign_requires_admin_role():
    resp = client.post("/products/prod-1/run-campaign", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_run_campaign_requires_auth_at_all():
    resp = client.post("/products/prod-1/run-campaign")
    assert resp.status_code == 401


# ── run-research ──────────────────────────────────────────────────────

def test_run_research_404_when_product_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    resp = client.post("/products/missing-prod/run-research", headers=_auth_header())
    assert resp.status_code == 404


def test_run_research_derives_keywords_and_queues(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [
        {"vertical": "Real Estate / Property Management", "solution_concept": "Automated showing follow-up", "pain_point": "agents lose deals"}
    ]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(
        product_marketing_router, "_run_research",
        lambda product_id, niche_keywords: captured.update(product_id=product_id, niche_keywords=niche_keywords),
    )

    resp = client.post("/products/prod-1/run-research", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "queued", "product_id": "prod-1",
        "niche_keywords": ["Real Estate / Property Management", "Automated showing follow-up"],
    }
    assert captured["product_id"] == "prod-1"
    assert captured["niche_keywords"] == ["Real Estate / Property Management", "Automated showing follow-up"]


# ── run-campaign ──────────────────────────────────────────────────────

def test_run_campaign_404_when_product_not_found(fake_db, monkeypatch):
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)
    resp = client.post("/products/missing-prod/run-campaign", headers=_auth_header())
    assert resp.status_code == 404


def test_run_campaign_409_when_no_research_report_yet(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    fake_db.responses["mse_research_reports"] = []
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)

    resp = client.post("/products/prod-1/run-campaign", headers=_auth_header())

    assert resp.status_code == 409
    assert "research" in resp.json()["detail"].lower()


def test_run_campaign_queues_with_research_opp_id_equal_to_product_id(fake_db, monkeypatch):
    fake_db.responses["opportunity_pipeline"] = [{"vertical": "Real Estate / Property Management"}]
    fake_db.responses["mse_research_reports"] = [{"id": "report-1"}]
    monkeypatch.setattr(product_marketing_router, "get_supabase", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(
        product_marketing_router, "_run_campaign",
        lambda product_id, vertical: captured.update(product_id=product_id, vertical=vertical),
    )

    resp = client.post("/products/prod-1/run-campaign", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "product_id": "prod-1"}
    assert captured == {"product_id": "prod-1", "vertical": "Real Estate / Property Management"}
