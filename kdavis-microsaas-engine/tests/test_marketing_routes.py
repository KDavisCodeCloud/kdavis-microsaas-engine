"""Regression coverage for the PUBLIC_PATHS gap found 2026-07-16: apollo-list,
dm-sequences, and seo-content were documented as "exempted from
tenant_context_middleware" in marketing.py's own module docstring but were
never actually added to PUBLIC_PATHS, so tenant_context_middleware's
verify_jwt() rejected every request before the route's own
MARKETING_API_KEY check ever ran. Fixed in api/middleware/tenant_context.py.
"""
from fastapi.testclient import TestClient

from api.main import app
from api.middleware.tenant_context import tenant_context_middleware
import api.routers.marketing as marketing_router

client = TestClient(app)

MARKETING_ROUTES = [
    ("/marketing/research", {"product_id": "p1", "niche_keywords": []}),
    ("/marketing/campaign", {"product_id": "p1", "research_opp_id": "r1"}),
    ("/marketing/apollo-list", {"product_id": "p1", "campaign_build_id": "c1", "research_report": {}}),
    ("/marketing/dm-sequences", {"product_id": "p1", "campaign_build_id": "c1", "research_report": {}}),
    ("/marketing/seo-content", {"product_id": "p1", "research_report": {}}),
    ("/marketing/send-sequences", {}),
]


def test_all_marketing_routes_are_public_paths_source():
    """Direct check on the middleware's own PUBLIC_PATHS set — the fast,
    exact regression guard against this specific gap reappearing."""
    import inspect
    source = inspect.getsource(tenant_context_middleware)
    for path, _ in MARKETING_ROUTES:
        assert path in source, f"{path} missing from tenant_context_middleware's PUBLIC_PATHS"


def test_marketing_routes_accept_api_key_not_jwt(monkeypatch):
    """None of these should ever require a Supabase JWT — a correct
    MARKETING_API_KEY bearer token must be enough to reach the route."""
    monkeypatch.setattr(marketing_router, "_run_research", lambda *a, **k: None)
    monkeypatch.setattr(marketing_router, "_run_campaign", lambda *a, **k: None)
    monkeypatch.setattr(marketing_router, "_run_apollo_list", lambda *a, **k: None)
    monkeypatch.setattr(marketing_router, "_run_dm_sequences", lambda *a, **k: None)
    monkeypatch.setattr(marketing_router, "_run_seo_content", lambda *a, **k: None)
    monkeypatch.setattr(marketing_router, "_run_send_sequences", lambda *a, **k: None)

    for path, body in MARKETING_ROUTES:
        resp = client.post(
            path, json=body, headers={"Authorization": "Bearer test-marketing-api-key"}
        )
        assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text}"
        assert resp.json()["status"] == "queued"


def test_marketing_routes_reject_wrong_api_key(monkeypatch):
    monkeypatch.setattr(marketing_router, "_run_apollo_list", lambda *a, **k: None)

    resp = client.post(
        "/marketing/apollo-list",
        json={"product_id": "p1", "campaign_build_id": "c1", "research_report": {}},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


def test_marketing_routes_reject_missing_auth_header():
    resp = client.post(
        "/marketing/apollo-list",
        json={"product_id": "p1", "campaign_build_id": "c1", "research_report": {}},
    )
    assert resp.status_code == 401
