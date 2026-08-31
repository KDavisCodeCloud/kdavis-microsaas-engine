from datetime import datetime, timedelta, timezone

import pytest

from agents.dist.support_gap_analyzer import analyze_gaps


def _ticket(classification, days_ago=1):
    return {
        "id": f"t-{classification}-{days_ago}",
        "product_id": "p1",
        "classification": classification,
        "subject": "How do I add a unit?",
        "body": "Can't find the button to add a new unit to my property.",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
    }


class FakeQuery:
    def __init__(self, rows, table_name, store):
        self._rows = rows
        self.table_name = table_name
        self.store = store
        self._payload = None
    def select(self, *a, **k):
        return self
    def eq(self, key, value):
        self._rows = [r for r in self._rows if r.get(key) == value]
        return self
    def gte(self, key, value):
        return self  # rows already pre-filtered to the lookback window in the fixture
    def limit(self, n):
        return self
    def upsert(self, row, on_conflict=None):
        self._payload = row
        return self
    def execute(self):
        if self._payload is not None:
            self.store.upserted.append(self._payload)
            return type("R", (), {"data": [{**self._payload, "id": "surface-1"}]})()
        return type("R", (), {"data": self._rows})()


class FakeDB:
    def __init__(self, tickets, surfaces_table_exists=True):
        self.tickets = tickets
        self.surfaces_table_exists = surfaces_table_exists
        self.upserted = []

    def table(self, name):
        if name == "mse_support_tickets":
            return FakeQuery(list(self.tickets), name, self)
        if name == "mse_content_surfaces":
            if not self.surfaces_table_exists:
                raise Exception("relation \"mse_content_surfaces\" does not exist")
            return FakeQuery([], name, self)
        raise AssertionError(f"unexpected table {name}")


def _fake_llm(system, user, max_tokens=400):
    return '{"title": "How do I add a unit to my property?", "body_mdx": "Go to Properties, select the property, click Add Unit."}'


@pytest.mark.asyncio
async def test_three_occurrences_emits_faq_draft_when_surfaces_table_exists():
    tickets = [_ticket("add_unit_howto", days_ago=d) for d in (1, 5, 10)]
    db = FakeDB(tickets, surfaces_table_exists=True)

    result = await analyze_gaps("p1", supabase_client=db, llm_analyze=_fake_llm)

    assert result["surfaces_table_available"] is True
    assert "add_unit_howto" in result["recurring_classifications"]
    assert len(result["faq_drafts_written"]) == 1
    assert db.upserted[0]["archetype"] == "faq_block"
    assert db.upserted[0]["hitl_tier"] == 1
    assert db.upserted[0]["status"] == "draft"


@pytest.mark.asyncio
async def test_two_occurrences_does_not_trigger():
    tickets = [_ticket("rare_question", days_ago=d) for d in (1, 5)]
    db = FakeDB(tickets, surfaces_table_exists=True)

    result = await analyze_gaps("p1", supabase_client=db, llm_analyze=_fake_llm)
    assert result["recurring_classifications"] == []
    assert result["faq_drafts_written"] == []


@pytest.mark.asyncio
async def test_surfaces_table_missing_flags_docs_gap_instead_of_crashing():
    tickets = [_ticket("add_unit_howto", days_ago=d) for d in (1, 5, 10)]
    db = FakeDB(tickets, surfaces_table_exists=False)

    result = await analyze_gaps("p1", supabase_client=db, llm_analyze=_fake_llm)

    assert result["surfaces_table_available"] is False
    assert result["faq_drafts_written"] == []
    assert "add_unit_howto" in result["docs_gaps_flagged"]
