"""
tests/test_scraper_v2_entry_point.py

End-to-end wiring for agents.marketing.mkt_lead_finder.run_scraper_v2_scout.

The unit tests cover find_company_first_signals thoroughly, but nothing
covered the ENTRY POINT -- and a wiring bug there (a wrong kwarg, a missing
policy, an un-passed taxonomy) only shows up after spending real Brave quota
and ~4 minutes of wall clock on a live run. Two of those were found exactly
that way on 2026-10-01. This exercises the whole function against a fake
Supabase and a fake scraper so the next one is caught for free.
"""

from datetime import date

import pytest

import agents.marketing.mkt_lead_finder as mlf


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeTable:
    """Records every write and answers reads from canned rows."""

    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._op = None
        self._payload = None

    # reads
    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, *_a, **_k):
        return self

    def neq(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    @property
    def not_(self):
        return self

    def gte(self, *_a, **_k):
        return self

    def lt(self, *_a, **_k):
        return self

    def lte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    # writes
    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def upsert(self, payload, **_k):
        self._op, self._payload = "upsert", payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        if self._op in ("insert", "update", "upsert"):
            self.store.writes.append((self.name, self._op, self._payload))
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            # Inserted rows need an id back (the caller logs activities on them)
            return FakeResult([{**r, "id": f"{self.name}-{i}"} for i, r in enumerate(rows)])
        rows = list(self.store.responses.get(self.name, []))
        if getattr(self, "_single", False):
            # maybe_single() yields ONE row (or None), not a list -- real
            # PostgREST returns bare None when nothing matches.
            return FakeResult(rows[0] if rows else None)
        return FakeResult(rows)


class FakeDb:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.writes = []

    def table(self, name):
        return FakeTable(self, name)


class FakeScraper:
    api_key = "k"

    def __init__(self, responses):
        self.responses = responses
        self.queries = []

    def _search(self, query):
        self.queries.append(query)
        for needle, items in self.responses.items():
            if needle in query:
                return items
        return []


GREENHOUSE = {
    "meta": {"company_name": "Northwind Systems"},
    "jobs": [
        {"title": "Senior Platform Engineer", "absolute_url": "https://boards.greenhouse.io/northwind/jobs/1",
         "updated_at": date.today().isoformat(),
         "content": "We run Azure, Terraform and Kubernetes. A team of 60 employees."},
        {"title": "Site Reliability Engineer", "absolute_url": "https://boards.greenhouse.io/northwind/jobs/2",
         "updated_at": date.today().isoformat(), "content": "Azure and Terraform."},
        {"title": "DevOps Engineer", "absolute_url": "https://boards.greenhouse.io/northwind/jobs/3",
         "updated_at": date.today().isoformat(), "content": "Kubernetes."},
        {"title": "Office Manager", "absolute_url": "https://boards.greenhouse.io/northwind/jobs/4",
         "updated_at": date.today().isoformat(), "content": "Front desk."},
    ],
}


def ats_client(payload_by_token):
    from scrapers.ats_boards import AtsBoardClient

    def _get(url, **_kw):
        for key, payload in payload_by_token.items():
            if key in url:
                return type("R", (), {"status_code": 200, "json": lambda self, p=payload: p})()
        return type("R", (), {"status_code": 404, "json": lambda self: {}})()

    return AtsBoardClient(http_get=_get, sleep=lambda _s: None)


@pytest.fixture
def patched(monkeypatch):
    """Stub every network edge: Brave, the ATS client, DNS, homepages and
    SMTP. Nothing here touches the network or sleeps."""
    import agents.marketing.company_first_sourcing as cfs
    import agents.marketing.domain_resolution as dr

    scraper = FakeScraper({
        "site:boards.greenhouse.io": [{"link": "https://boards.greenhouse.io/northwind/jobs/1",
                                      "title": "Senior Platform Engineer", "snippet": ""}],
        "site:linkedin.com/in": [{"link": "https://linkedin.com/in/janedoe",
                                 "title": "Jane Doe - Northwind Systems | LinkedIn",
                                 "snippet": "Jane Doe. VP of Engineering at Northwind Systems."}],
        "official website": [],
    })
    monkeypatch.setattr(mlf, "BraveSearchScraper", lambda **_k: scraper, raising=False)
    monkeypatch.setattr("scrapers.brave_search.BraveSearchScraper", lambda **_k: scraper)
    monkeypatch.setattr(cfs, "AtsBoardClient", lambda *a, **k: ats_client({"northwind": GREENHOUSE}))
    monkeypatch.setattr(dr, "_default_dns_resolves", lambda _h: True)
    monkeypatch.setattr(cfs, "_pace", lambda _s: None)
    monkeypatch.setattr(dr, "httpx", type("X", (), {
        "get": staticmethod(lambda url, **kw: type("R", (), {
            "status_code": 200, "text": "<title>Northwind Systems</title>"})())
    }))
    # No SMTP: email resolution returns nothing rather than probing.
    monkeypatch.setattr(mlf, "_make_email_resolver", lambda *a, **k: (lambda _n, _d: (None, "unknown")))
    return scraper


class TestRunScraperV2Scout:
    def _db(self):
        return FakeDb({
            "mse_lead_finder_runs": [{"id": "run-1"}],
            "mse_icp_configs": [{"product_id": mlf.THDAGENTIC_CONSULTING_PRODUCT_ID,
                                 "job_titles": ["CTO"], "role_taxonomy": None}],
            "mse_leads": [],
            "mse_ats_board_tokens": [],
            "usage_events": [], "audit_log": [], "mse_activities": [],
        })

    def test_full_run_writes_a_lead_and_completes(self, patched):
        db = self._db()
        result = mlf.run_scraper_v2_scout(
            mlf.THDAGENTIC_CONSULTING_PRODUCT_ID, supabase_client=db, max_queries=12)

        assert result["status"] == "complete"
        assert result["leads_written"] == 1
        assert result["stage1_passed"] == 1

        inserts = [w for w in db.writes if w[0] == "mse_leads" and w[1] == "insert"]
        assert len(inserts) == 1
        row = inserts[0][2][0]
        assert row["company"] == "Northwind Systems"
        assert row["domain"] == "northwind.com"
        assert row["domain_source"] == "constructed_verified"
        assert row["status"] == "pending_dm"
        assert row["contact_status"] == "found"
        assert row["first_name"] == "Jane"
        assert row["size_proxy_open_roles"] == 4

    def test_run_row_is_completed_with_funnel_stats(self, patched):
        db = self._db()
        mlf.run_scraper_v2_scout(mlf.THDAGENTIC_CONSULTING_PRODUCT_ID,
                                supabase_client=db, max_queries=12)
        updates = [w[2] for w in db.writes if w[0] == "mse_lead_finder_runs" and w[1] == "update"]
        final = updates[-1]
        assert final["status"] == "complete"
        assert final["funnel_stats"]["stage1_passed"] == 1
        assert "queries_total" in final["funnel_stats"]

    def test_funnel_stats_are_json_serialisable(self, patched):
        import json

        db = self._db()
        result = mlf.run_scraper_v2_scout(mlf.THDAGENTIC_CONSULTING_PRODUCT_ID,
                                        supabase_client=db, max_queries=12)
        json.dumps(result["funnel_stats"])

    def test_query_cap_is_respected_end_to_end(self, patched):
        db = self._db()
        result = mlf.run_scraper_v2_scout(mlf.THDAGENTIC_CONSULTING_PRODUCT_ID,
                                        supabase_client=db, max_queries=8)
        assert result["brave_queries_used"] <= 8
        assert len(patched.queries) <= 8

    def test_cloud_decoded_uses_the_downweighting_policy(self, patched, monkeypatch):
        """The two products must not share one exclusion policy -- that was
        the point of decision 3."""
        seen = {}
        import agents.marketing.company_first_sourcing as cfs
        original = cfs.find_company_first_signals

        def spy(*a, **kw):
            seen["policy"] = kw.get("policy")
            return original(*a, **kw)

        monkeypatch.setattr(cfs, "find_company_first_signals", spy)
        db = FakeDb({
            "mse_lead_finder_runs": [{"id": "run-cd"}],
            "mse_icp_configs": [{"product_id": mlf.CLOUD_DECODED_PRODUCT_ID, "role_taxonomy": None}],
            "mse_leads": [], "mse_ats_board_tokens": [],
            "usage_events": [], "audit_log": [], "mse_activities": [],
        })
        mlf.run_scraper_v2_scout(mlf.CLOUD_DECODED_PRODUCT_ID, supabase_client=db, max_queries=8)

        from agents.marketing.company_classification import (
            CLOUD_DECODED_EXCLUSION_POLICY,
            INFRA_VENDOR,
        )
        assert seen["policy"] is CLOUD_DECODED_EXCLUSION_POLICY
        assert INFRA_VENDOR not in seen["policy"].exclude_tags

    def test_a_failed_run_still_records_brave_spend_and_marks_the_row(self, patched, monkeypatch):
        """A run that dies must not leave the row at 'running' with no
        accounting -- Brave bills a query when it is issued."""
        import agents.marketing.company_first_sourcing as cfs

        def boom(*_a, **kw):
            stats = kw.get("stats")
            if stats is not None:
                stats.queries_discovery = 3
            raise RuntimeError("exploded mid-run")

        monkeypatch.setattr(cfs, "find_company_first_signals", boom)
        db = self._db()
        with pytest.raises(RuntimeError):
            mlf.run_scraper_v2_scout(mlf.THDAGENTIC_CONSULTING_PRODUCT_ID,
                                    supabase_client=db, max_queries=12)

        updates = [w[2] for w in db.writes if w[0] == "mse_lead_finder_runs" and w[1] == "update"]
        assert updates[-1]["status"] == "failed"
        assert updates[-1]["funnel_stats"]["queries_discovery"] == 3
        usage = [w[2] for w in db.writes if w[0] == "usage_events" and w[1] == "insert"]
        assert any(u.get("event_type") == "brave_search_queries_used" for u in usage)
