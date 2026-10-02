"""Brave monthly budget guard (Kelvin's decision 1e, 2026-10-02)."""

import pytest

from agents.marketing import brave_budget as bb


class FakeDB:
    """Only needs to satisfy _this_months_brave_query_count's query shape."""

    def __init__(self, used, month=None, raise_on_read=False):
        from datetime import date
        self.used = used
        self.month = month or date.today().isoformat()[:7]
        self.raise_on_read = raise_on_read

    def table(self, name):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        if self.raise_on_read:
            raise RuntimeError("postgrest unavailable")

        class R:
            pass
        r = R()
        r.data = [{"metadata": {"month": self.month, "count": self.used}}]
        return r


def test_threshold_is_eighty_percent_of_the_cap():
    assert bb.BUDGET_STOP_FRACTION == 0.80
    assert bb.stop_threshold(900) == 720


def test_a_run_well_under_budget_is_allowed():
    d = bb.check_budget(FakeDB(157), estimated_queries=35, cap=900)
    assert d.allowed is True
    assert (d.used, d.projected, d.threshold) == (157, 192, 720)
    assert d.remaining == 743
    assert d.remaining_to_threshold == 563


def test_a_run_that_would_cross_the_threshold_is_refused():
    """700 used + 35 = 735 > 720. The run is refused even though the hard
    900 cap has plenty left -- that is the point of a reserve."""
    d = bb.check_budget(FakeDB(700), estimated_queries=35, cap=900)
    assert d.allowed is False
    assert "past the 80% stop threshold" in d.reason
    assert d.remaining == 200, "the hard cap still had room; the reserve is what stopped it"


def test_a_run_landing_exactly_on_the_threshold_is_allowed():
    d = bb.check_budget(FakeDB(685), estimated_queries=35, cap=900)
    assert d.projected == 720
    assert d.allowed is True, "exactly at the threshold is not yet past it"


def test_already_past_the_threshold_is_refused_regardless_of_run_size():
    d = bb.check_budget(FakeDB(800), estimated_queries=1, cap=900)
    assert d.allowed is False
    assert "already at 800/900" in d.reason


def test_a_read_failure_fails_CLOSED():
    """Everywhere else a broken read fails open so a transient fault cannot
    halt the pipeline. Not here: 'we cannot tell what we have spent' is not a
    licence to spend more of a shared monthly budget."""
    d = bb.check_budget(FakeDB(0, raise_on_read=True), cap=900)
    assert d.allowed is False
    assert "could not read" in d.reason
    assert d.used == -1, "an unknown usage must not be reported as 0"


def test_usage_from_a_different_month_is_ignored():
    d = bb.check_budget(FakeDB(850, month="2020-01"), estimated_queries=35, cap=900)
    assert d.used == 0
    assert d.allowed is True


def test_as_dict_is_digest_ready():
    d = bb.check_budget(FakeDB(450), estimated_queries=35, cap=900)
    out = d.as_dict()
    for key in ("allowed", "used", "cap", "threshold", "projected",
                "remaining", "remaining_to_threshold", "percent_used", "reason"):
        assert key in out, f"{key} missing from the digest payload"
    assert out["percent_used"] == 50.0


def test_estimate_is_based_on_measured_runs_not_a_guess():
    """The 2026-10-01/02 runs spent 30 and 28 Brave queries; the estimate must
    be at least that, since a guard that underestimates is not a guard."""
    assert bb.ESTIMATED_QUERIES_PER_PRODUCT >= 30


def test_nine_product_sweep_would_be_refused_at_current_usage():
    """The concrete thing this guard exists to prevent: the old workflow swept
    all 9 configured products, ~315 queries in one Sunday."""
    sweep = 9 * bb.ESTIMATED_QUERIES_PER_PRODUCT
    d = bb.check_budget(FakeDB(500), estimated_queries=sweep, cap=900)
    assert d.allowed is False
