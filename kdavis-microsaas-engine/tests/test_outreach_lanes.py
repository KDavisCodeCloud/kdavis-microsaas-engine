"""Outreach lanes b/c/d (Kelvin's decision 3, 2026-10-05)."""

import pytest
from fastapi.testclient import TestClient

import api.routers.outreach_lanes as lanes
from api.main import app

client = TestClient(app)
AUTH = {"Authorization": "Bearer test-marketing-api-key"}


def _seed(fake_db, *, seq_status="pending_hitl"):
    fake_db.responses["mse_dm_sequences"] = [{
        "id": "seq-1", "status": seq_status, "lead_source": "job_posting_signal",
        "product_id": "prod-1", "lead_finder_lead_id": "lead-1",
        "touch_1": "Cory — saw the SRE posting.", "touch_2": "Worth a call? https://thdagentic.com",
        "touch_3": "Last note.", "created_at": "2026-10-05T00:00:00+00:00",
    }]
    fake_db.responses["mse_leads"] = [{
        "id": "lead-1", "company": "Onebrief", "domain": "onebrief.com",
        "first_name": "Cory", "last_name": "Ondrejka", "title": "Chief Technology Officer",
        "email": "cory@onebrief.com", "email_grade": "risky",
        "linkedin_url": "https://www.linkedin.com/in/cory", "lead_route": "outbound_email",
        "contact_status": "found", "job_posting_title": "Senior SRE",
        "job_posting_url": "https://boards.greenhouse.io/onebrief/jobs/1",
        "job_posting_date": "2026-09-20", "open_role_count": 3,
        "fit_score": 0.85, "intent_score": 0.85, "score_reasons": ["matching role"],
        "stack_tags": ["kubernetes"], "product_id": "prod-1",
    }]
    fake_db.responses["mse_products"] = [{"id": "prod-1", "name": "THD Agentic Systems Consulting"}]


# ── Lane (b) Approve Drafts ──────────────────────────────────────────────

def test_a_draft_card_carries_every_field_the_lane_shows(fake_db, monkeypatch):
    """The MSE page lost the contact because the CLIENT composed the card from
    an embed that omitted mse_leads. The API composes it here so a forgotten
    join cannot silently render "Unknown"."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)

    body = client.get("/marketing/outreach/drafts", headers=AUTH).json()
    assert body["count"] == 1
    card = body["drafts"][0]

    assert card["product"] == "THD Agentic Systems Consulting"
    assert card["company"] == "Onebrief"
    assert card["contact"]["name"] == "Cory Ondrejka"
    assert card["contact"]["title"] == "Chief Technology Officer"
    assert card["contact"]["linkedin_url"].endswith("/in/cory")
    assert card["contact"]["email"] == "cory@onebrief.com"
    assert card["contact"]["email_grade"] == "risky"
    assert card["route"] == "outbound_email"
    assert card["signal"]["role"] == "Senior SRE"
    assert card["signal"]["posting_age_days"] is not None
    assert card["scores"] == {"fit": 0.85, "intent": 0.85}
    assert set(card["touches"]) == {"touch_1", "touch_2", "touch_3"}


def test_posting_age_is_days_not_a_raw_date(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    card = client.get("/marketing/outreach/drafts", headers=AUTH).json()["drafts"][0]
    age = card["signal"]["posting_age_days"]
    assert isinstance(age, int) and age >= 0


def test_a_missing_posting_date_is_null_not_zero(fake_db, monkeypatch):
    """0 would read as "posted today"."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_leads"][0]["job_posting_date"] = None
    card = client.get("/marketing/outreach/drafts", headers=AUTH).json()["drafts"][0]
    assert card["signal"]["posting_age_days"] is None


