from datetime import datetime, timedelta, timezone

from agents.marketing.mkt_o5_sequence_sender import (
    run_send_touch_1,
    run_send_touch_2,
    run_sequence_sender,
)
from core.email_compliance import generate_unsubscribe_token


class FakeResendEmails:
    def __init__(self):
        self.sent = []

    def send(self, params):
        self.sent.append(params)
        return {"id": "email_test"}


class FakeResend:
    def __init__(self):
        self.Emails = FakeResendEmails()


def _seed_sequence(fake_db, status="approved_hitl", touch_1_sent_at=None, lead_id="lead-1"):
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-1",
        "lead_id": lead_id,
        "product_id": "prod-1",
        "campaign_build_id": "camp-1",
        "touch_1": "You're leaving $4k/mo on the table with manual invoicing.",
        "touch_2": "Following up — still leaving that $4k/mo on the table?",
        "status": status,
        "touch_1_sent_at": touch_1_sent_at,
    }]


def test_touch_1_sends_email_and_updates_status(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 1, "failed": [], "skipped": []}
    assert len(fake_resend.Emails.sent) == 1
    assert fake_resend.Emails.sent[0]["to"] == "lead@example.com"
    assert fake_resend.Emails.sent[0]["subject"] == "Quick question, Jamie"
    assert "4k/mo" in fake_resend.Emails.sent[0]["text"]

    updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c._payload and "status" in c._payload]
    assert updates[0]._payload["status"] == "touch_1_sent"
    assert updates[0]._payload["touch_1_sent_at"] is not None

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "win"


def test_touch_1_appends_compliance_footer_with_address_and_unsubscribe_link(fake_db):
    """CAN-SPAM requires a physical mailing address and a working opt-out
    in every commercial email -- regression guard for the 2026-08-12 gap
    where neither existed anywhere in the send path."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    sent_body = fake_resend.Emails.sent[0]["text"]
    assert "[test address]" in sent_body  # COMPLIANCE_MAILING_ADDRESS, conftest.py
    assert "/marketing/unsubscribe?email=lead%40example.com" in sent_body
    token = generate_unsubscribe_token("lead@example.com")
    assert f"token={token}" in sent_body


def test_touch_1_skips_suppressed_lead_without_sending(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_db.responses["mse_email_suppressions"] = [{"id": "sup-1", "email": "lead@example.com"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": [], "skipped": ["seq-1"]}
    assert len(fake_resend.Emails.sent) == 0

    updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c._payload and "status" in c._payload]
    assert updates[0]._payload["status"] == "suppressed"

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"
    assert audits[0]._payload["metadata"]["skipped"] == "suppressed"


def test_touch_1_stops_sending_once_daily_cap_reached(fake_db, monkeypatch):
    fake_db.responses["mse_dm_sequences"] = [
        {"id": "seq-1", "lead_id": "lead-1", "product_id": "prod-1", "campaign_build_id": "camp-1",
         "touch_1": "msg 1", "touch_2": "follow 1", "status": "approved_hitl", "touch_1_sent_at": None},
        {"id": "seq-2", "lead_id": "lead-2", "product_id": "prod-1", "campaign_build_id": "camp-1",
         "touch_1": "msg 2", "touch_2": "follow 2", "status": "approved_hitl", "touch_1_sent_at": None},
    ]
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    monkeypatch.setenv("MARKETING_DAILY_SEND_CAP", "0")
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": [], "skipped": ["seq-1", "seq-2"]}
    assert len(fake_resend.Emails.sent) == 0
    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert all(a._payload["metadata"].get("skipped") == "daily_cap" for a in audits)


def test_touch_1_skips_lead_with_no_email_but_continues(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": None, "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": ["seq-1"], "skipped": []}
    assert len(fake_resend.Emails.sent) == 0

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"
    assert "No email on file" in audits[0]._payload["metadata"]["error"]


def test_touch_1_skips_lead_that_no_longer_exists(fake_db):
    """Regression test for a real bug found 2026-07-17: maybe_single().execute()
    returns bare None (not a Response with .data=None) when zero rows match
    — a deleted/missing lead_id crashed this with an unhandled AttributeError
    instead of being caught by the existing "no email on file" handling."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = []  # lead_id doesn't exist at all
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": ["seq-1"], "skipped": []}
    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"


def test_touch_2_query_filters_on_status_and_cadence_cutoff(fake_db):
    """The FakeQuery doesn't actually filter its canned response based on
    .eq()/.lte() calls (that's Postgres's job against the real DB) — this
    asserts run_send_touch_2 issues the right filters, i.e. it only ever
    asks for touch_1_sent rows old enough to be due, rather than checking
    end-to-end filtering behavior a fake DB can't meaningfully provide."""
    old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _seed_sequence(fake_db, status="touch_1_sent", touch_1_sent_at=old_enough)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]

    run_send_touch_2(supabase_client=fake_db, resend_client=FakeResend())

    select_query = fake_db.executed[0]
    assert ("status", "touch_1_sent") in select_query._filters
    lte_calls = [c for c in select_query.calls if c[0] == "lte"]
    assert len(lte_calls) == 1
    assert lte_calls[0][1] == "touch_1_sent_at"


def test_touch_2_sends_and_completes_sequence(fake_db):
    old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _seed_sequence(fake_db, status="touch_1_sent", touch_1_sent_at=old_enough)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_send_touch_2(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 1, "failed": [], "skipped": []}
    assert fake_resend.Emails.sent[0]["subject"] == "Following up, Jamie"

    updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c._payload and "status" in c._payload]
    assert updates[0]._payload["status"] == "sequence_complete"
    assert updates[0]._payload["touch_2_sent_at"] is not None


def test_touch_2_skips_lead_who_unsubscribed_after_touch_1(fake_db):
    """The whole point of checking suppression at both touches, not once:
    someone can unsubscribe in the 3-day gap between touch_1 and touch_2."""
    old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _seed_sequence(fake_db, status="touch_1_sent", touch_1_sent_at=old_enough)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_db.responses["mse_email_suppressions"] = [{"id": "sup-1", "email": "lead@example.com"}]
    fake_resend = FakeResend()

    result = run_send_touch_2(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": [], "skipped": ["seq-1"]}
    assert len(fake_resend.Emails.sent) == 0


def test_run_sequence_sender_runs_both_stages(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_sequence_sender(supabase_client=fake_db, resend_client=fake_resend)

    assert "touch_1" in result and "touch_2" in result
    assert result["touch_1"]["sent"] == 1
