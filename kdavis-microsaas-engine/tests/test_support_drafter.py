import pytest

from agents.dist.support_drafter import draft_reply


class FakeRpcResult:
    def __init__(self, data):
        self.data = data


class FakeRpc:
    def __init__(self, rows):
        self._rows = rows
    def execute(self):
        return FakeRpcResult(self._rows)


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
    def maybe_single(self):
        self._single = True
        return self
    def insert(self, row):
        self._payload = row
        return self
    def update(self, row):
        self._payload = row
        return self
    def execute(self):
        if self._payload is not None:
            if self.table_name == "mse_support_drafts":
                inserted = {**self._payload, "id": "draft-1"}
                self.store.inserted_drafts.append(inserted)
                return type("R", (), {"data": [inserted]})()
            return type("R", (), {"data": [self._payload]})()
        if getattr(self, "_single", False):
            return type("R", (), {"data": self._rows[0] if self._rows else None})()
        return type("R", (), {"data": self._rows})()


class FakeDB:
    def __init__(self, ticket, kb_rows=None):
        self.ticket = ticket
        self.kb_rows = kb_rows or []
        self.inserted_drafts = []

    def table(self, name):
        if name == "mse_support_tickets":
            rows = [self.ticket] if self.ticket is not None else []
            return FakeQuery(rows, name, self)
        if name == "mse_support_drafts":
            return FakeQuery([], name, self)
        raise AssertionError(f"unexpected table {name}")

    def rpc(self, name, params):
        assert name == "match_support_kb"
        return FakeRpc(self.kb_rows)


async def _fake_embedding(text):
    return [0.1] * 768


@pytest.mark.asyncio
async def test_no_kb_match_produces_low_confidence_honest_draft(monkeypatch):
    import agents.dist.support_drafter as mod
    monkeypatch.setattr(mod, "get_embedding", _fake_embedding)

    ticket = {"id": "t1", "product_id": "p1", "subject": "Weird one", "body": "Does the app support carrier pigeons?"}
    db = FakeDB(ticket, kb_rows=[])  # nothing found

    def llm(system, user, max_tokens=800):
        return '{"draft": "I don\'t have a confirmed answer for this -- flagging for the owner.", "confidence": 0.9, "used_sources": false}'

    result = await draft_reply("t1", supabase_client=db, llm_analyze=llm)
    # Confidence capped low regardless of what the model claimed, since no KB/account data was used.
    assert result["confidence"] <= 0.3
    assert "flagging" in result["draft_body"].lower() or "don't have" in result["draft_body"].lower()


@pytest.mark.asyncio
async def test_real_kb_match_produces_sourced_draft(monkeypatch):
    import agents.dist.support_drafter as mod
    monkeypatch.setattr(mod, "get_embedding", _fake_embedding)

    ticket = {"id": "t2", "product_id": "p1", "subject": "Payout timing", "body": "When does rent hit my bank?"}
    kb_rows = [{"id": "kb1", "source": "docs/rent-collection.md", "chunk": "Payouts arrive 2 business days after collection.", "similarity": 0.91}]
    db = FakeDB(ticket, kb_rows=kb_rows)

    def llm(system, user, max_tokens=800):
        assert "Payouts arrive 2 business days" in user
        return '{"draft": "Your rent payout arrives 2 business days after collection.", "confidence": 0.95, "used_sources": true}'

    result = await draft_reply("t2", supabase_client=db, llm_analyze=llm)
    assert result["confidence"] == 0.95
    assert result["sources"]["kb_chunk_ids"] == ["kb1"]


@pytest.mark.asyncio
async def test_missing_ticket_raises(monkeypatch):
    import agents.dist.support_drafter as mod
    monkeypatch.setattr(mod, "get_embedding", _fake_embedding)
    db = FakeDB(ticket=None)
    with pytest.raises(ValueError, match="no ticket found"):
        await draft_reply("missing", supabase_client=db, llm_analyze=lambda *a, **k: "{}")
