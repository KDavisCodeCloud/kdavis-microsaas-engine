import pytest

from agents.dist.competitor_monitor import (
    _check_robots_allowed,
    _diff_snapshot,
    run_competitor_monitor,
    seed_competitors_from_positioning,
)


def _positioning_row(product_id, substitute_set):
    return {"product_id": product_id, "substitute_set": substitute_set}


def _competitor_row(id_, product_id, name, pricing_url, pricing_snapshot=None):
    return {
        "id": id_,
        "product_id": product_id,
        "name": name,
        "kind": "free_tool",
        "pricing_url": pricing_url,
        "pricing_snapshot": pricing_snapshot,
        "last_verified_at": "2026-08-01T00:00:00Z",
        "last_changed_at": None,
    }


# ---- seed_competitors_from_positioning ---------------------------------


def test_seed_excludes_do_nothing_entries(fake_db):
    fake_db.responses["mse_positioning"] = [
        _positioning_row(
            "p1",
            [
                {"kind": "free_tool", "name": "Innago", "source_url": "https://innago.com/pricing", "monetization": "tenant fees"},
                {"kind": "do_nothing", "name": "Spreadsheet + Zelle", "source_url": None},
            ],
        )
    ]

    inserted = seed_competitors_from_positioning(supabase_client=fake_db)

    assert len(inserted) == 1
    assert inserted[0]["name"] == "Innago"
    upserts = [c for c in fake_db.executed if c.table_name == "mse_competitors"]
    assert len(upserts) == 1
    assert upserts[0]._payload["name"] == "Innago"
    assert upserts[0]._payload["pricing_url"] == "https://innago.com/pricing"


def test_seed_dedupes_same_competitor_across_versions(fake_db):
    fake_db.responses["mse_positioning"] = [
        _positioning_row("p1", [{"kind": "free_tool", "name": "Innago", "source_url": "https://innago.com/pricing"}]),
        _positioning_row("p1", [{"kind": "free_tool", "name": "Innago", "source_url": "https://innago.com/pricing"}]),
    ]

    inserted = seed_competitors_from_positioning(supabase_client=fake_db)

    assert len(inserted) == 1


# ---- _diff_snapshot ------------------------------------------------------


def test_diff_snapshot_none_to_real_tiers_is_a_change():
    assert _diff_snapshot(None, [{"tier_name": "Pro", "price": 29}]) is True


def test_diff_snapshot_none_to_empty_is_not_a_change():
    assert _diff_snapshot(None, []) is False


def test_diff_snapshot_identical_tiers_different_order_is_not_a_change():
    old = [{"tier_name": "Pro", "price": 29}, {"tier_name": "Basic", "price": 0}]
    new = [{"tier_name": "Basic", "price": 0}, {"tier_name": "Pro", "price": 29}]
    assert _diff_snapshot(old, new) is False


def test_diff_snapshot_price_change_is_a_change():
    old = [{"tier_name": "Pro", "price": 29}]
    new = [{"tier_name": "Pro", "price": 39}]
    assert _diff_snapshot(old, new) is True


# ---- run_competitor_monitor ---------------------------------------------


def test_no_pricing_url_is_a_clean_skip_not_a_crash(fake_db):
    fake_db.responses["mse_competitors"] = [_competitor_row("c1", "p1", "Mystery Co", pricing_url=None)]

    results = run_competitor_monitor(supabase_client=fake_db, sleep_fn=lambda *_: None)

    assert results == [{"name": "Mystery Co", "changed": False, "error": "no pricing_url"}]


def test_fetch_failure_does_not_crash_the_whole_run(fake_db):
    fake_db.responses["mse_competitors"] = [_competitor_row("c1", "p1", "Unreachable Co", pricing_url="https://example.invalid/pricing")]

    def fetch_fn(url):
        return None

    results = run_competitor_monitor(supabase_client=fake_db, fetch_fn=fetch_fn, sleep_fn=lambda *_: None)

    assert results == [{"name": "Unreachable Co", "changed": False, "error": "fetch_failed_or_disallowed"}]


