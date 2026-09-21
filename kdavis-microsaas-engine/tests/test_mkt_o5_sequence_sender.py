from datetime import datetime, timedelta, timezone

from agents.marketing.mkt_o5_sequence_sender import (
    run_send_touch_1,
    run_send_touch_2,
    run_sequence_sender,
)
from core.email_compliance import generate_unsubscribe_token


class FakeResendEmails:
    def __init__(self):
        self.sent = []

    def send(self, params):
        self.sent.append(params)
        return {"id": "email_test"}


class FakeResend:
    def __init__(self):
        self.Emails = FakeResendEmails()


def _update_to(fake_db, status):
    """Finds the mse_dm_sequences update() call that set this exact status
    -- claim-then-confirm (Finding 1 fix) and the expiry-void check
    (Finding 2 fix) both add update() calls ahead of the final one, so
    positional indexing (updates[0]) is no longer meaningful; look up by
    the status value instead."""
    updates = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c._payload and c._payload.get("status") == status]
    assert updates, f"no mse_dm_sequences update set status={status!r}; saw {[c._payload for c in fake_db.executed if c.table_name == 'mse_dm_sequences' and c._payload]}"
    return updates[0]


def _seed_sequence(fake_db, status="approved_hitl", touch_1_sent_at=None, lead_id="lead-1"):
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-1",
        "lead_id": lead_id,
        "product_id": "prod-1",
        "campaign_build_id": "camp-1",
        "touch_1": "You're leaving $4k/mo on the table with manual invoicing.",
        "touch_2": "Following up — still leaving that $4k/mo on the table?",
        "status": status,
        "touch_1_sent_at": touch_1_sent_at,
    }]
    # Marketing stage-gate update (session 2026-09-15): MKT-O5 now checks
    # mse_icp_configs.selling_stage before sending -- every test in this
    # file uses product_id "prod-1", so seeding one active row here covers
    # all of them without needing per-test changes.
    _seed_active_stage(fake_db, product_id="prod-1")


def _seed_active_stage(fake_db, product_id="prod-1"):
    fake_db.responses["mse_icp_configs"] = [{"product_id": product_id, "selling_stage": "active"}]


def test_touch_1_sends_email_and_updates_status(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 1, "failed": [], "skipped": []}
    assert len(fake_resend.Emails.sent) == 1
    assert fake_resend.Emails.sent[0]["to"] == "lead@example.com"
    assert fake_resend.Emails.sent[0]["subject"] == "Quick question, Jamie"
    assert "4k/mo" in fake_resend.Emails.sent[0]["text"]

    # RFC 8058 one-click unsubscribe headers (2026-09-21) -- every
    # marketing send must carry these, not just the in-body link.
    headers = fake_resend.Emails.sent[0]["headers"]
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "marketing/unsubscribe" in headers["List-Unsubscribe"]
    assert generate_unsubscribe_token("lead@example.com") in headers["List-Unsubscribe"]

    final_update = _update_to(fake_db, "touch_1_sent")
    assert final_update._payload["touch_1_sent_at"] is not None

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "win"


