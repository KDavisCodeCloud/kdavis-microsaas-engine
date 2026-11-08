"""
tests/test_touch_3_and_stops.py

Follow-ups on day 3 and day 7 (Kelvin, 2026-11-07). Before this, touch_3 was
drafted and approved but never sent: touch_2 marked the sequence complete.
Every follow-up stops on a reply, a bounce or an unsubscribe.
"""

from datetime import datetime, timedelta, timezone

import agents.marketing.mkt_o5_sequence_sender as o5
from tests.test_mkt_o5_sequence_sender import FakeResend, _seed_active_stage, _update_to


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed(fake_db, status, *, touch_3="Last note from me -- worth a look?", lead=None, **cols):
    row = {"id": "seq-1", "lead_id": "lead-1", "product_id": "prod-1",
           "touch_1": "t1", "touch_2": "t2", "touch_3": touch_3, "status": status}
    row.update(cols)
    fake_db.responses["mse_dm_sequences"] = [row]
    fake_db.responses["mse_apollo_leads"] = [lead or {"email": "cto@acme.com", "first_name": "Ana",
                                                      "email_grade": "valid"}]
    _seed_active_stage(fake_db)


def test_cadence_is_day_3_then_day_7():
    assert o5.TOUCH_2_DELAY == timedelta(days=3)
    assert o5.TOUCH_2_DELAY + o5.TOUCH_3_DELAY == timedelta(days=7)


def test_touch_2_hands_off_to_touch_3_when_there_is_one(fake_db):
    _seed(fake_db, "touch_1_sent", touch_1_sent_at=_ago(4))
    assert o5.run_send_touch_2(supabase_client=fake_db, resend_client=FakeResend())["sent"] == 1
    assert _update_to(fake_db, "touch_2_sent")._payload["touch_2_sent_at"]


def test_touch_2_completes_a_two_touch_sequence(fake_db):
    _seed(fake_db, "touch_1_sent", touch_3=None, touch_1_sent_at=_ago(4))
    o5.run_send_touch_2(supabase_client=fake_db, resend_client=FakeResend())
    _update_to(fake_db, "sequence_complete")


def test_touch_3_sends_and_completes(fake_db):
    _seed(fake_db, "touch_2_sent", touch_2_sent_at=_ago(5))
    resend = FakeResend()
    assert o5.run_send_touch_3(supabase_client=fake_db, resend_client=resend)["sent"] == 1
    assert "Last note" in resend.Emails.sent[0]["text"]
    assert _update_to(fake_db, "sequence_complete")._payload["touch_3_sent_at"]


def test_touch_3_only_asks_for_due_touch_2_sent_rows(fake_db):
    _seed(fake_db, "touch_2_sent", touch_2_sent_at=_ago(5))
    o5.run_send_touch_3(supabase_client=fake_db, resend_client=FakeResend())
    q = fake_db.executed[0]
    assert ("status", "touch_2_sent") in q._filters
    assert [c[1] for c in q.calls if c[0] == "lte"] == ["touch_2_sent_at"]


def test_replied_sequences_are_never_polled():
    # A reply sets status='replied'; the follow-ups only read touch_1_sent and
    # touch_2_sent, so a replied sequence is out of reach by construction.
    assert {o5._TOUCH_2.from_status, o5._TOUCH_3.from_status}.isdisjoint({"replied"})


def test_touch_3_stops_on_unsubscribe(fake_db):
    _seed(fake_db, "touch_2_sent", touch_2_sent_at=_ago(5))
    fake_db.responses["mse_email_suppressions"] = [{"id": "s", "email": "cto@acme.com"}]
    resend = FakeResend()
    assert o5.run_send_touch_3(supabase_client=fake_db, resend_client=resend)["skipped"] == ["seq-1"]
    assert resend.Emails.sent == []
    _update_to(fake_db, "suppressed")


def test_touch_3_stops_on_bounce(fake_db):
    _seed(fake_db, "touch_2_sent", touch_2_sent_at=_ago(5),
          lead={"email": "cto@acme.com", "first_name": "Ana", "email_grade": "valid", "status": "bounced"})
    resend = FakeResend()
    assert o5.run_send_touch_3(supabase_client=fake_db, resend_client=resend)["skipped"] == ["seq-1"]
    assert resend.Emails.sent == []
    _update_to(fake_db, "bounced")


def test_sender_runs_all_three_stages(fake_db):
    fake_db.responses["mse_dm_sequences"] = []
    _seed_active_stage(fake_db)
    out = o5.run_sequence_sender(supabase_client=fake_db, resend_client=FakeResend())
    assert set(out) == {"touch_1", "touch_2", "touch_3"}
