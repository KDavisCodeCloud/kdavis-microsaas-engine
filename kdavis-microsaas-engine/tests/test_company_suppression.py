"""Company-level suppression (marketing fix, 2026-11-07).

The job-signal finders build their "already seen" sets from mse_leads AND
from mse_suppressed_companies, so a purged company cannot be re-ingested
even if its lead rows are ever cleaned up.
"""

from agents.marketing.mkt_lead_finder import (
    _existing_job_signal_companies,
    _existing_job_signal_domains,
)


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def execute(self):
        return _Res([r for r in self.rows if all(r.get(k) == v for k, v in self.filters)])


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))


def _db():
    return _DB({
        "mse_leads": [{"source": "job_posting_signal", "company": "Acme Corp", "domain": "acme.com"}],
        "mse_suppressed_companies": [
            {"domain": "ziprecruiter.com", "company_norm": None, "product_id": None},
            {"domain": None, "company_norm": "staffordgray", "product_id": None},
        ],
    })


def test_suppressed_domains_join_existing_domains():
    domains = _existing_job_signal_domains(_db())
    assert {"acme.com", "ziprecruiter.com"} <= domains


def test_suppressed_names_join_existing_companies():
    names = _existing_job_signal_companies(_db())
    assert {"acmecorp", "staffordgray"} <= names


def test_lane_and_retry_skip_purged_rows():
    import inspect
    from api.routers import buyer_research
    from agents.marketing import mkt_lead_finder
    assert "rejected_wrong_icp" in inspect.getsource(buyer_research.list_buyer_research_tasks)
    assert "rejected_wrong_icp" in inspect.getsource(mkt_lead_finder)