def test_touch_1_appends_compliance_footer_with_address_and_unsubscribe_link(fake_db):
    """CAN-SPAM requires a physical mailing address and a working opt-out
    in every commercial email -- regression guard for the 2026-08-12 gap
    where neither existed anywhere in the send path."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    sent_body = fake_resend.Emails.sent[0]["text"]
    assert "[test address]" in sent_body  # COMPLIANCE_MAILING_ADDRESS, conftest.py
    assert "/marketing/unsubscribe?email=lead%40example.com" in sent_body
    token = generate_unsubscribe_token("lead@example.com")
    assert f"token={token}" in sent_body


def test_touch_1_skips_suppressed_lead_without_sending(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_db.responses["mse_email_suppressions"] = [{"id": "sup-1", "email": "lead@example.com"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": [], "skipped": ["seq-1"]}
    assert len(fake_resend.Emails.sent) == 0

    _update_to(fake_db, "suppressed")

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"
    assert audits[0]._payload["metadata"]["skipped"] == "suppressed"


def test_touch_1_stops_sending_once_daily_cap_reached(fake_db, monkeypatch):
    fake_db.responses["mse_dm_sequences"] = [
        {"id": "seq-1", "lead_id": "lead-1", "product_id": "prod-1", "campaign_build_id": "camp-1",
         "touch_1": "msg 1", "touch_2": "follow 1", "status": "approved_hitl", "touch_1_sent_at": None},
        {"id": "seq-2", "lead_id": "lead-2", "product_id": "prod-1", "campaign_build_id": "camp-1",
         "touch_1": "msg 2", "touch_2": "follow 2", "status": "approved_hitl", "touch_1_sent_at": None},
    ]
    _seed_active_stage(fake_db)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    monkeypatch.setenv("MARKETING_DAILY_SEND_CAP", "0")
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": [], "skipped": ["seq-1", "seq-2"]}
    assert len(fake_resend.Emails.sent) == 0
    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert all(a._payload["metadata"].get("skipped") == "daily_cap" for a in audits)


def test_touch_1_skips_lead_with_no_email_but_continues(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": None, "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": ["seq-1"], "skipped": []}
    assert len(fake_resend.Emails.sent) == 0

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"
    assert "No email on file" in audits[0]._payload["metadata"]["error"]


def test_touch_1_skips_lead_that_no_longer_exists(fake_db):
    """Regression test for a real bug found 2026-07-17: maybe_single().execute()
    returns bare None (not a Response with .data=None) when zero rows match
    — a deleted/missing lead_id crashed this with an unhandled AttributeError
    instead of being caught by the existing "no email on file" handling."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = []  # lead_id doesn't exist at all
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": ["seq-1"], "skipped": []}
    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"


def test_touch_2_query_filters_on_status_and_cadence_cutoff(fake_db):
    """The FakeQuery doesn't actually filter its canned response based on
    .eq()/.lte() calls (that's Postgres's job against the real DB) — this
    asserts run_send_touch_2 issues the right filters, i.e. it only ever
    asks for touch_1_sent rows old enough to be due, rather than checking
    end-to-end filtering behavior a fake DB can't meaningfully provide."""
    old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _seed_sequence(fake_db, status="touch_1_sent", touch_1_sent_at=old_enough)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]

    run_send_touch_2(supabase_client=fake_db, resend_client=FakeResend())

    select_query = fake_db.executed[0]
    assert ("status", "touch_1_sent") in select_query._filters
    lte_calls = [c for c in select_query.calls if c[0] == "lte"]
    assert len(lte_calls) == 1
    assert lte_calls[0][1] == "touch_1_sent_at"


def test_touch_2_sends_and_completes_sequence(fake_db):
    old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _seed_sequence(fake_db, status="touch_1_sent", touch_1_sent_at=old_enough)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_send_touch_2(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 1, "failed": [], "skipped": []}
    assert fake_resend.Emails.sent[0]["subject"] == "Following up, Jamie"

    final_update = _update_to(fake_db, "sequence_complete")
    assert final_update._payload["touch_2_sent_at"] is not None


def test_touch_2_skips_lead_who_unsubscribed_after_touch_1(fake_db):
    """The whole point of checking suppression at both touches, not once:
    someone can unsubscribe in the 3-day gap between touch_1 and touch_2."""
    old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _seed_sequence(fake_db, status="touch_1_sent", touch_1_sent_at=old_enough)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_db.responses["mse_email_suppressions"] = [{"id": "sup-1", "email": "lead@example.com"}]
    fake_resend = FakeResend()

    result = run_send_touch_2(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": [], "skipped": ["seq-1"]}
    assert len(fake_resend.Emails.sent) == 0


def test_touch_1_resolves_lead_finder_lead_via_mse_leads(fake_db):
    """lead_finder sequences carry lead_finder_lead_id instead of lead_id
    (mse_dm_sequences' 3-way one-lead-ref constraint) — _get_lead must look
    in mse_leads, not mse_apollo_leads, for these."""
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-lf-1",
        "lead_id": None,
        "lead_finder_lead_id": "lf-lead-1",
        "product_id": "prod-1",
        "campaign_build_id": None,
        "touch_1": "You're leaving $4k/mo on the table with manual scheduling.",
        "touch_2": "Following up — still leaving that $4k/mo on the table?",
        "status": "approved_hitl",
        "touch_1_sent_at": None,
    }]
    _seed_active_stage(fake_db)
    fake_db.responses["mse_leads"] = [{"email": "verified@example.com", "first_name": "Alex"}]
    fake_db.responses["mse_apollo_leads"] = []  # must not be consulted for this sequence
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 1, "failed": [], "skipped": []}
    assert fake_resend.Emails.sent[0]["to"] == "verified@example.com"

    lead_selects = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "select"]
    assert len(lead_selects) == 1
    assert ("id", "lf-lead-1") in lead_selects[0]._filters


