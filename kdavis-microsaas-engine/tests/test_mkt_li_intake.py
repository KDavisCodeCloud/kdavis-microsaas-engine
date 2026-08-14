"""
MKT-LI Intake — dedup on linkedin_url, source tagging, CSV parsing.

Note on this repo's FakeSupabase (tests/conftest.py): .execute() always
returns whatever's currently seeded in responses[table_name], regardless
of whether the call was a select or an insert -- it doesn't distinguish
"state before" from "state after" a write. Since run_li_intake reads
mse_linkedin_leads (for dedup) and then writes to it in the same call,
tests that want a non-empty insert "return" seed one unrelated existing
row (a different linkedin_url than anything being submitted) so it plays
both roles safely: a real existing lead for the dedup check, and a
believable non-empty echo for the insert's own result.data. The actual
correctness check for what was inserted always reads the real submitted
payload off fake_db.executed, never the fake's echoed return value.
"""
import pytest

from agents.marketing.mkt_li_intake import parse_csv_leads, run_li_intake
from tests.conftest import FakeSupabase

_UNRELATED_EXISTING_ROW = {"id": "existing-row", "linkedin_url": "https://linkedin.com/in/someone-else"}


def test_run_li_intake_adds_new_leads_and_tags_source():
    fake_db = FakeSupabase(responses={"mse_linkedin_leads": [_UNRELATED_EXISTING_ROW]})
    leads = [
        {"first_name": "Jane", "last_name": "Doe", "title": "Team Lead", "company": "Acme Realty", "linkedin_url": "https://linkedin.com/in/janedoe", "location": "Phoenix, AZ"},
    ]

    result = run_li_intake(leads=leads, source="linkedin_manual", product_id="prod-1", supabase_client=fake_db)

    assert result == {"added": 1, "duplicates_skipped": 0}
    inserts = [c for c in fake_db.executed if c.table_name == "mse_linkedin_leads" and c.calls[0][0] == "insert"]
    payload = inserts[0]._payload[0]
    assert payload["source"] == "linkedin_manual"
    assert payload["status"] == "pending_dm"
    assert payload["product_id"] == "prod-1"
    assert payload["linkedin_url"] == "https://linkedin.com/in/janedoe"


def test_run_li_intake_dedupes_against_existing_leads():
    fake_db = FakeSupabase(responses={
        "mse_linkedin_leads": [{"linkedin_url": "https://linkedin.com/in/janedoe"}],
    })
    leads = [{"first_name": "Jane", "linkedin_url": "https://linkedin.com/in/janedoe"}]

    result = run_li_intake(leads=leads, source="linkedin_manual", supabase_client=fake_db)

    assert result == {"added": 0, "duplicates_skipped": 1}
    inserts = [c for c in fake_db.executed if c.table_name == "mse_linkedin_leads" and c.calls[0][0] == "insert"]
    assert inserts == []


def test_run_li_intake_dedupes_within_the_same_batch():
    fake_db = FakeSupabase(responses={"mse_linkedin_leads": [_UNRELATED_EXISTING_ROW]})
    leads = [
        {"first_name": "Jane", "linkedin_url": "https://linkedin.com/in/janedoe"},
        {"first_name": "Jane Again", "linkedin_url": "https://linkedin.com/in/janedoe"},
    ]

    result = run_li_intake(leads=leads, source="linkedin_manual", supabase_client=fake_db)

    assert result == {"added": 1, "duplicates_skipped": 1}
    inserts = [c for c in fake_db.executed if c.table_name == "mse_linkedin_leads" and c.calls[0][0] == "insert"]
    # Only one row was actually submitted for insert, despite two in the batch.
    assert len(inserts[0]._payload) == 1


def test_run_li_intake_skips_rows_with_no_linkedin_url():
    fake_db = FakeSupabase(responses={"mse_linkedin_leads": []})
    leads = [{"first_name": "No URL"}]

    result = run_li_intake(leads=leads, source="linkedin_manual", supabase_client=fake_db)

    assert result == {"added": 0, "duplicates_skipped": 0}


def test_run_li_intake_rejects_invalid_source():
    fake_db = FakeSupabase(responses={"mse_linkedin_leads": []})
    with pytest.raises(ValueError):
        run_li_intake(leads=[], source="apollo", supabase_client=fake_db)


def test_run_li_intake_tags_engager_context():
    fake_db = FakeSupabase(responses={"mse_linkedin_leads": [_UNRELATED_EXISTING_ROW]})
    leads = [{
        "first_name": "Sam", "linkedin_url": "https://linkedin.com/in/sam",
        "source_post_url": "https://linkedin.com/posts/123", "interaction_type": "comment",
        "interaction_note": "AI agent guardrails",
    }]

    result = run_li_intake(leads=leads, source="linkedin_engager", supabase_client=fake_db)

    assert result["added"] == 1
    inserts = [c for c in fake_db.executed if c.table_name == "mse_linkedin_leads" and c.calls[0][0] == "insert"]
    payload = inserts[0]._payload[0]
    assert payload["source"] == "linkedin_engager"
    assert payload["interaction_type"] == "comment"
    assert payload["interaction_note"] == "AI agent guardrails"


def test_parse_csv_leads():
    raw = "first_name,last_name,title,company,linkedin_url,location\nJane,Doe,Team Lead,Acme Realty,https://linkedin.com/in/janedoe,Phoenix AZ\n"
    leads = parse_csv_leads(raw)
    assert leads == [{
        "first_name": "Jane", "last_name": "Doe", "title": "Team Lead",
        "company": "Acme Realty", "linkedin_url": "https://linkedin.com/in/janedoe", "location": "Phoenix AZ",
    }]
