"""
agents/marketing/mkt_o3_email_sequence_loader.py — provider swap
(systeme.io -> Brevo, 2026-08-14). Covers the drafting path (unchanged,
minus the removed systeme.io push) and the new enroll_trial_in_sequence
per-signup entry point. brevo_client.create_or_update_contact is mocked
at the module-attribute level (patched on the mkt_o3 module, not
core.brevo_client, so we assert exactly what MKT-O3 itself passes in).
"""
import json
from unittest.mock import MagicMock

import agents.marketing.mkt_o3_email_sequence_loader as mkt_o3
from core.brevo_client import BrevoContactResult
from tests.conftest import FakeSupabase

SEQUENCE_JSON = json.dumps({
    "emails": [
        {"day": 0, "subject": "Welcome", "body": "Here's how to get started."},
        {"day": 2, "subject": "The scheduling problem", "body": "You mentioned double-booked showings..."},
        {"day": 4, "subject": "One more thing", "body": "..."},
        {"day": 7, "subject": "Halfway through your trial", "body": "..."},
        {"day": 14, "subject": "Trial ending soon", "body": "..."},
    ]
})


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return type("Msg", (), {"content": [type("Block", (), {"text": self._responses.pop(0)})()]})()


class FakeAnthropic:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _research_report():
    return {"pain_language": [{"phrase": "double-booked showings"}], "proof_signals": [], "willingness_to_pay_band": "$50-100/mo"}


# ── drafting path (unchanged logic, systeme.io push removed) ──────────

def test_run_o3_drafts_and_saves_sequence_without_any_systeme_io_call():
    fake_db = FakeSupabase(responses={
        "mse_email_sequences": [{"id": "seq-1"}],
        "campaign_builds": [{"id": "cb-1"}],
    })
    anthropic_client = FakeAnthropic(responses=[SEQUENCE_JSON])

    result = mkt_o3.run_o3_email_sequence_loader(
        product_id="prod-1", research_report=_research_report(), campaign_build_id="cb-1",
        supabase_client=fake_db, anthropic_client=anthropic_client,
    )

    assert result == {"status": "ready_for_hitl", "sequence_id": "seq-1", "email_count": 5}

    inserts = [c for c in fake_db.executed if c.table_name == "mse_email_sequences" and c.calls[0][0] == "insert"]
    assert inserts[0]._payload["status"] == "pending_hitl"
    assert len(inserts[0]._payload["emails"]) == 5

    # The whole point of this provider swap: email_sequence_status must
    # actually reach 'ready_for_hitl' now -- under the old systeme.io
    # path this always landed on 'failed' since that push always 404'd.
    cb_updates = [c for c in fake_db.executed if c.table_name == "campaign_builds" and c.calls[0][0] == "update"]
    assert cb_updates[0]._payload == {"email_sequence_status": "ready_for_hitl"}


def test_systeme_io_client_is_flagged_deprecated_and_unused_by_active_flow():
    assert mkt_o3.DEPRECATED is True
    assert mkt_o3._SystemeIOClient is not None  # retained for reference, not deleted


# ── enroll_trial_in_sequence ───────────────────────────────────────────

def _seed_enroll_db(list_id=7, sequence_id="seq-1", suppressed=False):
    return FakeSupabase(responses={
        "mse_email_suppressions": [{"id": "sup-1"}] if suppressed else [],
        "mse_brevo_lists": [{"brevo_list_id": list_id}],
        "mse_email_sequences": [{"id": sequence_id}],
    })


def test_enroll_trial_in_sequence_calls_brevo_client_with_correct_list_id(monkeypatch):
    fake_db = _seed_enroll_db(list_id=42)
    fake_brevo_client = MagicMock()
    captured = {}

    def fake_create_or_update_contact(**kwargs):
        captured.update(kwargs)
        return BrevoContactResult(success=True, contact_id=99)

    monkeypatch.setattr(mkt_o3, "create_or_update_contact", fake_create_or_update_contact)

    result = mkt_o3.enroll_trial_in_sequence(
        product_id="prod-1", email="jane@example.com", first_name="Jane", last_name="Doe",
        plan_tier="solo", trial_start="2026-08-14",
        supabase_client=fake_db, brevo_client=fake_brevo_client,
    )

    assert result.status == "enrolled"
    assert result.enrolled is True
    assert result.sequence_drafted is True
    assert result.brevo_list_id == 42
    assert captured["email"] == "jane@example.com"
    assert captured["list_ids"] == [42]
    assert captured["attributes"]["product_id"] == "prod-1"
    assert captured["attributes"]["plan_tier"] == "solo"
    assert captured["brevo_client"] is fake_brevo_client


def test_enroll_trial_in_sequence_does_not_enroll_suppressed_emails(monkeypatch):
    fake_db = _seed_enroll_db(suppressed=True)
    called = {"count": 0}

    def fake_create_or_update_contact(**kwargs):
        called["count"] += 1
        return BrevoContactResult(success=True, contact_id=1)

    monkeypatch.setattr(mkt_o3, "create_or_update_contact", fake_create_or_update_contact)

    result = mkt_o3.enroll_trial_in_sequence(
        product_id="prod-1", email="suppressed@example.com", first_name="Jane", last_name="Doe",
        plan_tier="solo", trial_start="2026-08-14", supabase_client=fake_db,
    )

    assert result.status == "suppressed"
    assert result.enrolled is False
    assert called["count"] == 0  # brevo_client never touched


def test_enroll_trial_in_sequence_fails_cleanly_without_registered_list():
    fake_db = FakeSupabase(responses={"mse_email_suppressions": [], "mse_brevo_lists": []})

    result = mkt_o3.enroll_trial_in_sequence(
        product_id="prod-1", email="jane@example.com", first_name="Jane", last_name="Doe",
        plan_tier="solo", trial_start="2026-08-14", supabase_client=fake_db,
    )

    assert result.status == "failed"
    assert result.enrolled is False
    assert "No Brevo list registered" in result.error


def test_enroll_trial_in_sequence_fails_cleanly_without_drafted_sequence():
    fake_db = FakeSupabase(responses={
        "mse_email_suppressions": [], "mse_brevo_lists": [{"brevo_list_id": 7}], "mse_email_sequences": [],
    })

    result = mkt_o3.enroll_trial_in_sequence(
        product_id="prod-1", email="jane@example.com", first_name="Jane", last_name="Doe",
        plan_tier="solo", trial_start="2026-08-14", supabase_client=fake_db,
    )

    assert result.status == "failed"
    assert result.sequence_drafted is False
    assert "No drafted trial-nurture sequence" in result.error


def test_enroll_trial_in_sequence_surfaces_brevo_failure_without_raising(monkeypatch):
    fake_db = _seed_enroll_db()
    monkeypatch.setattr(
        mkt_o3, "create_or_update_contact",
        lambda **k: BrevoContactResult(success=False, error="Attribute not found"),
    )

    result = mkt_o3.enroll_trial_in_sequence(
        product_id="prod-1", email="jane@example.com", first_name="Jane", last_name="Doe",
        plan_tier="solo", trial_start="2026-08-14", supabase_client=fake_db,
    )

    assert result.status == "failed"
    assert result.sequence_drafted is True
    assert result.enrolled is False
    assert result.error == "Attribute not found"
