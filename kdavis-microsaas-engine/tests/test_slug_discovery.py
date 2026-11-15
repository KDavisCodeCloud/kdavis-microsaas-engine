"""Lead-source expansion, Phase 1 (2026-11-15): Recruitee, parallel per-provider
board sweeps, the full-cache read, weekly slug discovery and per-source Brave
allocations. No network: every HTTP call and Brave query is a stub."""

import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from agents.marketing import brave_budget as bb
from agents.marketing import slug_discovery as sd
from agents.marketing.company_first_sourcing import load_cached_board_refs
from scrapers.ats_boards import (
    AtsBoardClient,
    BoardRef,
    board_ref_from_url,
    company_domain_from_postings,
)


class R:
    def __init__(self, payload=None, status=200, text=""):
        self._p, self.status_code, self.text = payload, status, text

    def json(self):
        return self._p


# ── Recruitee ─────────────────────────────────────────────────────────────

RECRUITEE = {"offers": [
    {"title": "Senior DevOps Engineer", "careers_url": "https://careers.bunq.com/o/devops",
     "company_name": "bunq", "status": "published", "location": "Amsterdam, Netherlands",
     "published_at": "2026-11-11 13:21:40 UTC", "description": "<p>We run <b>Kubernetes</b></p>",
     "requirements": "<p>Terraform</p>"},
    {"title": "Draft role", "status": "draft", "company_name": "bunq"},
]}


def test_recruitee_slug_is_the_subdomain():
    assert board_ref_from_url("https://bunq.recruitee.com/o/security-engineer-3") == BoardRef("recruitee", "bunq")


@pytest.mark.parametrize("url", ["https://www.recruitee.com/pricing", "https://app.recruitee.com/x",
                                 "https://a.b.recruitee.com/o/x", "https://recruitee.com/"])
def test_recruitee_vendor_pages_are_not_slugs(url):
    assert board_ref_from_url(url) is None


def test_recruitee_board_is_normalised_and_robots_checked():
    calls = []

    def get(url, **_k):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return R(status=200, text="User-agent: *\nDisallow: /v/\n")
        return R(RECRUITEE)

    c = AtsBoardClient(http_get=get, sleep=lambda _s: None)
    posts = c.fetch_board(BoardRef("recruitee", "bunq"))
    assert calls == ["https://bunq.recruitee.com/robots.txt", "https://bunq.recruitee.com/api/offers/"]
    assert [p.title for p in posts] == ["Senior DevOps Engineer"], "unpublished offers are not open roles"
    p = posts[0]
    assert p.company == "bunq" and p.posted_at == date(2026, 11, 11)
    assert "Kubernetes" in p.description_text and "Terraform" in p.description_text
    assert company_domain_from_postings(posts) == "bunq.com", "careers. subdomain is not the company domain"


def test_recruitee_robots_disallow_skips_the_board():
    def get(url, **_k):
        if url.endswith("/robots.txt"):
            return R(status=200, text="User-agent: *\nDisallow: /api/\n")
        raise AssertionError("must not fetch a disallowed path")

    c = AtsBoardClient(http_get=get, sleep=lambda _s: None)
    assert c.fetch_board(BoardRef("recruitee", "private")) == []
    assert c.board_status["recruitee:private"] == "failed"
    assert c.stats.http_errors == {"robots_disallowed": 1}


def test_robots_read_once_per_host():
    calls = []

    def get(url, **_k):
        calls.append(url)
        return R(status=404) if url.endswith("robots.txt") else R({"offers": []})

    c = AtsBoardClient(http_get=get, sleep=lambda _s: None)
    c.fetch_board(BoardRef("recruitee", "x"))
    c.fetch_board(BoardRef("recruitee", "x"))
    assert sum(u.endswith("robots.txt") for u in calls) == 1


def test_smartrecruiters_is_not_a_provider():
    """robots.txt on api.smartrecruiters.com disallows us (stop condition)."""
    assert board_ref_from_url("https://jobs.smartrecruiters.com/Acme/123-devops") is None


# ── parallel sweep ────────────────────────────────────────────────────────

def test_providers_run_side_by_side_but_each_host_stays_sequential():
    active: dict[str, int] = {}
    peak_per_host: dict[str, int] = {}
    peak_total = [0]
    lock = threading.Lock()

    def get(url, **_k):
        host = url.split("/")[2]
        with lock:
            active[host] = active.get(host, 0) + 1
            peak_per_host[host] = max(peak_per_host.get(host, 0), active[host])
            peak_total[0] = max(peak_total[0], sum(active.values()))
        time.sleep(0.02)
        with lock:
            active[host] -= 1
        return R({"jobs": []} if "greenhouse" in url else [])

    c = AtsBoardClient(http_get=get, sleep=lambda _s: None)
    refs = [BoardRef("greenhouse", f"g{i}") for i in range(4)] + [BoardRef("lever", f"l{i}") for i in range(4)]
    out = c.fetch_boards(refs)
    assert list(out) == [r.cache_key for r in refs], "result keeps the caller's order"
    assert max(peak_per_host.values()) == 1, "never two requests at once to one provider"
    assert peak_total[0] == 2, "the two providers overlap"
    assert c.stats.boards_fetched == 8


