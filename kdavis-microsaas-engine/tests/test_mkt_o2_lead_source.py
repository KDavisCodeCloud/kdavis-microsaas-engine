"""
MKT-O2 lead_source routing (2026-08-14): apollo (unchanged, campaign_build
required), linkedin_manual (standard sequence, no campaign_build), and
linkedin_engager (opener references the real post interaction). Also
covers run_o2_for_linkedin_leads' engager-before-manual priority
ordering. See tests/test_mkt_li_intake.py's module docstring for how this
repo's FakeSupabase handles insert "returns."
"""
import json

import agents.marketing.mkt_o2_cold_dm_writer as mkt_o2_module
from agents.marketing.mkt_o2_cold_dm_writer import run_o2_cold_dm_writer
from tests.conftest import FakeSupabase

SEQUENCE_JSON = json.dumps({"touch_1": "Quick question about your showings", "touch_2": "Following up — worth 15 min?"})


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Msg", (), {"content": [type("Block", (), {"text": self._responses.pop(0)})()]})()


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _research_report():
    return {
        "pain_language": [{"phrase": "double-booked showings eat my Saturdays"}],
        "proof_signals": [{"signal": "sig"}],
    }


def test_apollo_lead_source_unchanged_behavior():
    fake_db = FakeSupabase(responses={
        "mse_dm_sequences": [{"id": "seq-1"}],
        "campaign_builds": [{"id": "cb-1", "dm_sequence_status": "ready_for_hitl"}],
        "mse_icp_configs": [{"selling_stage": "active"}],
    })
    anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
    leads = [{"id": "lead-1", "first_name": "Jane", "title": "Team Lead", "company": "Acme"}]

    result = run_o2_cold_dm_writer(
        product_id="prod-1", research_report=_research_report(), leads=leads,
        campaign_build_id="cb-1", lead_source="apollo",
        supabase_client=fake_db, anthropic_client=anthropic_client,
    )

    assert result == {"status": "ready_for_hitl", "sequences_written": 1}
    inserts = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "insert"]
    row = inserts[0]._payload[0]
    assert row["lead_id"] == "lead-1"
    assert "linkedin_lead_id" not in row
    assert row["lead_source"] == "apollo"
    assert row["campaign_build_id"] == "cb-1"

    # campaign_builds.dm_sequence_status IS touched for apollo.
    cb_updates = [c for c in fake_db.executed if c.table_name == "campaign_builds" and c.calls[0][0] == "update"]
    assert cb_updates[0]._payload == {"dm_sequence_status": "ready_for_hitl"}

    # apollo uses the standard system prompt -- no engager-specific fields on the lead.
    system_prompt = anthropic_client.messages.calls[0]["system"]
    assert "already engaged" not in system_prompt


def test_linkedin_manual_uses_standard_prompt_no_campaign_build():
    fake_db = FakeSupabase(responses={
        "mse_dm_sequences": [{"id": "seq-1"}],
        "mse_icp_configs": [{"selling_stage": "active"}],
    })
    anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
    leads = [{"id": "li-lead-1", "first_name": "Sam", "title": "Broker", "company": "Sun Realty"}]

    result = run_o2_cold_dm_writer(
        product_id="prod-1", research_report=_research_report(), leads=leads,
        campaign_build_id=None, lead_source="linkedin_manual",
        supabase_client=fake_db, anthropic_client=anthropic_client,
    )

    assert result == {"status": "ready_for_hitl", "sequences_written": 1}
    inserts = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "insert"]
    row = inserts[0]._payload[0]
    assert row["linkedin_lead_id"] == "li-lead-1"
    assert "lead_id" not in row
    assert row["lead_source"] == "linkedin_manual"
    assert row["campaign_build_id"] is None

    # campaign_builds is never touched when there's no campaign_build_id.
    cb_updates = [c for c in fake_db.executed if c.table_name == "campaign_builds"]
    assert cb_updates == []

    system_prompt = anthropic_client.messages.calls[0]["system"]
    assert "already engaged" not in system_prompt


