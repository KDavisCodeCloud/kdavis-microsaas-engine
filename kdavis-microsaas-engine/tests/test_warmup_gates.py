"""
tests/test_warmup_gates.py

Warmup send gates (Kelvin's decision 3b/3c, 2026-10-01).

3b: catch-all ("risky") addresses may now send, capped at 25% of the daily
    cap. A catch-all accepts every RCPT TO, so a 250 proved only that the
    domain answers -- the cap bounds the bounce exposure rather than refusing
    the volume outright.
3c: a bounce rate over 3% in a rolling 7-day window pauses EVERY send. Past
    that point mailbox providers start filing everything as spam, and the
    damage outlives the batch that caused it.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

import core.email_compliance as ec


class FakeQ:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class FakeDb:
    def __init__(self, audit=None, leads=None, raise_on=None):
        self.audit = audit or []
        self.leads = leads or []
        self.raise_on = raise_on

    def table(self, name):
        if self.raise_on == name:
            raise RuntimeError("database unavailable")
        return FakeQ(self.audit if name == "audit_log" else self.leads)


def _sends(n, grade=None):
    return [{"id": f"a{i}", "metadata": ({"email_grade": grade} if grade else {})} for i in range(n)]


class TestRiskySubCap:
    def test_cap_is_a_quarter_of_the_daily_cap(self, monkeypatch):
        monkeypatch.setenv("MARKETING_DAILY_SEND_CAP", "12")
        assert ec.risky_send_cap() == 3

    def test_cap_is_never_zero(self, monkeypatch):
        """A tiny daily cap must still allow one risky send rather than
        silently disabling the whole band."""
        monkeypatch.setenv("MARKETING_DAILY_SEND_CAP", "2")
        assert ec.risky_send_cap() >= 1

    def test_share_matches_the_spec(self):
        assert ec.RISKY_SHARE_OF_DAILY_CAP == 0.25

    def test_counts_only_risky_sends(self):
        db = FakeDb(audit=_sends(3, "risky") + _sends(5, "valid") + _sends(2))
        assert ec.risky_sends_today(db) == 3


class TestBounceRate:
    def test_rate_is_none_below_the_minimum_sample(self):
        """1 bounce out of 3 sends is 33% and tells you nothing. Pausing on it
        would stop the programme before it ever warmed up."""
        db = FakeDb(audit=_sends(5), leads=[{"id": "l1"}])
        rate, bounced, sent = ec.bounce_rate_7d(db)
        assert rate is None
        assert (bounced, sent) == (1, 5)

    def test_rate_computed_once_there_is_enough_volume(self):
        db = FakeDb(audit=_sends(100), leads=[{"id": f"l{i}"} for i in range(2)])
        rate, bounced, sent = ec.bounce_rate_7d(db)
        assert rate == pytest.approx(0.02)
        assert (bounced, sent) == (2, 100)

    def test_minimum_sample_matches_the_spec(self):
        assert ec.BOUNCE_MIN_SENDS_FOR_RATE == 20
        assert ec.BOUNCE_WINDOW_DAYS == 7
        assert ec.BOUNCE_RATE_PAUSE_THRESHOLD == 0.03


class TestSendsPaused:
    def test_pauses_above_three_percent(self):
        db = FakeDb(audit=_sends(100), leads=[{"id": f"l{i}"} for i in range(4)])
        paused, reason = ec.sends_paused(db)
        assert paused is True
        assert "4.0%" in reason or "4%" in reason
        assert "3%" in reason

    def test_does_not_pause_at_exactly_three_percent(self):
        """The rule is "exceeds 3%", so 3.0% itself keeps sending."""
        db = FakeDb(audit=_sends(100), leads=[{"id": f"l{i}"} for i in range(3)])
        paused, _reason = ec.sends_paused(db)
        assert paused is False

    def test_does_not_pause_below_the_threshold(self):
        db = FakeDb(audit=_sends(100), leads=[{"id": "l1"}])
        paused, reason = ec.sends_paused(db)
        assert paused is False
        assert "1.0%" in reason

    def test_does_not_pause_on_an_unmeaningful_sample(self):
        db = FakeDb(audit=_sends(5), leads=[{"id": f"l{i}"} for i in range(3)])
        paused, reason = ec.sends_paused(db)
        assert paused is False
        assert "not yet meaningful" in reason

    def test_fails_open_on_a_database_error(self):
        """A transient read failure must not silently halt the outbound
        programme -- a halt nobody is told about is worse than the risk it
        avoids. The reason string says what happened."""
        db = FakeDb(raise_on="audit_log")
        paused, reason = ec.sends_paused(db)
        assert paused is False
        assert "unavailable" in reason


class TestSenderIntegration:
    def test_risky_sends_while_budget_remains_and_blocks_after(self):
        import agents.marketing.mkt_o5_sequence_sender as o5

        lead = {"email": "a@b.com", "lead_route": "outbound_email", "email_grade": "risky"}
        assert o5._email_send_block_reason(lead, risky_budget_left=2) is None
        assert o5._email_send_block_reason(lead, risky_budget_left=0) == "risky_daily_cap_reached"

    @pytest.mark.parametrize("grade", ["invalid", "unknown"])
    def test_invalid_and_unknown_never_send_regardless_of_budget(self, grade):
        import agents.marketing.mkt_o5_sequence_sender as o5

        lead = {"email": "a@b.com", "lead_route": "outbound_email", "email_grade": grade}
        assert o5._email_send_block_reason(lead, risky_budget_left=99) == f"email_grade={grade}"

    def test_valid_sends_without_consuming_the_risky_budget(self):
        import agents.marketing.mkt_o5_sequence_sender as o5

        lead = {"email": "a@b.com", "lead_route": "outbound_email", "email_grade": "valid"}
        assert o5._email_send_block_reason(lead, risky_budget_left=0) is None

    def test_both_send_loops_check_the_pause(self):
        """A pause that only guards touch_1 would let follow-ups keep going
        into a damaged reputation."""
        import inspect

        import agents.marketing.mkt_o5_sequence_sender as o5

        for fn in (o5.run_send_touch_1, o5.run_send_touch_2):
            src = inspect.getsource(fn)
            assert "sends_paused(db)" in src, f"{fn.__name__} does not check the bounce pause"
            assert "risky_budget_left" in src, f"{fn.__name__} does not apply the risky sub-cap"