def test_touch_1_advances_lead_finder_lead_to_contacted_and_logs_activity(fake_db):
    """DIST Phase 8 close-the-loop wiring: a successful touch_1 send for a
    lead_finder-sourced sequence must move mse_leads.stage new -> contacted
    and record an mse_activities row -- this is the only automated stage
    transition in the pipeline (there is no real reply/bounce signal to
    justify moving a lead further than this without a human)."""
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-lf-3",
        "lead_id": None,
        "lead_finder_lead_id": "lf-lead-3",
        "product_id": "prod-1",
        "campaign_build_id": None,
        "touch_1": "msg",
        "touch_2": "follow-up",
        "status": "approved_hitl",
        "touch_1_sent_at": None,
    }]
    _seed_active_stage(fake_db)
    fake_db.responses["mse_leads"] = [{"email": "verified@example.com", "first_name": "Alex"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)
    assert result == {"sent": 1, "failed": [], "skipped": []}

    stage_update = [
        c for c in fake_db.executed
        if c.table_name == "mse_leads" and c.calls[0][0] == "update" and c._payload.get("stage") == "contacted"
    ]
    assert len(stage_update) == 1
    assert ("id", "lf-lead-3") in stage_update[0]._filters
    assert ("stage", "new") in stage_update[0]._filters  # non-regressing: only advances from 'new'

    activity_inserts = [c for c in fake_db.executed if c.table_name == "mse_activities" and c.calls[0][0] == "insert"]
    assert activity_inserts[0]._payload["subject_id"] == "lf-lead-3"
    assert activity_inserts[0]._payload["kind"] == "outreach_sent"
    assert activity_inserts[0]._payload["body"] == "touch_1 sent"


def test_touch_2_logs_activity_without_changing_stage(fake_db):
    """touch_2 must not overwrite a stage a human may have already advanced
    (e.g. to 'qualified') -- it only refreshes last_activity_at and logs
    the touch, never sets 'stage' in its update payload."""
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-lf-4",
        "lead_id": None,
        "lead_finder_lead_id": "lf-lead-4",
        "product_id": "prod-1",
        "campaign_build_id": None,
        "touch_1": "msg",
        "touch_2": "follow-up",
        "status": "touch_1_sent",
        "touch_1_sent_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
    }]
    _seed_active_stage(fake_db)
    fake_db.responses["mse_leads"] = [{"email": "verified@example.com", "first_name": "Alex"}]
    fake_resend = FakeResend()

    result = run_send_touch_2(supabase_client=fake_db, resend_client=fake_resend)
    assert result == {"sent": 1, "failed": [], "skipped": []}

    lead_updates = [c for c in fake_db.executed if c.table_name == "mse_leads" and c.calls[0][0] == "update"]
    assert len(lead_updates) == 1
    assert "stage" not in lead_updates[0]._payload

    activity_inserts = [c for c in fake_db.executed if c.table_name == "mse_activities" and c.calls[0][0] == "insert"]
    assert activity_inserts[0]._payload["body"] == "touch_2 sent"


