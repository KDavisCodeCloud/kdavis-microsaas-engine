"""Lead-source expansion, Phase 4: Wellfound as a signal only. No network."""

import inspect
from datetime import date

import pytest

from agents.marketing import wellfound_signal as wf


@pytest.mark.parametrize("title,url,name", [
    ("Senior DevOps Engineer at Acme Robotics • Remote | Wellfound",
     "https://wellfound.com/jobs/123-senior-devops-engineer", "Acme Robotics"),
    ("Acme Robotics Careers &amp; Jobs | Wellfound", "https://wellfound.com/company/acme-robotics", "Acme Robotics"),
    ("Northwind - Company Profile", "https://wellfound.com/company/northwind", "Northwind"),
    ("Wellfound: Startup Jobs", "https://wellfound.com/company/vector-labs", "Vector Labs"),
    ("Startup Jobs | Wellfound", "https://wellfound.com/jobs", None),
])
def test_names_come_from_title_or_url_only(title, url, name):
    assert wf.company_from_result(title, url) == name


def test_never_fetches_wellfound():
    src = inspect.getsource(wf)
    assert "http_get" not in src and "httpx" not in src
    assert all(q.startswith("site:wellfound.com/") for q in wf.WELLFOUND_QUERIES)


class Scraper:
    api_key = "k"

    def __init__(self, items):
        self.items, self.calls = items, []

    def _search(self, q, offset=0, count=10):
        self.calls.append((q, offset, count))
        return self.items if not self.calls[:-1] else []


class Ats:
    def __init__(self, boards):
        self.boards, self.fetched = boards, []

    def fetch_board(self, ref):
        self.fetched.append(ref.cache_key)
        return self.boards.get(ref.cache_key, [])


class P:
    def __init__(self, company):
        self.company, self.description_text = company, ""


class Db:
    def __init__(self):
        self.events, self.upserts = [], []
        self._t = None

    def table(self, t):
        self._t = t
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def range(self, *_a):
        return self

    def insert(self, row):
        self.events.append(row)
        return self

    def upsert(self, row, **_k):
        self.upserts.append(row)
        return self

    def execute(self):
        if self._t == "usage_events":
            return type("X", (), {"data": [{"metadata": e["metadata"]} for e in self.events]})()
        if self._t == "mse_leads":
            return type("X", (), {"data": [{"company": "Existing Co"}]})()
        return type("X", (), {"data": []})()


def test_run_queues_only_verified_boards_and_records_usage():
    items = [
        {"link": "https://wellfound.com/jobs/1-sre", "title": "SRE at Acme Robotics • Remote | Wellfound"},
        {"link": "https://wellfound.com/jobs/2-sre", "title": "SRE at Ghost Corp | Wellfound"},
        {"link": "https://wellfound.com/jobs/3-sre", "title": "SRE at Existing Co | Wellfound"},
        {"link": "https://example.com/x", "title": "SRE at Not Wellfound Inc"},
    ]
    ats = Ats({"greenhouse:acmerobotics": [P("Acme Robotics")], "lever:ghostcorp": [P("Ghostcorp")]})
    db = Db()
    out = wf.run_wellfound_signal(db, scraper=Scraper(items), ats_client=ats, max_queries=2,
                                  today=date(2026, 12, 1), sleep=lambda _s: None)
    fs = out["funnel_stats"]
    assert fs["names_found"] == 3 and fs["skipped_existing"] == 1
    assert [u["board_token"] for u in db.upserts] == ["acmerobotics"]
    assert db.upserts[0]["discovered_from"] == "wellfound_signal"
    assert fs["unresolved_names"] == ["Ghost Corp"], "a board that does not name the company is not accepted"
    assert db.events[0]["metadata"]["source"] == "wellfound" and db.events[0]["metadata"]["count"] == 2
