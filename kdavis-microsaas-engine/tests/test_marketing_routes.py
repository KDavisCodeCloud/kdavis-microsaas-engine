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
from core.email_compliance import generate_unsubscribe_token
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


# ── GET /marketing/unsubscribe — public, no auth of any kind (a real human
# clicks this from their own inbox) ──────────────────────────────────────

def test_unsubscribe_is_a_public_path_source():
    import inspect
    source = inspect.getsource(tenant_context_middleware)
    assert "/marketing/unsubscribe" in source


def test_unsubscribe_with_valid_token_suppresses_and_returns_200(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_router, "get_supabase", lambda: fake_db)
    token = generate_unsubscribe_token("lead@example.com")

    resp = client.get(f"/marketing/unsubscribe?email=lead@example.com&token={token}")

    assert resp.status_code == 200
    assert "unsubscribed" in resp.text.lower()
    upserts = [c for c in fake_db.executed if c.table_name == "mse_email_suppressions" and c.calls[0][0] == "upsert"]
    assert upserts[0]._payload == {"email": "lead@example.com", "reason": "unsubscribed"}


def test_unsubscribe_with_valid_token_requires_no_auth_header_at_all(fake_db, monkeypatch):
    """The whole point of this endpoint — a recipient with zero session of
    any kind must be able to use it."""
    monkeypatch.setattr(marketing_router, "get_supabase", lambda: fake_db)
    token = generate_unsubscribe_token("lead@example.com")

    resp = client.get(f"/marketing/unsubscribe?email=lead@example.com&token={token}")
    assert resp.status_code == 200


def test_unsubscribe_rejects_invalid_token_and_does_not_suppress(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_router, "get_supabase", lambda: fake_db)

    resp = client.get("/marketing/unsubscribe?email=lead@example.com&token=forged-token")

    assert resp.status_code == 400
    upserts = [c for c in fake_db.executed if c.table_name == "mse_email_suppressions"]
    assert upserts == []


def test_unsubscribe_escapes_email_in_html_response(fake_db, monkeypatch):
    """Regression guard: the email in the response body was interpolated
    into raw HTML unescaped when this was first written -- a crafted
    email/token pair could inject markup into the confirmation page."""
    monkeypatch.setattr(marketing_router, "get_supabase", lambda: fake_db)
    malicious_email = "<script>alert(1)</script>@example.com"
    token = generate_unsubscribe_token(malicious_email)

    resp = client.get(f"/marketing/unsubscribe?email={malicious_email}&token={token}")

    assert "<script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


# ── POST /marketing/unsubscribe — RFC 8058 one-click target, same
# public/no-auth shape as the GET landing page above ────────────────────

def test_one_click_unsubscribe_is_a_public_path_source():
    import inspect
    source = inspect.getsource(tenant_context_middleware)
    # Same PUBLIC_PATHS entry covers both verbs (path-only match) -- this
    # just documents that the POST route relies on that, not a second entry.
    assert "/marketing/unsubscribe" in source


def test_one_click_unsubscribe_with_valid_token_suppresses(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_router, "get_supabase", lambda: fake_db)
    token = generate_unsubscribe_token("lead@example.com")

    resp = client.post(f"/marketing/unsubscribe?email=lead@example.com&token={token}")

    assert resp.status_code == 200
    assert resp.json() == {"status": "unsubscribed"}
    upserts = [c for c in fake_db.executed if c.table_name == "mse_email_suppressions" and c.calls[0][0] == "upsert"]
    assert upserts[0]._payload == {"email": "lead@example.com", "reason": "unsubscribed"}


def test_one_click_unsubscribe_rejects_invalid_token(fake_db, monkeypatch):
    monkeypatch.setattr(marketing_router, "get_supabase", lambda: fake_db)

    resp = client.post("/marketing/unsubscribe?email=lead@example.com&token=forged-token")

    assert resp.status_code == 400
    assert [c for c in fake_db.executed if c.table_name == "mse_email_suppressions"] == []