def test_price_change_writes_snapshot_and_monitoring_event(fake_db):
    fake_db.responses["mse_competitors"] = [
        _competitor_row("c1", "p1", "Innago", "https://innago.com/pricing", pricing_snapshot=[{"tier_name": "Free", "price": 0}])
    ]

    def fetch_fn(url):
        return "raw page text"

    def llm_analyze(system, user, max_tokens=1024):
        return '[{"tier_name": "Free", "price": 0}, {"tier_name": "Pro", "price": 19, "billing_period": "month", "notes": null}]'

    results = run_competitor_monitor(
        supabase_client=fake_db, llm_analyze=llm_analyze, fetch_fn=fetch_fn, sleep_fn=lambda *_: None
    )

    assert results[0]["changed"] is True
    updates = [c for c in fake_db.executed if c.table_name == "mse_competitors" and ("update", c._payload) in c.calls]
    assert len(updates) == 1
    assert updates[0]._payload["last_changed_at"] is not None
    assert updates[0]._payload["pricing_snapshot"][1]["tier_name"] == "Pro"

    events = [c for c in fake_db.executed if c.table_name == "mse_monitoring_events"]
    assert len(events) == 1
    assert events[0]._payload["severity"] == "P3"
    assert events[0]._payload["requires_human_decision"] is True
    assert "Innago" in events[0]._payload["recommended_action"]


def test_no_change_only_bumps_last_verified_at(fake_db):
    same_tiers = [{"tier_name": "Free", "price": 0}]
    fake_db.responses["mse_competitors"] = [_competitor_row("c1", "p1", "Innago", "https://innago.com/pricing", pricing_snapshot=same_tiers)]

    results = run_competitor_monitor(
        supabase_client=fake_db,
        llm_analyze=lambda s, u, max_tokens=1024: '[{"tier_name": "Free", "price": 0}]',
        fetch_fn=lambda url: "raw page text",
        sleep_fn=lambda *_: None,
    )

    assert results[0]["changed"] is False
    events = [c for c in fake_db.executed if c.table_name == "mse_monitoring_events"]
    assert events == []


def test_cascade_stale_is_a_clean_noop_when_surfaces_table_missing(fake_db):
    """Phase 4 (mse_content_surfaces) hasn't landed yet -- the cascade
    must not crash the run just because that table doesn't exist."""
    fake_db.responses["mse_competitors"] = [
        _competitor_row("c1", "p1", "Innago", "https://innago.com/pricing", pricing_snapshot=[{"tier_name": "Free", "price": 0}])
    ]

    real_table = fake_db.table

    def table_raising_on_surfaces(name):
        if name == "mse_content_surfaces":
            raise Exception("relation \"mse_content_surfaces\" does not exist")
        return real_table(name)

    fake_db.table = table_raising_on_surfaces

    results = run_competitor_monitor(
        supabase_client=fake_db,
        llm_analyze=lambda s, u, max_tokens=1024: '[{"tier_name": "Free", "price": 0}, {"tier_name": "Pro", "price": 19}]',
        fetch_fn=lambda url: "raw page text",
        sleep_fn=lambda *_: None,
    )

    assert results[0]["changed"] is True
    assert results[0]["stale_surfaces_flagged"] == 0


# ---- robots.txt -----------------------------------------------------------


def test_robots_disallow_is_respected(monkeypatch):
    import urllib.robotparser

    class FakeRobotParser:
        def set_url(self, url):
            pass

        def read(self):
            pass

        def can_fetch(self, agent, url):
            return False

    monkeypatch.setattr(urllib.robotparser, "RobotFileParser", FakeRobotParser)
    assert _check_robots_allowed("https://example.com/pricing") is False


def test_robots_unreadable_defaults_to_allowed(monkeypatch):
    import urllib.robotparser

    class FakeRobotParser:
        def set_url(self, url):
            pass

        def read(self):
            raise ConnectionError("no robots.txt")

        def can_fetch(self, agent, url):
            return True

    monkeypatch.setattr(urllib.robotparser, "RobotFileParser", FakeRobotParser)
    assert _check_robots_allowed("https://example.com/pricing") is True
