import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from api.main import app
import api.routers.stripe as stripe_router

client = TestClient(app)

WEBHOOK_SECRET = "whsec_test_placeholder"  # matches conftest.py env default


def _sign(payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{body.decode()}"
    signature = hmac.new(
        WEBHOOK_SECRET.encode(), signed_payload.encode(), hashlib.sha256
    ).hexdigest()
    header = f"t={timestamp},v1={signature}"
    return body, header


def _event(event_type: str, data_object: dict) -> dict:
    return {
        "id": "evt_test",
        "object": "event",
        "type": event_type,
        "data": {"object": data_object},
    }


def test_rejects_invalid_signature():
    payload = _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_1"})
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/stripe",
        content=body,
        headers={"stripe-signature": "t=1,v1=deadbeef"},
    )
    assert resp.status_code == 400


def test_rejects_missing_signature_header():
    payload = _event("customer.subscription.created", {"id": "sub_1", "customer": "cus_1"})
    body = json.dumps(payload).encode()
    resp = client.post("/webhooks/stripe", content=body)
    assert resp.status_code == 400


def test_accepts_valid_signature_and_updates_tenant(monkeypatch, fake_db):
    fake_db.responses["tenants"] = [{"id": "user-123"}]

    monkeypatch.setattr(stripe_router, "get_supabase", lambda: fake_db)

    class FakeCustomer:
        @staticmethod
        def retrieve(customer_id):
            return {"metadata": {"supabase_user_id": "user-123"}, "name": "Test User", "email": "t@example.com"}

    monkeypatch.setattr(stripe_router.stripe, "Customer", FakeCustomer)

    subscription = {
        "id": "sub_1",
        "customer": "cus_1",
        "status": "active",
        "items": {"data": [{"price": {"metadata": {"tier": "growth"}}}]},
    }
    payload = _event("customer.subscription.created", subscription)
    body, header = _sign(payload)

    resp = client.post(
        "/webhooks/stripe",
        content=body,
        headers={"stripe-signature": header},
    )

    assert resp.status_code == 200
    assert resp.json() == {"received": True}
    upserts = [c for c in fake_db.executed if c.table_name == "tenants"]
    assert len(upserts) == 1
    assert upserts[0]._payload["tier"] == "growth"
    assert upserts[0]._payload["id"] == "user-123"


def test_subscription_deleted_marks_tenant_churned(monkeypatch, fake_db):
    fake_db.responses["tenants"] = [{"id": "user-123"}]

    monkeypatch.setattr(stripe_router, "get_supabase", lambda: fake_db)

    subscription = {"id": "sub_1", "customer": "cus_1"}
    payload = _event("customer.subscription.deleted", subscription)
    body, header = _sign(payload)

    resp = client.post(
        "/webhooks/stripe",
        content=body,
        headers={"stripe-signature": header},
    )

    assert resp.status_code == 200
    tenant_updates = [c for c in fake_db.executed if c.table_name == "tenants"]
    assert any(c._payload.get("status") == "churned" for c in tenant_updates)