def test_deadline_still_bounds_a_parallel_sweep():
    ticks = iter(range(100))
    c = AtsBoardClient(http_get=lambda *_a, **_k: R([]), sleep=lambda _s: None)
    out = c.fetch_boards([BoardRef("lever", f"l{i}") for i in range(10)],
                         deadline_seconds=3, clock=lambda: next(ticks), parallel=False)
    assert 0 < len(out) < 10


# ── full-cache read ───────────────────────────────────────────────────────

class PagedTable:
    def __init__(self, rows):
        self.rows, self.ranges = rows, []

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, a, b):
        self.ranges.append((a, b))
        self._slice = self.rows[a:b + 1]
        return self

    def execute(self):
        return type("X", (), {"data": self._slice})()


class PagedDb:
    def __init__(self, rows):
        self.t = PagedTable(rows)

    def table(self, _n):
        return self.t


def test_reads_every_cached_slug_past_one_page():
    rows = [{"provider": "greenhouse", "board_token": f"t{i}", "consecutive_failures": 0} for i in range(2300)]
    db = PagedDb(rows)
    assert len(load_cached_board_refs(db, "p")) == 2300
    assert db.t.ranges == [(0, 999), (1000, 1999), (2000, 2999)]


def test_oversize_boards_are_rechecked_monthly_not_weekly():
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    rows = [
        {"provider": "greenhouse", "board_token": "big_recent", "consecutive_failures": 0,
         "open_role_count": 90, "last_fetched_at": (now - timedelta(days=7)).isoformat()},
        {"provider": "greenhouse", "board_token": "big_stale", "consecutive_failures": 0,
         "open_role_count": 90, "last_fetched_at": (now - timedelta(days=30)).isoformat()},
        {"provider": "greenhouse", "board_token": "small", "consecutive_failures": 0,
         "open_role_count": 12, "last_fetched_at": (now - timedelta(days=7)).isoformat()},
    ]
    skipped = {}
    refs = load_cached_board_refs(PagedDb(rows), "p", skip_stats=skipped, now=now)
    assert [r.token for r in refs] == ["big_stale", "small"]
    assert skipped == {"oversize": 1}


# ── slug discovery ────────────────────────────────────────────────────────

class FakeScraper:
    api_key = "k"

    def __init__(self, by_query):
        self.by_query, self.calls = by_query, []

    def _search(self, query, offset=0, count=10):
        self.calls.append((query, offset, count))
        return [{"link": u} for u in self.by_query.get(query, [])]


def test_queries_cover_every_host_and_brief_term():
    qs = sd.discovery_queries()
    assert len(qs) == 36 == sd.DEFAULT_MAX_QUERIES
    for term in ("devops", "sre", "site reliability", "platform engineer", "infrastructure", "cloud"):
        assert any(f'"{term}"' in q for q in qs)
    assert all(q.startswith("site:") and "linkedin" not in q and "wellfound" not in q for q in qs)
    assert 'site:recruitee.com "devops"' in qs


def test_only_unknown_slugs_are_new_and_page_rotates():
    q0 = sd.discovery_queries()[0]
    scraper = FakeScraper({q0: ["https://boards.greenhouse.io/known/jobs/1",
                                "https://boards.greenhouse.io/fresh/jobs/2",
                                "https://acme.recruitee.com/o/sre",
                                "https://example.com/not-ats"]})
    new, stats = sd.find_new_slugs(scraper, {"greenhouse:known"}, max_queries=3, page=2, sleep=lambda _s: None)
    assert set(new) == {"greenhouse:fresh", "recruitee:acme"}
    assert stats.queries == 3 and stats.slugs_seen == 3 and stats.slugs_new == 2
    assert stats.by_provider_new == {"greenhouse": 1, "recruitee": 1}
    assert all(off == 2 and cnt == 20 for _q, off, cnt in scraper.calls)


def test_page_for_cycles_through_five_pages():
    pages = {sd.page_for(date(2026, 1, 5) + timedelta(weeks=w)) for w in range(10)}
    assert pages == {0, 1, 2, 3, 4}


