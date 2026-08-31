import pytest

from agents.dist.support_classifier import classify_and_store, classify_ticket


def _fake_llm(system, user, max_tokens=300):
    return '{"tier": 2, "classification": "test_key", "sentiment": "neutral"}'


@pytest.mark.asyncio
async def test_refund_keyword_forces_tier_3_regardless_of_model():
    llm_calls = []

    def llm(system, user, max_tokens=300):
        llm_calls.append(user)
        return '{"tier": 1, "classification": "refund_request", "sentiment": "neutral"}'

    result = await classify_ticket("Question about my bill", "I want a refund for last month", llm_analyze=llm)
    assert result["tier"] == 3


@pytest.mark.asyncio
async def test_cancel_keyword_forces_tier_3():
    result = await classify_ticket(None, "How do I cancel my subscription?", llm_analyze=_fake_llm)
    assert result["tier"] == 3


@pytest.mark.asyncio
async def test_chargeback_forces_tier_3():
    result = await classify_ticket("Dispute", "I'm filing a chargeback on this charge", llm_analyze=_fake_llm)
    assert result["tier"] == 3


@pytest.mark.asyncio
async def test_lawyer_mention_forces_tier_3():
    result = await classify_ticket(None, "My lawyer says this violates the contract", llm_analyze=_fake_llm)
    assert result["tier"] == 3


@pytest.mark.asyncio
async def test_normal_question_uses_model_tier():
    result = await classify_ticket("How do I add a unit?", "Can't find the add-unit button", llm_analyze=_fake_llm)
    assert result["tier"] == 2
    assert result["classification"] == "test_key"


@pytest.mark.asyncio
async def test_invalid_model_response_raises():
    def bad_llm(system, user, max_tokens=300):
        return "not json at all"

    with pytest.raises(ValueError, match="no JSON object"):
        await classify_ticket("hi", "hello", llm_analyze=bad_llm)


@pytest.mark.asyncio
async def test_classify_and_store_inserts_real_row(fake_db):
    fake_db.responses.setdefault("mse_support_tickets", [])

    class InsertCapture:
        def __init__(self, store):
            self.store = store
        def insert(self, row):
            self.row = row
            return self
        def execute(self):
            inserted = {**self.row, "id": "ticket-1"}
            return type("R", (), {"data": [inserted]})()

    class DB:
        def table(self, name):
            assert name == "mse_support_tickets"
            return InsertCapture(self)

    result = await classify_and_store(
        product_id="prod-1", channel="email", body="please cancel my account",
        subject="cancel", supabase_client=DB(), llm_analyze=_fake_llm,
    )
    assert result["tier"] == 3
    assert result["product_id"] == "prod-1"
    assert result["channel"] == "email"
