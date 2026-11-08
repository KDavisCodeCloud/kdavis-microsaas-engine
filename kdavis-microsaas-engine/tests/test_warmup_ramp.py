"""
tests/test_warmup_ramp.py

Warmup ramp (Kelvin, 2026-11-07): 10-15/day in weeks 1-2, 20-25 in weeks
3-4, 30/day steady. The env cap stays a hard ceiling. A test send to
KELVIN_EMAIL never starts the clock.
"""

from datetime import date

import pytest

import core.email_compliance as ec


class _Q:
    def __init__(self, rows): self.rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": self.rows})()


class _DB:
    def __init__(self, rows): self.rows = rows
    def table(self, name): return _Q(self.rows)


def _win(day: str, test=False):
    return {"created_at": f"{day}T15:00:00+00:00", "metadata": {"test_send": test}}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("MARKETING_WARMUP_START", raising=False)
    monkeypatch.setenv("MARKETING_DAILY_SEND_CAP", "200")


@pytest.mark.parametrize("today,expect", [
    ("2026-11-10", 10), ("2026-11-16", 10),   # week 1
    ("2026-11-17", 15), ("2026-11-23", 15),   # week 2
    ("2026-11-24", 20), ("2026-11-30", 20),   # week 3
    ("2026-12-01", 25), ("2026-12-07", 25),   # week 4
    ("2026-12-08", 30), ("2027-03-01", 30),   # steady
])
def test_ramp_by_day(today, expect):
    db = _DB([_win("2026-11-10")])
    assert ec.warmup_daily_cap(db, today=date.fromisoformat(today)) == expect


def test_bands_match_the_brief():
    caps = [c for _, c in ec.WARMUP_RAMP]
    assert all(10 <= c <= 15 for c in caps[:2])
    assert all(20 <= c <= 25 for c in caps[2:])
    assert ec.WARMUP_STEADY_CAP == 30


def test_no_real_send_yet_is_day_one():
    assert ec.warmup_daily_cap(_DB([]), today=date(2027, 1, 1)) == 10


def test_a_test_send_does_not_start_the_clock():
    db = _DB([_win("2026-11-08", test=True)])
    assert ec.warmup_start(db) is None
    db = _DB([_win("2026-11-08", test=True), _win("2026-11-20")])
    assert ec.warmup_start(db) == date(2026, 11, 20)


def test_env_cap_is_a_ceiling(monkeypatch):
    monkeypatch.setenv("MARKETING_DAILY_SEND_CAP", "12")
    db = _DB([_win("2026-01-01")])  # long past warmup -> ramp says 30
    assert ec.effective_daily_cap(db) == 12


def test_ramp_is_below_a_high_env_cap():
    assert ec.effective_daily_cap(_DB([])) == 10


def test_sender_uses_the_effective_cap():
    import inspect
    import agents.marketing.mkt_o5_sequence_sender as o5
    src = inspect.getsource(o5)
    assert "cap = effective_daily_cap(db)" in src
    assert "cap = daily_send_cap()" not in src


def test_win_metadata_records_grade_resend_id_and_test_flag(monkeypatch):
    import agents.marketing.mkt_o5_sequence_sender as o5
    monkeypatch.setenv("KELVIN_EMAIL", "kelvin@example.com")
    m = o5._win_metadata({"id": "s1"}, {"email": "Kelvin@Example.com", "email_grade": "valid"}, 1, "re_123")
    assert m == {"sequence_id": "s1", "touch": 1, "resend_id": "re_123",
                 "email_grade": "valid", "test_send": True}
    m = o5._win_metadata({"id": "s2"}, {"email": "cto@acme.com", "email_grade": "valid"}, 2, None)
    assert m["test_send"] is False