def test_linkedin_engager_uses_engager_prompt_and_references_interaction():
    fake_db = FakeSupabase(responses={
        "mse_dm_sequences": [{"id": "seq-1"}],
        "mse_icp_configs": [{"selling_stage": "active"}],
    })
    anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
    leads = [{
        "id": "li-lead-2", "first_name": "Pat", "title": "Team Lead", "company": "Valley Realty",
        "interaction_type": "comment", "interaction_note": "AI agent guardrails",
    }]

    result = run_o2_cold_dm_writer(
        product_id="prod-1", research_report=_research_report(), leads=leads,
        campaign_build_id=None, lead_source="linkedin_engager",
        supabase_client=fake_db, anthropic_client=anthropic_client,
    )

    assert result == {"status": "ready_for_hitl", "sequences_written": 1}
    inserts = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "insert"]
    row = inserts[0]._payload[0]
    assert row["linkedin_lead_id"] == "li-lead-2"
    assert row["lead_source"] == "linkedin_engager"

    call = anthropic_client.messages.calls[0]
    assert "already engaged" in call["system"]
    user_prompt = call["messages"][0]["content"]
    assert "comment" in user_prompt
    assert "AI agent guardrails" in user_prompt


def test_lead_finder_source_uses_lead_finder_lead_id_and_standard_prompt():
    fake_db = FakeSupabase(responses={
        "mse_dm_sequences": [{"id": "seq-1"}],
        "mse_leads": [{"id": "lf-lead-1"}],
        "mse_icp_configs": [{"selling_stage": "active"}],
    })
    anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
    leads = [{"id": "lf-lead-1", "first_name": "Alex", "title": "Broker", "company": "Sun Realty"}]

    result = run_o2_cold_dm_writer(
        product_id="prod-1", research_report=_research_report(), leads=leads,
        campaign_build_id=None, lead_source="lead_finder",
        supabase_client=fake_db, anthropic_client=anthropic_client,
    )

    assert result == {"status": "ready_for_hitl", "sequences_written": 1}
    inserts = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "insert"]
    row = inserts[0]._payload[0]
    assert row["lead_finder_lead_id"] == "lf-lead-1"
    assert "lead_id" not in row
    assert "linkedin_lead_id" not in row
    assert row["lead_source"] == "lead_finder"

    # lead_finder's two-stage lifecycle: mse_leads.status advances to
    # 'pending_email' once a sequence has been drafted for it.
    lead_updates = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "update"]
    assert lead_updates[0]._payload == {"status": "pending_email"}
    assert ("id", "lf-lead-1") in lead_updates[0]._filters

    # lead_finder uses the standard system prompt -- no engager-specific fields.
    system_prompt = anthropic_client.messages.calls[0]["system"]
    assert "already engaged" not in system_prompt


class TestSellingStageGate:
    """Marketing stage-gate update (session 2026-09-15). A product whose
    mse_icp_configs.selling_stage isn't 'active' must never get a DM
    sequence written -- no LLM call, no mse_dm_sequences insert -- since
    that row is exactly what the HITL approval queue reads."""

    def test_warming_product_skips_write_entirely(self):
        fake_db = FakeSupabase(responses={"mse_icp_configs": [{"selling_stage": "warming"}]})
        anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
        leads = [{"id": "lead-1", "first_name": "Jane", "title": "Team Lead", "company": "Acme"}]

        result = run_o2_cold_dm_writer(
            product_id="prod-warming", research_report=_research_report(), leads=leads,
            campaign_build_id=None, lead_source="apollo",
            supabase_client=fake_db, anthropic_client=anthropic_client,
        )

        assert result == {"status": "stage_gated", "sequences_written": 0}
        assert anthropic_client.messages.calls == []
        inserts = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "insert"]
        assert inserts == []

    def test_building_product_skips_write_entirely(self):
        fake_db = FakeSupabase(responses={"mse_icp_configs": [{"selling_stage": "building"}]})
        anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
        leads = [{"id": "lead-1", "first_name": "Jane", "title": "Team Lead", "company": "Acme"}]

        result = run_o2_cold_dm_writer(
            product_id="prod-building", research_report=_research_report(), leads=leads,
            campaign_build_id=None, lead_source="lead_finder",
            supabase_client=fake_db, anthropic_client=anthropic_client,
        )

        assert result == {"status": "stage_gated", "sequences_written": 0}
        assert anthropic_client.messages.calls == []

    def test_no_icp_config_row_defaults_to_building_and_skips(self):
        """No mse_icp_configs row at all for a product must fail closed
        (skip), matching the column's own DB default, not be silently
        treated as active."""
        fake_db = FakeSupabase(responses={})
        anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
        leads = [{"id": "lead-1", "first_name": "Jane", "title": "Team Lead", "company": "Acme"}]

        result = run_o2_cold_dm_writer(
            product_id="prod-unknown", research_report=_research_report(), leads=leads,
            campaign_build_id=None, lead_source="apollo",
            supabase_client=fake_db, anthropic_client=anthropic_client,
        )

        assert result == {"status": "stage_gated", "sequences_written": 0}

    def test_stage_gate_skip_is_audited(self):
        fake_db = FakeSupabase(responses={"mse_icp_configs": [{"selling_stage": "warming"}]})
        anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])
        leads = [{"id": "lead-1", "first_name": "Jane", "title": "Team Lead", "company": "Acme"}]

        run_o2_cold_dm_writer(
            product_id="prod-warming", research_report=_research_report(), leads=leads,
            campaign_build_id=None, lead_source="apollo",
            supabase_client=fake_db, anthropic_client=anthropic_client,
        )

        audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
        assert audits[0]._payload["metadata"]["skipped"] == "selling_stage_not_active"
        assert audits[0]._payload["metadata"]["selling_stage"] == "warming"


