import json

import pytest

from agents.dist import positioning_researcher, wedge_validator


class FakeQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._filters = []
        self._payload = None
        self._order_desc = False
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def order(self, *args, **kwargs):
        self._order_desc = kwargs.get("desc", False)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self._single = True
        return self

    def _matching(self, rows):
        result = rows
        for key, value in self._filters:
            result = [r for r in result if r.get(key) == value]
        return result

    def execute(self):
        rows = self.store.data.get(self.name, [])
        if self._payload is not None and self.name == "mse_positioning" and "id" not in self._payload:
            # insert
            new_row = {**self._payload, "id": f"row-{len(rows) + 1}"}
            self.store.data.setdefault(self.name, []).append(new_row)
            return type("Result", (), {"data": [new_row]})()
        if self._payload is not None:
            # update: matched rows get merged in place
            matched = self._matching(rows)
            for r in matched:
                r.update(self._payload)
            return type("Result", (), {"data": matched})()

        matched = self._matching(rows)
        if self._order_desc:
            matched = sorted(matched, key=lambda r: r.get("version", 0), reverse=True)
        if self._limit is not None:
            matched = matched[: self._limit]
        if getattr(self, "_single", False):
            if not matched:
                return None
            return type("Result", (), {"data": matched[0]})()
        return type("Result", (), {"data": matched})()


class FakeSupabase:
    def __init__(self, data=None):
        self.data = data or {}

    def table(self, name):
        return FakeQuery(self, name)


def _product_row(slug="tradesdesk", pid="p1"):
    return {"id": pid, "slug": slug, "name": "TradesDesk"}


# ---- positioning_researcher.research_positioning -----------------------


async def test_research_positioning_raises_on_missing_product():
    db = FakeSupabase(data={"mse_products": []})
    with pytest.raises(ValueError, match="No mse_products row"):
        await positioning_researcher.research_positioning("nonexistent", "context", supabase_client=db)