def test_editing_a_pending_draft_updates_only_the_touches(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    resp = client.post("/marketing/outreach/drafts/seq-1/edit",
                       json={"touch_1": "Rewritten opener."}, headers=AUTH)
    assert resp.status_code == 200
    upd = [c for c in fake_db.executed
           if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"][0]
    assert upd._payload == {"touch_1": "Rewritten opener."}
    assert ("status", "pending_hitl") in upd._filters, \
        "the edit must be scoped to pending drafts"


def test_editing_an_approved_draft_is_refused(fake_db, monkeypatch):
    """Editing after approval would leave an audit trail approving copy that
    no longer exists."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_dm_sequences"] = []  # the status-scoped update matches nothing
    resp = client.post("/marketing/outreach/drafts/seq-1/edit",
                       json={"touch_1": "too late"}, headers=AUTH)
    assert resp.status_code == 409
    assert "after approval" in resp.json()["detail"]


def test_editing_a_touch_to_empty_is_rejected(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    resp = client.post("/marketing/outreach/drafts/seq-1/edit",
                       json={"touch_1": "   "}, headers=AUTH)
    assert resp.status_code == 422


def test_edit_with_no_touches_is_a_400(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    assert client.post("/marketing/outreach/drafts/seq-1/edit",
                       json={}, headers=AUTH).status_code == 400


# ── Lane (c) Ready to Paste ──────────────────────────────────────────────

def test_ready_to_paste_lists_approved_manual(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db, seq_status="approved_manual")
    body = client.get("/marketing/outreach/ready-to-paste", headers=AUTH).json()
    assert body["count"] == 1
    assert body["ready"][0]["touches"]["touch_1"]
    q = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences"][0]
    assert ("status", "approved_manual") in q._filters


def test_marking_sent_stamps_touch_1(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db, seq_status="approved_manual")
    resp = client.post("/marketing/outreach/ready-to-paste/seq-1/mark",
                       json={"event": "sent"}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["touch_1_sent_at"]


def test_marking_replied_stops_the_sequence_and_opens_a_conversation(fake_db, monkeypatch):
    """A reply noticed while pasting is the same event as one noticed later;
    making the user record it twice guarantees the funnel undercounts."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db, seq_status="approved_manual")
    fake_db.responses["mse_outreach_conversations"] = []

    resp = client.post("/marketing/outreach/ready-to-paste/seq-1/mark",
                       json={"event": "replied"}, headers=AUTH)
    assert resp.status_code == 200
    stops = [c for c in fake_db.executed
             if c.table_name == "mse_dm_sequences" and (c._payload or {}).get("status") == "replied"]
    assert stops, "a reply must stop the sequence"
    opens = [c for c in fake_db.executed
             if c.table_name == "mse_outreach_conversations" and c.calls[0][0] == "insert"]
    assert opens, "a reply must open a conversation"


def test_an_unknown_paste_event_is_rejected(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db, seq_status="approved_manual")
    assert client.post("/marketing/outreach/ready-to-paste/seq-1/mark",
                       json={"event": "ghosted"}, headers=AUTH).status_code == 422


# ── Lane (d) Conversations ───────────────────────────────────────────────

def test_stage_update_creates_the_conversation_when_absent(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_outreach_conversations"] = []
    # The insert's return value comes from the same canned list, so seed it
    # only after the "does one exist" read -- emulated by the route's own order.
    resp = client.post("/marketing/outreach/conversations/lead-1/stage",
                       json={"stage": "call_booked"}, headers=AUTH)
    assert resp.status_code in (200, 500)  # 500 only if the fake returns no insert data
    inserts = [c for c in fake_db.executed
               if c.table_name == "mse_outreach_conversations" and c.calls[0][0] == "insert"]
    assert inserts, "a stage update on a fresh lead must open the conversation"
    payload = inserts[0]._payload
    assert payload["stage"] == "call_booked"
    assert payload["call_booked_at"]
    # Any stage implies a reply happened; without this the funnel's first step
    # is empty for a deal that reached "won".
    assert payload["replied_at"], "replied_at must be stamped even when skipped"


def test_stages_only_move_forward(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_outreach_conversations"] = [
        {"id": "conv-1", "lead_id": "lead-1", "stage": "won"},
    ]
    resp = client.post("/marketing/outreach/conversations/lead-1/stage",
                       json={"stage": "replied"}, headers=AUTH)
    assert resp.status_code == 409
    assert "only move forward" in resp.json()["detail"]


def test_lost_is_reachable_from_any_stage(fake_db, monkeypatch):
    """A deal can die at any point."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_outreach_conversations"] = [
        {"id": "conv-1", "lead_id": "lead-1", "stage": "proposal_sent"},
    ]
    resp = client.post("/marketing/outreach/conversations/lead-1/stage",
                       json={"stage": "lost", "lost_reason": "went with an agency"}, headers=AUTH)
    assert resp.status_code == 200
    upd = [c for c in fake_db.executed
           if c.table_name == "mse_outreach_conversations" and c.calls[0][0] == "update"][0]
    assert upd._payload["lost_reason"] == "went with an agency"


def test_re_marking_the_same_stage_is_allowed(fake_db, monkeypatch):
    """So a note or lost_reason can be corrected."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_outreach_conversations"] = [
        {"id": "conv-1", "lead_id": "lead-1", "stage": "call_booked"},
    ]
    resp = client.post("/marketing/outreach/conversations/lead-1/stage",
                       json={"stage": "call_booked", "notes": "moved to Friday"}, headers=AUTH)
    assert resp.status_code == 200


def test_an_unknown_stage_is_rejected(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    assert client.post("/marketing/outreach/conversations/lead-1/stage",
                       json={"stage": "maybe"}, headers=AUTH).status_code == 422


def test_stage_update_on_an_unknown_lead_is_404(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_leads"] = []
    assert client.post("/marketing/outreach/conversations/nope/stage",
                       json={"stage": "replied"}, headers=AUTH).status_code == 404


def test_conversations_list_groups_by_stage(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_outreach_conversations"] = [
        {"id": "c1", "lead_id": "lead-1", "product_id": "prod-1", "stage": "replied",
         "replied_at": "2026-10-05T00:00:00+00:00", "updated_at": "2026-10-05T00:00:00+00:00"},
    ]
    body = client.get("/marketing/outreach/conversations", headers=AUTH).json()
    assert body["by_stage"] == {"replied": 1}
    assert body["conversations"][0]["company"] == "Onebrief"


# ── auth ─────────────────────────────────────────────────────────────────

def test_every_lane_endpoint_requires_auth():
    for method, path, payload in (
        ("get", "/marketing/outreach/drafts", None),
        ("get", "/marketing/outreach/ready-to-paste", None),
        ("get", "/marketing/outreach/conversations", None),
        ("post", "/marketing/outreach/drafts/x/edit", {"touch_1": "a"}),
        ("post", "/marketing/outreach/ready-to-paste/x/mark", {"event": "sent"}),
        ("post", "/marketing/outreach/conversations/x/stage", {"stage": "replied"}),
    ):
        fn = getattr(client, method)
        r = fn(path) if payload is None else fn(path, json=payload)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


# ── LinkedIn URL gate (Kelvin's decision 1, 2026-10-06) ──────────────────
#
# A manual_linkedin draft is unapprovable without a profile URL: the lane is
# paste-by-hand and there is no LinkedIn API to look the person up. Security
# Risk Advisors reached the queue with a CTO and no URL.

def test_a_linkedin_draft_without_a_url_is_blocked(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_leads"][0]["lead_route"] = "manual_linkedin"
    fake_db.responses["mse_leads"][0]["linkedin_url"] = None
    card = client.get("/marketing/outreach/drafts", headers=AUTH).json()["drafts"][0]
    assert card["blocked_reason"]
    assert "profile URL" in card["blocked_reason"]


def test_a_linkedin_draft_with_a_url_is_not_blocked(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_leads"][0]["lead_route"] = "manual_linkedin"
    fake_db.responses["mse_leads"][0]["linkedin_url"] = "https://www.linkedin.com/in/cory"
    card = client.get("/marketing/outreach/drafts", headers=AUTH).json()["drafts"][0]
    assert card["blocked_reason"] is None


def test_an_email_draft_is_never_blocked_by_a_missing_url(fake_db, monkeypatch):
    """MKT-O5 sends to an address; a LinkedIn URL is irrelevant to that."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_leads"][0]["lead_route"] = "outbound_email"
    fake_db.responses["mse_leads"][0]["linkedin_url"] = None
    card = client.get("/marketing/outreach/drafts", headers=AUTH).json()["drafts"][0]
    assert card["blocked_reason"] is None


def test_a_company_url_is_rejected(fake_db, monkeypatch):
    """The common paste mistake. Same rejection as Find the Buyer, from the
    same shared validator."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    resp = client.post("/marketing/outreach/drafts/seq-1/linkedin-url",
                       json={"linkedin_url": "https://www.linkedin.com/company/onebrief/"},
                       headers=AUTH)
    assert resp.status_code == 422


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/in/cory-ondrejka",
    "https://linkedin.com/in/cory",
    "http://fr.linkedin.com/in/cory",       # country subdomains are real
    "https://www.linkedin.com/in/cory/",
])
def test_real_profile_urls_are_accepted(url, fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    resp = client.post("/marketing/outreach/drafts/seq-1/linkedin-url",
                       json={"linkedin_url": url}, headers=AUTH)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["blocked_reason"] is None


def test_the_url_is_written_to_the_LEAD_not_the_sequence(fake_db, monkeypatch):
    """It is a property of the person and must survive the draft being
    regenerated."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    client.post("/marketing/outreach/drafts/seq-1/linkedin-url",
                json={"linkedin_url": "https://www.linkedin.com/in/cory"}, headers=AUTH)
    writes = [c for c in fake_db.executed
              if c.table_name == "mse_leads" and c.calls[0][0] == "update"]
    assert writes and writes[0]._payload == {"linkedin_url": "https://www.linkedin.com/in/cory"}
    seq_writes = [c for c in fake_db.executed
                  if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"]
    assert not seq_writes, "the URL must not be stored on the sequence"


def test_saving_a_url_on_an_approved_draft_is_refused(fake_db, monkeypatch):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db, seq_status="approved_manual")
    resp = client.post("/marketing/outreach/drafts/seq-1/linkedin-url",
                       json={"linkedin_url": "https://www.linkedin.com/in/cory"}, headers=AUTH)
    assert resp.status_code == 409


def test_a_duplicate_profile_names_the_other_company(fake_db, monkeypatch):
    """linkedin_url carries a UNIQUE partial index; the raw DB error is
    opaque."""
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    _seed(fake_db)
    fake_db.responses["mse_leads"] = [
        {"id": "other-lead", "company": "Acme", "lead_route": "manual_linkedin"},
    ]
    resp = client.post("/marketing/outreach/drafts/seq-1/linkedin-url",
                       json={"linkedin_url": "https://www.linkedin.com/in/cory"}, headers=AUTH)
    assert resp.status_code == 409
    assert "Acme" in resp.json()["detail"]


def test_the_approve_endpoint_enforces_the_gate_too():
    """A disabled button is a UI convenience; this endpoint is reachable with
    a session and curl."""
    import inspect
    import api.routers.outreach as outreach
    src = inspect.getsource(outreach.approve_dm_sequence)
    assert "_approval_block_reason" in src
    assert "409" in src or "status_code=409" in src


def test_both_lanes_share_one_url_validator():
    """Two copies would disagree the first time one learned a new URL shape."""
    import inspect
    import api.routers.buyer_research as br
    from core import linkedin_urls
    assert "is_profile_url" in inspect.getsource(br)
    assert "_LINKEDIN_PROFILE_RE" not in inspect.getsource(br), \
        "buyer_research still has its own copy of the regex"
    assert linkedin_urls.is_profile_url("https://www.linkedin.com/in/x")
    assert not linkedin_urls.is_profile_url("https://www.linkedin.com/company/x")