def test_touch_1_skips_mse_leads_activity_wiring_for_apollo_sequences(fake_db):
    """A sequence with lead_id (apollo-sourced) instead of
    lead_finder_lead_id must never touch mse_leads/mse_activities --
    apollo leads live in mse_apollo_leads, which has no stage column."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)
    assert result == {"sent": 1, "failed": [], "skipped": []}

    assert "mse_leads" not in fake_db.tables_touched
    assert "mse_activities" not in fake_db.tables_touched


def test_touch_1_fails_lead_finder_sequence_with_no_verified_email_on_lead(fake_db):
    """A lead_finder lead with no email on file (email_status never reached
    'verified', so mse_leads.email is still null) must fail loudly, not
    silently send to nothing — same "No email on file" guard apollo gets."""
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-lf-2",
        "lead_id": None,
        "lead_finder_lead_id": "lf-lead-2",
        "product_id": "prod-1",
        "campaign_build_id": None,
        "touch_1": "msg",
        "touch_2": "follow",
        "status": "approved_hitl",
        "touch_1_sent_at": None,
    }]
    _seed_active_stage(fake_db)
    fake_db.responses["mse_leads"] = [{"email": None, "first_name": "Alex"}]
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": ["seq-lf-2"], "skipped": []}
    assert len(fake_resend.Emails.sent) == 0
    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert "No email on file" in audits[0]._payload["metadata"]["error"]


# ── Finding 1 (2026-09-02 HITL audit): atomic claim, no double-send ─────

def test_touch_1_claims_row_with_conditional_update_before_sending(fake_db):
    """The claim must be a real conditional UPDATE (id + prior status),
    matching what real Postgres needs to make it atomic under concurrent
    callers -- not just an unconditional status flip."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    claim = _update_to(fake_db, "touch_1_sending")
    assert ("id", "seq-1") in claim._filters
    assert ("status", "approved_hitl") in claim._filters
    assert len(fake_resend.Emails.sent) == 1  # the send still happens once the claim succeeds
    final_update = _update_to(fake_db, "touch_1_sent")
    # Ordering: claim, then send (implicit -- the fake can't order against
    # an external call), then confirm. The confirm must come after the claim.
    assert fake_db.executed.index(claim) < fake_db.executed.index(final_update)


