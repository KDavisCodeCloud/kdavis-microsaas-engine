"""
tests/test_delivery_events.py

Bounce / complaint handling and the Resend webhook signature (2026-11-07).
"""

import base64
import hashlib
import hmac
import json

import pytest

from core.delivery_events import (
    IN_FLIGHT_STATUSES,
    SignatureError,
    apply_delivery_failure,
    classify_event,
    verify_svix_signature,
)

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def _sign(body: bytes, msg_id="msg_1", ts="1700000000"):
    key = base64.b64decode(SECRET.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return msg_id, ts, f"v1,{sig}"


class TestSignature:
    def test_valid_signature_passes(self):
        body = b'{"type":"email.bounced"}'
        mid, ts, sig = _sign(body)
        verify_svix_signature(SECRET, mid, ts, sig, body, now=1700000010)

    def test_tampered_body_fails(self):
        mid, ts, sig = _sign(b'{"a":1}')
        with pytest.raises(SignatureError):
            verify_svix_signature(SECRET, mid, ts, sig, b'{"a":2}', now=1700000010)

    def test_replay_outside_tolerance_fails(self):
        body = b"{}"
        mid, ts, sig = _sign(body)
        with pytest.raises(SignatureError, match="tolerance"):
            verify_svix_signature(SECRET, mid, ts, sig, body, now=1700009999)

    def test_missing_headers_fail(self):
        with pytest.raises(SignatureError):
            verify_svix_signature(SECRET, "", "", "", b"{}")


@pytest.mark.parametrize("event,expect", [
    ({"type": "email.bounced", "data": {"bounce": {"type": "Permanent"}}}, "bounced"),
    ({"type": "email.bounced", "data": {}}, "bounced"),
    ({"type": "email.bounced", "data": {"bounce": {"type": "Transient"}}}, "soft_bounce"),
    ({"type": "email.complained", "data": {}}, "complained"),
    ({"type": "email.delivered", "data": {}}, None),
])
def test_classify(event, expect):
    assert classify_event(event) == expect


def _event(kind="email.bounced", to="CTO@Acme.com"):
    return {"type": kind, "data": {"to": [to], "email_id": "re_1", "bounce": {"type": "Permanent"}}}


def test_hard_bounce_suppresses_marks_lead_and_stops_sequences(fake_db):
    fake_db.responses["mse_leads"] = [{"id": "lead-1", "product_id": "p1"}]
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-1", "status": "touch_1_sent"}]
    out = apply_delivery_failure(fake_db, _event())
    assert out["outcome"] == "bounced" and out["leads"] == 1 and out["sequences_stopped"] == 1

    payloads = [(c.table_name, c._payload) for c in fake_db.executed if c._payload]
    assert ("mse_email_suppressions", {"email": "cto@acme.com", "reason": "bounced"}) in payloads
    assert ("mse_leads", {"status": "bounced", "email_status": "bounced"}) in payloads
    assert ("mse_dm_sequences", {"status": "bounced"}) in payloads
    assert any(t == "audit_log" and p["action"] == "delivery_bounced" for t, p in payloads)


def test_complaint_unsubscribes(fake_db):
    fake_db.responses["mse_leads"] = [{"id": "lead-1", "product_id": "p1"}]
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-1", "status": "touch_1_sent"}]
    apply_delivery_failure(fake_db, _event("email.complained"))
    payloads = [(c.table_name, c._payload) for c in fake_db.executed if c._payload]
    assert ("mse_email_suppressions", {"email": "cto@acme.com", "reason": "complained"}) in payloads
    assert ("mse_leads", {"status": "unsubscribed"}) in payloads
    assert ("mse_dm_sequences", {"status": "suppressed"}) in payloads


def test_soft_bounce_is_recorded_only(fake_db):
    ev = _event()
    ev["data"]["bounce"]["type"] = "Transient"
    apply_delivery_failure(fake_db, ev)
    tables = {c.table_name for c in fake_db.executed if c._payload}
    assert tables == {"audit_log"}


def test_every_sending_status_is_stoppable():
    for s in ("approved_hitl", "touch_1_sent", "touch_2_sent", "touch_3_sending"):
        assert s in IN_FLIGHT_STATUSES


def test_webhook_refuses_without_a_secret(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    r = TestClient(app).post("/webhooks/resend", content=b"{}")
    assert r.status_code == 503


def test_webhook_rejects_a_bad_signature(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", SECRET)
    r = TestClient(app).post("/webhooks/resend", content=b"{}",
                             headers={"svix-id": "m", "svix-timestamp": "1", "svix-signature": "v1,x"})
    assert r.status_code == 401