def test_run_o2_for_linkedin_leads_pulls_lead_finder_leads_filtered_to_verified_email(monkeypatch):
    # Same FakeSupabase limitation noted above -- .eq("email_status",
    # "verified") isn't actually enforced by the fake, so this proves the
    # query issues that filter (the thing MKT-O2's own code is responsible
    # for), not that unverified rows get excluded end-to-end.
    fake_db = FakeSupabase(responses={
        "mse_linkedin_leads": [],
        "mse_leads": [{"id": "lf-lead-1", "product_id": "prod-1", "status": "pending_dm", "email_status": "verified"}],
    })
    calls: list[str] = []

    def fake_writer(*, product_id, research_report, leads, campaign_build_id, lead_source, supabase_client, anthropic_client=None):
        calls.append(lead_source)
        return {"sequences_written": len(leads)}

    monkeypatch.setattr(mkt_o2_module, "run_o2_cold_dm_writer", fake_writer)

    result = mkt_o2_module.run_o2_for_linkedin_leads(
        product_id="prod-1", research_report=_research_report(), supabase_client=fake_db,
    )

    assert calls == ["lead_finder"]
    assert result["by_source"] == {"linkedin_engager": 0, "linkedin_manual": 0, "lead_finder": 1}

    lead_finder_select = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "select"][0]
    assert ("email_status", "verified") in lead_finder_select._filters
    assert ("status", "pending_dm") in lead_finder_select._filters


def test_run_o2_for_linkedin_leads_processes_engagers_before_manual(monkeypatch):
    # tests/conftest.py's FakeSupabase doesn't apply .eq() filters when
    # producing a select's result_data (see test_mkt_li_intake.py's module
    # docstring) -- both the linkedin_engager-scoped and linkedin_manual-
    # scoped fetches inside run_o2_for_linkedin_leads would return the same
    # seeded rows either way, so that alone can't prove ordering. Mocking
    # out run_o2_cold_dm_writer itself and recording call order proves the
    # thing that actually matters: engager leads are handed to the writer
    # before manual leads, regardless of what the DB layer returns.
    fake_db = FakeSupabase(responses={
        "mse_linkedin_leads": [{"id": "some-lead", "product_id": "prod-1", "status": "pending_dm", "source": "linkedin_engager"}],
    })
    calls: list[str] = []

    def fake_writer(*, product_id, research_report, leads, campaign_build_id, lead_source, supabase_client, anthropic_client=None):
        calls.append(lead_source)
        return {"sequences_written": len(leads)}

    monkeypatch.setattr(mkt_o2_module, "run_o2_cold_dm_writer", fake_writer)

    result = mkt_o2_module.run_o2_for_linkedin_leads(
        product_id="prod-1", research_report=_research_report(), supabase_client=fake_db,
    )

    assert calls == ["linkedin_engager", "linkedin_manual"]
    # lead_finder is 0 here since mse_leads isn't seeded in this test --
    # run_o2_for_linkedin_leads now also checks it (2026-08-14).
    assert result["by_source"] == {"linkedin_engager": 1, "linkedin_manual": 1, "lead_finder": 0}
