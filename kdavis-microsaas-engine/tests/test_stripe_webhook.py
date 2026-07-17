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


def test_subscription_deleted_when_tenant_lookup_finds_nothing_does_not_crash(monkeypatch, fake_db):
    """Regression test for a real bug found 2026-07-17: maybe_single().execute()
    returns bare None (not a Response with .data=None) when zero rows match —
    a subscription with no matching tenant row crashed this whole handler
    with a 500 before the fix, instead of just skipping the suppression step."""
    fake_db.responses["tenants"] = []  # no matching tenant at all

    monkeypatch.setattr(stripe_router, "get_supabase", lambda: fake_db)

    subscription = {"id": "sub_unknown", "customer": "cus_unknown"}
    payload = _event("customer.subscription.deleted", subscription)
    body, header = _sign(payload)

    resp = client.post(
        "/webhooks/stripe",
        content=body,
        headers={"stripe-signature": header},
    )

    assert resp.status_code == 200


def test_payment_failed_when_tenant_not_found_does_not_crash(monkeypatch, fake_db):
    fake_db.responses["tenants"] = []

    monkeypatch.setattr(stripe_router, "get_supabase", lambda: fake_db)

    invoice = {"id": "in_1", "customer": "cus_unknown", "subscription": "sub_1", "amount_due": 2900}
    payload = _event("invoice.payment_failed", invoice)
    body, header = _sign(payload)

    resp = client.post(
        "/webhooks/stripe",
        content=body,
        headers={"stripe-signature": header},
    )

    assert resp.status_code == 200
    event_writes = [c for c in fake_db.executed if c.table_name == "usage_events"]
    assert len(event_writes) == 0  # nothing to log against — no tenant found


def test_payment_failed_logs_event_and_activates_sequence(monkeypatch, fake_db):
    fake_db.responses["tenants"] = [{"id": "user-123"}]
    fake_db.responses["retention_sequences"] = []  # no existing active prebilling sequence

    monkeypatch.setattr(stripe_router, "get_supabase", lambda: fake_db)

    invoice = {"id": "in_1", "customer": "cus_1", "subscription": "sub_1", "amount_due": 2900}
    payload = _event("invoice.payment_failed", invoice)
    body, header = _sign(payload)

    resp = client.post(
        "/webhooks/stripe",
        content=body,
        headers={"stripe-signature": header},
    )

    assert resp.status_code == 200
    event_writes = [c for c in fake_db.executed if c.table_name == "usage_events"]
    assert len(event_writes) == 1
    assert event_writes[0]._payload["event_type"] == "payment_failed"

    sequence_writes = [
        c for c in fake_db.executed
        if c.table_name == "retention_sequences" and c._payload and c._payload.get("sequence_type") == "prebilling"
    ]
    assert len(sequence_writes) == 1
