import time

import jwt
from fastapi.testclient import TestClient

from api.main import app
import api.routers.factory as factory_router

client = TestClient(app)


def _auth_header(role: str = "admin", sub: str = "operator-1") -> dict:
    token = jwt.encode(
        {"sub": sub, "app_metadata": {"role": role}, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "placeholder-jwt-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_build_requires_admin_role():
    resp = client.post(
        "/factory/build/opp-1", json={"stripe_api_key": "sk_test_x"}, headers=_auth_header(role="marketing"),
    )
    assert resp.status_code == 403


def test_build_requires_auth_at_all():
    resp = client.post("/factory/build/opp-1", json={"stripe_api_key": "sk_test_x"})
    assert resp.status_code == 401


def test_build_queues_with_triggered_by_from_jwt_sub(monkeypatch):
    captured = {}
    monkeypatch.setattr(factory_router, "_run_build", lambda opp_id, key, triggered_by, org_id: captured.update(
        opp_id=opp_id, key=key, triggered_by=triggered_by, org_id=org_id,
    ))

    resp = client.post(
        "/factory/build/opp-1",
        json={"stripe_api_key": "sk_test_x"},
        headers=_auth_header(sub="operator-42"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "opportunity_id": "opp-1"}
    assert captured["opp_id"] == "opp-1"
    assert captured["key"] == "sk_test_x"
    assert captured["triggered_by"] == "operator-42"
    assert captured["org_id"] is None


def test_build_passes_through_explicit_org_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(factory_router, "_run_build", lambda opp_id, key, triggered_by, org_id: captured.update(org_id=org_id))

    resp = client.post(
        "/factory/build/opp-1",
        json={"stripe_api_key": "sk_test_x", "org_id": "custom-org"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    assert captured["org_id"] == "custom-org"


def test_generate_brief_requires_admin_role():
    resp = client.post("/factory/generate-brief/opp-1", headers=_auth_header(role="marketing"))
    assert resp.status_code == 403


def test_generate_brief_requires_auth_at_all():
    resp = client.post("/factory/generate-brief/opp-1")
    assert resp.status_code == 401


def test_generate_brief_queues_with_triggered_by_from_jwt_sub(monkeypatch):
    captured = {}
    monkeypatch.setattr(factory_router, "_run_generate_brief", lambda opp_id, triggered_by: captured.update(
        opp_id=opp_id, triggered_by=triggered_by,
    ))

    resp = client.post("/factory/generate-brief/opp-1", headers=_auth_header(sub="operator-42"))

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "opportunity_id": "opp-1"}
    assert captured["opp_id"] == "opp-1"
    assert captured["triggered_by"] == "operator-42"
