"""DIST Task 6 (2026-08-31) -- agents/dist/product_mrr.py.

STRIPE_SECRET_KEY is confirmed unset on the real Railway backend as of
2026-08-31 (same disclosed gap as every other credential-blocked DIST
piece tonight; confirmed via Railway's own variable listing, not
guessed). This test suite's tests/conftest.py sets a
STRIPE_SECRET_KEY=sk_test_placeholder default via os.environ.setdefault
for the whole suite (so other Stripe-route tests can import cleanly),
so the inert-without-key case below explicitly monkeypatch.delenv()s it
rather than relying on ambient state, which would test the wrong thing
here. The MRR-math tests use a fake Stripe module (this repo has no
live Stripe test-mode fixtures wired up), so they are mocked at the
Stripe SDK boundary only -- the real snapshot_product_mrr()
aggregation/upsert logic runs unmocked.
"""
from __future__ import annotations

import pytest

from agents.dist.product_mrr import StripeNotConfiguredError, snapshot_product_mrr


def test_raises_when_key_unset(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(StripeNotConfiguredError):
        snapshot_product_mrr("prod-1", ["price_123"])


class _FakePage:
    def __init__(self, data, has_more):
        self.data = data
        self._has_more = has_more

    def get(self, key, default=None):
        if key == "has_more":
            return self._has_more
        return default


class _FakeSubscriptionList:
    def __init__(self, pages):
        self._pages = pages
        self._calls = 0

    def list(self, status, price, limit, starting_after):
        page = self._pages[self._calls]
        self._calls += 1
        return page


class _FakeStripeModule:
    def __init__(self, pages):
        self.api_key = None
        self.Subscription = _FakeSubscriptionList(pages)


def _sub(sub_id, price_id, unit_amount, quantity=1, interval="month", interval_count=1):
    return {
        "id": sub_id,
        "items": {
            "data": [
                {
                    "price": {"id": price_id, "unit_amount": unit_amount, "recurring": {"interval": interval, "interval_count": interval_count}},
                    "quantity": quantity,
                }
            ]
        },
    }


def test_sums_monthly_subscriptions(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    page = _FakePage(data=[_sub("sub_1", "price_123", 2900), _sub("sub_2", "price_123", 2900)], has_more=False)
    fake_stripe = _FakeStripeModule([page])

    row = snapshot_product_mrr("prod-1", ["price_123"], stripe_module=fake_stripe)

    assert row["mrr_cents"] == 5800
    assert row["active_subs"] == 2
    assert row["product_id"] == "prod-1"


def test_annual_price_normalized_to_monthly(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    page = _FakePage(data=[_sub("sub_1", "price_annual", 120000, interval="year")], has_more=False)
    fake_stripe = _FakeStripeModule([page])

    row = snapshot_product_mrr("prod-1", ["price_annual"], stripe_module=fake_stripe)

    assert row["mrr_cents"] == 10000
    assert row["active_subs"] == 1


def test_upserts_into_supabase_when_client_given(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    page = _FakePage(data=[_sub("sub_1", "price_123", 5000)], has_more=False)
    fake_stripe = _FakeStripeModule([page])

    captured = {}

    class FakeTable:
        def upsert(self, row, on_conflict):
            captured["row"] = row
            captured["on_conflict"] = on_conflict
            return self

        def execute(self):
            return None

    class FakeSupabase:
        def table(self, name):
            captured["table"] = name
            return FakeTable()

    snapshot_product_mrr("prod-1", ["price_123"], supabase_client=FakeSupabase(), stripe_module=fake_stripe)

    assert captured["table"] == "mse_product_mrr"
    assert captured["on_conflict"] == "product_id,snapshot_date"
    assert captured["row"]["mrr_cents"] == 5000