async def test_research_positioning_inserts_draft_row(monkeypatch):
    db = FakeSupabase(data={"mse_products": [_product_row()], "mse_positioning": []})

    fake_llm_output = json.dumps({
        "icp": "Solo plumber, 1-3 trucks",
        "trigger_event": "Missed a job because of a scheduling conflict",
        "substitute_set": [
            {"name": "Orcatec", "kind": "free_tool", "price_to_buyer": 0, "monetization": None,
             "switching_cost_hours": 3, "source_url": "https://orcatec.com", "verified_at": "2026-08-30"},
            {"name": "Jobber", "kind": "paid_tool", "price_to_buyer": 49, "monetization": "subscription",
             "switching_cost_hours": 8, "source_url": "https://getjobber.com", "verified_at": "2026-08-30"},
            {"name": "Pen and paper", "kind": "do_nothing", "price_to_buyer": 0, "monetization": None,
             "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
        ],
        "wedge": "Flat pricing regardless of crew size",
        "wedge_type": "temporary",
        "wedge_evidence": {"claims": []},
        "price_rationale": "Argued against Orcatec at $0",
        "kill_criteria": "Below $2K MRR by month 6 -> stop funding",
    })
    monkeypatch.setattr(positioning_researcher, "analyze_with_web_search", lambda *a, **k: fake_llm_output)

    row = await positioning_researcher.research_positioning("tradesdesk", "FSM for trades", supabase_client=db)

    assert row["status"] == "draft"
    assert row["version"] == 1
    assert row["product_id"] == "p1"
    assert len(row["substitute_set"]) == 3
    assert any(s["kind"] == "do_nothing" for s in row["substitute_set"])


async def test_research_positioning_increments_version(monkeypatch):
    db = FakeSupabase(data={
        "mse_products": [_product_row()],
        "mse_positioning": [{"id": "existing", "product_id": "p1", "version": 1}],
    })
    fake_llm_output = json.dumps({
        "icp": "x", "trigger_event": "x",
        "substitute_set": [
            {"name": "A", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
            {"name": "B", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
            {"name": "C", "kind": "do_nothing", "price_to_buyer": 0, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
        ],
        "wedge": "x", "wedge_type": "temporary", "wedge_evidence": {}, "price_rationale": "x", "kill_criteria": "x",
    })
    monkeypatch.setattr(positioning_researcher, "analyze_with_web_search", lambda *a, **k: fake_llm_output)

    row = await positioning_researcher.research_positioning("tradesdesk", "ctx", supabase_client=db)
    assert row["version"] == 2


async def test_research_positioning_strips_markdown_fences(monkeypatch):
    db = FakeSupabase(data={"mse_products": [_product_row()], "mse_positioning": []})
    fenced = "```json\n" + json.dumps({
        "icp": "x", "trigger_event": "x",
        "substitute_set": [
            {"name": "A", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
            {"name": "B", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
            {"name": "C", "kind": "do_nothing", "price_to_buyer": 0, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
        ],
        "wedge": "x", "wedge_type": "temporary", "wedge_evidence": {}, "price_rationale": "x", "kill_criteria": "x",
    }) + "\n```"
    monkeypatch.setattr(positioning_researcher, "analyze_with_web_search", lambda *a, **k: fenced)

    row = await positioning_researcher.research_positioning("tradesdesk", "ctx", supabase_client=db)
    assert row["status"] == "draft"


# ---- wedge_validator.validate_wedge -------------------------------------


async def test_validate_wedge_raises_on_missing_row():
    db = FakeSupabase(data={"mse_positioning": []})
    with pytest.raises(ValueError, match="No mse_positioning row"):
        await wedge_validator.validate_wedge("missing", supabase_client=db)


async def test_validate_wedge_never_writes_approved(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [{
        "id": "b1", "product_id": "p1", "icp": "x", "trigger_event": "x",
        "substitute_set": [{"name": "A", "kind": "do_nothing"}],
        "wedge": "x", "wedge_type": "structural", "wedge_evidence": {}, "price_rationale": "x",
        "review_log": [],
    }]})
    findings = json.dumps({
        "corrected_wedge_type": "temporary",
        "downgrade_reason": "Substitute could ship this in one sprint",
        "unsourced_claims": ["claim X is asserted, not sourced"],
        "price_rationale_flag": None,
        "verdict": "pending_review",
        "notes": "Real finding.",
    })
    monkeypatch.setattr(wedge_validator, "analyze", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)

    assert row["status"] == "pending_review"
    assert row["status"] != "approved"
    assert row["wedge_type"] == "temporary"
    assert row["moat_risk"] is True


async def test_validate_wedge_downgrade_sets_moat_risk_true(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [{
        "id": "b1", "product_id": "p1", "icp": "x", "trigger_event": "x",
        "substitute_set": [], "wedge": "x", "wedge_type": "structural",
        "wedge_evidence": {}, "price_rationale": "x", "review_log": [],
    }]})
    findings = json.dumps({
        "corrected_wedge_type": "structural",
        "downgrade_reason": None,
        "unsourced_claims": [],
        "price_rationale_flag": None,
        "verdict": "pending_review",
        "notes": "Confirmed structural.",
    })
    monkeypatch.setattr(wedge_validator, "analyze", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["moat_risk"] is False
    assert row["wedge_type"] == "structural"


async def test_validate_wedge_appends_review_log(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [{
        "id": "b1", "product_id": "p1", "icp": "x", "trigger_event": "x",
        "substitute_set": [], "wedge": "x", "wedge_type": "structural",
        "wedge_evidence": {}, "price_rationale": "x",
        "review_log": [{"date": "2026-08-01", "reviewer": "DIST-P1", "outcome": "draft", "notes": "initial"}],
    }]})
    findings = json.dumps({
        "corrected_wedge_type": "temporary", "downgrade_reason": "x", "unsourced_claims": [],
        "price_rationale_flag": None, "verdict": "pending_review", "notes": "second review",
    })
    monkeypatch.setattr(wedge_validator, "analyze", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert len(row["review_log"]) == 2
    assert row["review_log"][-1]["notes"] == "second review"