def test_touch_1_skips_without_sending_when_claim_is_lost_to_a_concurrent_run(fake_db):
    """Simulates the exact race Finding 1 was about: another (concurrent
    or retried) run already claimed this row between our SELECT and our
    UPDATE. A real conditional UPDATE would affect zero rows on Postgres;
    next_update_returns_empty simulates that for the fake. Must not send,
    must not error -- a lost claim is an expected outcome, not a failure."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_db.next_update_returns_empty.add(("mse_dm_sequences", "touch_1_sending"))
    fake_resend = FakeResend()

    result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

    assert result == {"sent": 0, "failed": [], "skipped": ["seq-1"]}
    assert len(fake_resend.Emails.sent) == 0  # the actual point: never touched Resend

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["metadata"]["skipped"] == "already_claimed"


def test_touch_1_reverts_claim_when_send_raises_so_it_can_retry_later(fake_db):
    """A send failure AFTER a successful claim must not strand the row at
    the transient 'touch_1_sending' status forever -- it has to go back to
    'approved_hitl' so a later run picks it up again."""
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]

    class ExplodingResend:
        class Emails:
            @staticmethod
            def send(params):
                raise RuntimeError("Resend API timeout")

    result = run_send_touch_1(supabase_client=fake_db, resend_client=ExplodingResend())

    assert result == {"sent": 0, "failed": ["seq-1"], "skipped": []}
    revert = _update_to(fake_db, "approved_hitl")
    assert ("status", "touch_1_sending") in revert._filters

    audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
    assert audits[0]._payload["outcome"] == "lose"
    assert "Resend API timeout" in audits[0]._payload["metadata"]["error"]


def test_touch_2_claim_and_revert_use_touch_1_sent_as_the_base_status(fake_db):
    """touch_2's claim/release pair must key off touch_1_sent, not
    approved_hitl -- a copy-paste of touch_1's constants here would let a
    touch_2 claim succeed against a row that was never actually sent
    touch_1, or fail to revert correctly on a touch_2 send failure."""
    old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    _seed_sequence(fake_db, status="touch_1_sent", touch_1_sent_at=old_enough)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]

    run_send_touch_2(supabase_client=fake_db, resend_client=FakeResend())

    claim = _update_to(fake_db, "touch_2_sending")
    assert ("status", "touch_1_sent") in claim._filters


# ── Finding 2 (2026-09-02 HITL audit): stale approvals void, not fire ───

def test_touch_1_voids_expired_approvals_before_processing_the_batch(fake_db):
    """An approval past its expiry must be voided to 'approval_expired' via
    a real conditional UPDATE (status='approved_hitl' AND
    hitl_approved_expires_at < now) before the send loop runs at all --
    this is the "stale approvals fire late" gap the audit found, closed.
    (The fake DB always returns the same canned rows regardless of which
    filters a query applied -- same limitation noted on the existing
    test_touch_2_query_filters_on_status_and_cadence_cutoff test above --
    so this asserts the void call and its filters are issued correctly,
    which is what proves the fix exists; real Postgres enforcing those
    filters is what makes it actually work in production.)"""
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-1", "lead_id": "lead-1", "product_id": "prod-1", "campaign_build_id": "camp-1",
        "touch_1": "msg", "touch_2": "follow", "status": "approved_hitl", "touch_1_sent_at": None,
        "hitl_approved_expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    }]
    _seed_active_stage(fake_db)
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]

    run_send_touch_1(supabase_client=fake_db, resend_client=FakeResend())

    void_call = _update_to(fake_db, "approval_expired")
    assert ("status", "approved_hitl") in void_call._filters
    lt_calls = [c for c in void_call.calls if c[0] == "lt"]
    assert lt_calls and lt_calls[0][1] == "hitl_approved_expires_at"
    # The void call must be issued before anything else touches this table.
    assert fake_db.executed.index(void_call) == 0


def test_run_sequence_sender_runs_both_stages(fake_db):
    _seed_sequence(fake_db, status="approved_hitl")
    fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
    fake_resend = FakeResend()

    result = run_sequence_sender(supabase_client=fake_db, resend_client=fake_resend)

    assert "touch_1" in result and "touch_2" in result
    assert result["touch_1"]["sent"] == 1


class TestSellingStageGateAtSendTime:
    """Marketing stage-gate update (session 2026-09-15). MKT-O2 gates at
    write time, but a product's stage can change after a sequence was
    already approved and before it sends -- MKT-O5 re-checks at send
    time so that later stage change still stops the send."""

    def test_touch_1_skips_sequence_for_non_active_product(self, fake_db):
        fake_db.responses["mse_dm_sequences"] = [{
            "id": "seq-1", "lead_id": "lead-1", "product_id": "prod-warming", "campaign_build_id": "camp-1",
            "touch_1": "msg", "touch_2": "follow", "status": "approved_hitl", "touch_1_sent_at": None,
        }]
        fake_db.responses["mse_icp_configs"] = [{"product_id": "prod-warming", "selling_stage": "warming"}]
        fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
        fake_resend = FakeResend()

        result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

        assert result == {"sent": 0, "failed": [], "skipped": ["seq-1"]}
        assert len(fake_resend.Emails.sent) == 0
        audits = [c for c in fake_db.executed if c.table_name == "audit_log" and c.calls[0][0] == "insert"]
        assert audits[0]._payload["metadata"]["skipped"] == "selling_stage_not_active"

    def test_touch_2_skips_sequence_for_non_active_product(self, fake_db):
        old_enough = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        fake_db.responses["mse_dm_sequences"] = [{
            "id": "seq-1", "lead_id": "lead-1", "product_id": "prod-building", "campaign_build_id": "camp-1",
            "touch_1": "msg", "touch_2": "follow", "status": "touch_1_sent", "touch_1_sent_at": old_enough,
        }]
        fake_db.responses["mse_icp_configs"] = [{"product_id": "prod-building", "selling_stage": "building"}]
        fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
        fake_resend = FakeResend()

        result = run_send_touch_2(supabase_client=fake_db, resend_client=fake_resend)

        assert result == {"sent": 0, "failed": [], "skipped": ["seq-1"]}
        assert len(fake_resend.Emails.sent) == 0

    def test_touch_1_sends_when_product_is_active(self, fake_db):
        """Positive control -- proves the gate isn't just failing closed
        by accident (e.g. an exception swallowed somewhere)."""
        _seed_sequence(fake_db, status="approved_hitl")
        fake_db.responses["mse_apollo_leads"] = [{"email": "lead@example.com", "first_name": "Jamie"}]
        fake_resend = FakeResend()

        result = run_send_touch_1(supabase_client=fake_db, resend_client=fake_resend)

        assert result == {"sent": 1, "failed": [], "skipped": []}
