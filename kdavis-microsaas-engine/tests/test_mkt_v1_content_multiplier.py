import json

import pytest

from agents.marketing.mkt_v1_content_multiplier import run, run_v1_content_multiplier


class FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeMessage(self._responses.pop(0))


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _research_report(icp_channels):
    return {
        "cycle_date": "2026-08-12",
        "icp_channels": icp_channels,
        "pain_language": [{"phrase": "manual invoicing eats my Sundays", "context": "c", "source": "s", "frequency": 3}],
        "content_angles": [{"angle": "stop doing this by hand", "supporting_data": "d"}],
        "proof_signals": [{"signal": "sig", "source": "src"}],
    }


BOTH_PLATFORMS_JSON = json.dumps({
    "posts": [
        {"platform": "reddit", "title": "Anyone else losing their Sunday to manual invoicing?", "body": "b" * 200},
        {"platform": "facebook", "title": None, "body": "b" * 100},
    ]
})


def test_happy_path_writes_one_post_per_platform(fake_db):
    fake_db.responses["mse_social_content"] = [{"id": "content-1"}]
    anthropic_client = FakeAnthropic(responses=[BOTH_PLATFORMS_JSON])

    result = run_v1_content_multiplier(
        research_report=_research_report(["reddit", "facebook_groups"]),
        product_id="prod-1",
        campaign_build_id="camp-1",
        supabase_client=fake_db,
        anthropic_client=anthropic_client,
    )

    assert result["platforms"] == ["reddit", "facebook"]
    assert len(result["posts"]) == 2

    writes = [c for c in fake_db.executed if c.table_name == "mse_social_content"]
    assert len(writes) == 2
    assert writes[0]._payload["platform"] == "reddit"
    assert writes[0]._payload["title"]
    assert writes[1]._payload["platform"] == "facebook"
    assert writes[1]._payload["title"] is None

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "win"

    events = [c for c in fake_db.executed if c.table_name == "usage_events"]
    event_types = [c._payload["event_type"] for c in events]
    assert "social_content_started" in event_types
    assert "social_content_completed" in event_types


def test_only_requests_platforms_present_in_icp_channels(fake_db):
    fake_db.responses["mse_social_content"] = [{"id": "content-1"}]
    reddit_only_json = json.dumps({
        "posts": [{"platform": "reddit", "title": "t", "body": "b" * 200}],
    })
    anthropic_client = FakeAnthropic(responses=[reddit_only_json])

    result = run_v1_content_multiplier(
        research_report=_research_report(["reddit", "linkedin"]),
        product_id="prod-1",
        campaign_build_id="camp-1",
        supabase_client=fake_db,
        anthropic_client=anthropic_client,
    )

    assert result["platforms"] == ["reddit"]
    user_prompt = anthropic_client.messages.calls[0]["messages"][0]["content"]
    assert '"reddit"' in user_prompt
    assert "facebook" not in user_prompt.split("Target platforms")[1].split("\n")[0]


def test_raises_when_neither_reddit_nor_facebook_in_icp_channels(fake_db):
    with pytest.raises(RuntimeError, match="MKT-V1 content multiplier failed"):
        run_v1_content_multiplier(
            research_report=_research_report(["linkedin"]),
            product_id="prod-1",
            campaign_build_id="camp-1",
            supabase_client=fake_db,
            anthropic_client=FakeAnthropic(responses=[]),
        )

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"


def test_malformed_llm_output_writes_lose_audit_and_raises(fake_db):
    anthropic_client = FakeAnthropic(responses=["not valid json {{{"])

    with pytest.raises(RuntimeError, match="MKT-V1 content multiplier failed"):
        run_v1_content_multiplier(
            research_report=_research_report(["reddit"]),
            product_id="prod-1",
            campaign_build_id="camp-1",
            supabase_client=fake_db,
            anthropic_client=anthropic_client,
        )

    writes = [c for c in fake_db.executed if c.table_name == "mse_social_content"]
    assert writes == []


def test_wrong_post_count_raises(fake_db):
    only_one_post_json = json.dumps({
        "posts": [{"platform": "reddit", "title": "t", "body": "b" * 200}],
    })
    anthropic_client = FakeAnthropic(responses=[only_one_post_json])

    with pytest.raises(RuntimeError, match="expected 2 post"):
        run_v1_content_multiplier(
            research_report=_research_report(["reddit", "facebook_groups"]),
            product_id="prod-1",
            campaign_build_id="camp-1",
            supabase_client=fake_db,
            anthropic_client=anthropic_client,
        )


def test_run_adapter_updates_social_status_complete(fake_db, monkeypatch):
    import agents.marketing.mkt_v1_content_multiplier as mod
    monkeypatch.setattr(mod, "get_supabase", lambda: fake_db)
    anthropic_client = FakeAnthropic(responses=[BOTH_PLATFORMS_JSON])
    monkeypatch.setattr(
        mod, "run_v1_content_multiplier",
        lambda research_report, product_id, campaign_build_id, supabase_client=None, anthropic_client=None: {
            "product_id": product_id, "platforms": ["reddit", "facebook"], "posts": [], "ids": ["id-1", "id-2"],
        },
    )

    result = run(
        research_report=_research_report(["reddit", "facebook_groups"]),
        campaign_build={"id": "camp-1", "product_id": "prod-1"},
    )

    assert result["ids"] == ["id-1", "id-2"]
    updates = [c for c in fake_db.executed if c.table_name == "campaign_builds"]
    assert updates[-1]._payload == {"social_status": "complete"}


def test_run_adapter_updates_social_status_failed_on_error(fake_db, monkeypatch):
    import agents.marketing.mkt_v1_content_multiplier as mod
    monkeypatch.setattr(mod, "get_supabase", lambda: fake_db)

    def _boom(*args, **kwargs):
        raise RuntimeError("MKT-V1 content multiplier failed for product prod-1: boom")

    monkeypatch.setattr(mod, "run_v1_content_multiplier", _boom)

    with pytest.raises(RuntimeError):
        run(
            research_report=_research_report(["reddit"]),
            campaign_build={"id": "camp-1", "product_id": "prod-1"},
        )

    updates = [c for c in fake_db.executed if c.table_name == "campaign_builds"]
    assert updates[-1]._payload == {"social_status": "failed"}