class LedgerDb:
    """usage_events + token cache + inserts, enough for run_slug_discovery."""

    def __init__(self, used_events, tokens=()):
        self.events, self.tokens, self.inserts = list(used_events), list(tokens), []
        self._t = None

    def table(self, name):
        self._t, self._range = name, None
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def insert(self, row):
        self.inserts.append((self._t, row))
        if self._t == "usage_events":
            self.events.append(row)
        return self

    def execute(self):
        if self._t == "usage_events":
            return type("X", (), {"data": [{"metadata": e["metadata"]} for e in self.events]})()
        if self._t == "mse_ats_board_tokens":
            a, b = self._range or (0, 999)
            return type("X", (), {"data": self.tokens[a:b + 1]})()
        return type("X", (), {"data": []})()


def _ev(n, source=None):
    m = {"month": date.today().isoformat()[:7], "count": n}
    if source:
        m["source"] = source
    return {"metadata": m}


def test_run_writes_new_slugs_and_records_usage_under_its_source(monkeypatch):
    q0 = sd.discovery_queries()[0]
    db = LedgerDb([_ev(220)], tokens=[{"provider": "greenhouse", "board_token": "known"}])
    scraper = FakeScraper({q0: ["https://boards.greenhouse.io/fresh/jobs/2"]})
    out = sd.run_slug_discovery(db, scraper=scraper, max_queries=4, sleep=lambda _s: None)
    assert out["slugs_new"] == 1 and out["brave_queries"] == 4
    token_rows = [r for t, r in db.inserts if t == "mse_ats_board_tokens"]
    assert token_rows[0]["board_token"] == "fresh" and token_rows[0]["discovered_from"] == "brave_slug_discovery"
    usage = [r for t, r in db.inserts if t == "usage_events"]
    assert usage[0]["metadata"]["source"] == "slug_discovery" and usage[0]["metadata"]["count"] == 4


def test_run_refused_when_month_is_at_the_threshold():
    db = LedgerDb([_ev(715)])
    with pytest.raises(RuntimeError, match="budget refused"):
        sd.run_slug_discovery(db, scraper=FakeScraper({}), max_queries=36)
    assert not db.inserts, "a refused run spends and writes nothing"


# ── per-source allocations ────────────────────────────────────────────────

def test_allocations_fit_under_the_stop_threshold():
    assert sum(bb.SOURCE_ALLOCATIONS.values()) <= bb.stop_threshold(900)


def test_source_allocation_is_enforced_separately_from_the_month():
    db = LedgerDb([_ev(100), _ev(150, "slug_discovery")])
    assert bb.check_source_budget(db, "slug_discovery", 9).allowed
    d = bb.check_source_budget(db, "slug_discovery", 11)
    assert not d.allowed and "160" in d.reason
    assert bb.check_source_budget(db, "wellfound", 36).allowed, "another source's share is untouched"


def test_per_run_cap_is_three_hundred():
    d = bb.check_source_budget(LedgerDb([]), "slug_discovery", 301)
    assert not d.allowed and "300" in d.reason


def test_unknown_source_is_refused():
    assert not bb.check_source_budget(LedgerDb([]), "linkedin_scrape", 1).allowed


def test_usage_by_source_reports_untagged_as_unattributed():
    assert bb.usage_by_source(LedgerDb([_ev(220), _ev(30, "slug_discovery")])) == {
        "unattributed": 220, "slug_discovery": 30}


# ── schedule ──────────────────────────────────────────────────────────────

def test_slug_discovery_is_due_saturday_and_before_the_sunday_scout():
    from agents.marketing.lead_schedule import _saturday_before_scout
    sat = datetime(2026, 11, 14, 12, 5, tzinfo=timezone.utc)
    assert _saturday_before_scout(sat) == "2026-11-14"
    assert _saturday_before_scout(datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)) == "2026-11-14"
    assert _saturday_before_scout(datetime(2026, 11, 15, 12, 30, tzinfo=timezone.utc)) is None
    assert _saturday_before_scout(datetime(2026, 11, 14, 11, 0, tzinfo=timezone.utc)) is None


def test_scout_skips_its_own_discovery_only_after_a_recent_run():
    class D:
        def __init__(self, rows, boom=False):
            self.rows, self.boom = rows, boom

        def table(self, _n):
            return self

        def select(self, *_a):
            return self

        def eq(self, *_a):
            return self

        def gte(self, *_a):
            return self

        def limit(self, *_a):
            return self

        def execute(self):
            if self.boom:
                raise RuntimeError("down")
            return type("X", (), {"data": self.rows})()

    assert sd.discovery_ran_recently(D([{"finished_at": "x"}]))
    assert not sd.discovery_ran_recently(D([]))
    assert not sd.discovery_ran_recently(D([], boom=True)), "fails toward the old behaviour"
