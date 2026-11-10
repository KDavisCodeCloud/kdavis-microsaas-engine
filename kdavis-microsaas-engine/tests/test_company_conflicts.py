"""One company, one product (Kelvin, 2026-11-09)."""

import pytest
from fastapi.testclient import TestClient

import api.routers.buyer_research as buyer_research
import api.routers.outreach as outreach_router
import api.routers.outreach_lanes as lanes
from api.main import app
from core.company_conflicts import company_key, conflict_block_reason, is_committed
from tests.test_outreach_routes import _auth_header

client = TestClient(app)
AUTH = {"Authorization": "Bearer test-marketing-api-key"}

CD = "777a1852-f84c-49d8-890e-cd14670b7f6f"
CONSULTING = "9b6c8f36-985f-4d7a-a416-c40da89e23af"
PARTNERS = "c0e8aaf3-e858-4df4-b0ba-863580556a18"


@pytest.mark.parametrize("domain", ["sandboxaq.com", "www.sandboxaq.com", "sandboxaq.com:",
                                    "https://sandboxaq.com/careers", "SandboxAQ.com"])
def test_domain_variants_are_one_company(domain):
    assert company_key("SandboxAQ", domain) == "sandboxaq.com"


def test_name_is_the_fallback_when_no_domain():
    assert company_key("Polygon Labs", None) == company_key("polygon-labs", "") == "name:polygonlabs"
    assert company_key(None, None) is None


@pytest.mark.parametrize("status,committed", [
    ("pending_hitl", False), ("rejected_hitl", False), ("archived_wrong_icp", False),
    ("approved_hitl", True), ("approved_manual", True), ("touch_1_sent", True),
    ("replied", True), ("sequence_complete", True),
])
def test_what_counts_as_claimed(status, committed):
    assert is_committed(status) is committed


def _seed(fake_db, *, other_status="pending_hitl", other_product=CONSULTING, other_domain="www.freedompay.com:"):
    # Row order matters: the fake applies no .eq() filters, so maybe_single()
    # lookups get the FIRST row -- our own lead / our own sequence.
    fake_db.responses["mse_leads"] = [
        {"id": "lead-cd", "product_id": CD, "company": "Freedompay", "domain": "freedompay.com",
         "first_name": "Ana", "last_name": "Ruiz", "title": "CTO", "lead_route": "outbound_email",
         "email": "ana@freedompay.com", "email_grade": "valid", "status": "qualified",
         "contact_status": "found"},
        {"id": "lead-con", "product_id": other_product, "company": "FreedomPay Inc", "domain": other_domain,
         "status": "qualified", "contact_status": "found"},
    ]
    fake_db.responses["mse_dm_sequences"] = [
        {"id": "seq-cd", "status": "pending_hitl", "product_id": CD, "lead_finder_lead_id": "lead-cd",
         "lead_source": "job_posting_signal", "touch_1": "hi"},
        {"id": "seq-con", "status": other_status, "product_id": other_product,
         "lead_finder_lead_id": "lead-con", "lead_source": "job_posting_signal", "touch_1": "hi"},
    ]
    fake_db.responses["mse_products"] = [
        {"id": CD, "name": "Cloud Decoded"},
        {"id": CONSULTING, "name": "THD Agentic Systems Consulting"},
        {"id": PARTNERS, "name": "Referral Partners"},
    ]


def _card(fake_db, monkeypatch, sequence_id="seq-cd"):
    monkeypatch.setattr(lanes, "get_supabase", lambda: fake_db)
    drafts = client.get("/marketing/outreach/drafts", headers=AUTH).json()["drafts"]
    return next(d for d in drafts if d["sequence_id"] == sequence_id)


def test_a_pending_draft_under_the_other_product_is_shown_but_not_blocking(fake_db, monkeypatch):
    _seed(fake_db)
    card = _card(fake_db, monkeypatch)
    assert card["company_conflicts"] == [{
        "product_id": CONSULTING, "product": "THD Agentic Systems Consulting", "lead_id": "lead-con",
        "sequence_id": "seq-con", "status": "pending_hitl", "committed": False,
    }]
    assert card["conflict_block_reason"] is None


def test_once_the_other_product_is_approved_this_one_is_blocked(fake_db, monkeypatch):
    _seed(fake_db, other_status="approved_hitl")
    card = _card(fake_db, monkeypatch)
    assert card["company_conflicts"][0]["committed"] is True
    assert "Already approved under THD Agentic Systems Consulting" in card["conflict_block_reason"]


def test_a_rejected_card_elsewhere_is_no_conflict(fake_db, monkeypatch):
    _seed(fake_db, other_status="rejected_hitl")
    card = _card(fake_db, monkeypatch)
    assert card["company_conflicts"] == []
    assert card["conflict_block_reason"] is None


def test_a_different_company_is_no_conflict(fake_db, monkeypatch):
    _seed(fake_db, other_status="approved_hitl", other_domain="freedompaylabs.io")
    assert _card(fake_db, monkeypatch)["company_conflicts"] == []


def test_the_partner_lane_is_never_compared(fake_db, monkeypatch):
    _seed(fake_db, other_status="approved_manual", other_product=PARTNERS)
    assert _card(fake_db, monkeypatch)["company_conflicts"] == []


def test_approve_endpoint_refuses_a_second_product(fake_db, monkeypatch):
    """The greyed-out button is a convenience; this is the enforcement."""
    _seed(fake_db, other_status="touch_1_sent")
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)
    resp = client.post("/outreach/dm-sequences/seq-cd/approve",
                       json={"resolved_by": "kelvin"}, headers=_auth_header())
    assert resp.status_code == 409
    assert "One product per company" in resp.json()["detail"]
    assert not [c for c in fake_db.executed
                if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"]


def test_approve_endpoint_allows_the_first_of_two_pending(fake_db, monkeypatch):
    _seed(fake_db, other_status="pending_hitl")
    monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)
    resp = client.post("/outreach/dm-sequences/seq-cd/approve",
                       json={"resolved_by": "kelvin"}, headers=_auth_header())
    assert resp.status_code == 200


def test_conflict_reason_names_the_claiming_product():
    assert conflict_block_reason([{"committed": False, "product": "X"}]) is None
    msg = conflict_block_reason([{"committed": True, "product": "Cloud Decoded", "status": "approved_manual"}])
    assert msg.startswith("Already approved under Cloud Decoded (approved manual)")


def test_find_the_buyer_task_carries_product_and_conflicts(fake_db, monkeypatch):
    _seed(fake_db, other_status="approved_hitl")
    fake_db.responses["mse_leads"][0]["contact_status"] = "pending"
    monkeypatch.setattr(buyer_research, "get_supabase", lambda: fake_db)
    tasks = client.get("/marketing/buyer-research", headers=AUTH).json()["tasks"]
    task = next(t for t in tasks if t["lead_id"] == "lead-cd")
    assert task["product"] == "Cloud Decoded"
    assert task["company_conflicts"][0]["product"] == "THD Agentic Systems Consulting"
    assert task["company_conflicts"][0]["committed"] is True
