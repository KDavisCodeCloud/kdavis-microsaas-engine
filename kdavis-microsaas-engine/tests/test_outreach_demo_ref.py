"""
tests/test_outreach_demo_ref.py
Cloud Decoded GAPS #34 (2026-11-14): every outreach lead's demo link carries
?ref=<code> so theclouddecoded.com can attribute the demo visit, approvals,
signup and payment to that contact.
"""

import time

import jwt
import pytest
from fastapi.testclient import TestClient

import api.routers.outreach as outreach_router
from agents.marketing import mkt_o2_cold_dm_writer as writer
from agents.marketing.outreach_copy import (
    CLOUD_DECODED_DEMO_URL,
    LINKEDIN_NOTE_MAX,
    attach_demo_ref,
    attach_demo_ref_if_fits,
    demo_ref_code,
    demo_url_for_lead,
)
from api.main import app

LEAD = "3f2a9c1e-7b4d-4e2a-9c11-5d6e7f8a9b0c"
CODE = "r3f2a9c1e7b"
REF_URL = f"{CLOUD_DECODED_DEMO_URL}?ref={CODE}"
client = TestClient(app)


def _auth():
    tok = jwt.encode({"sub": "op", "app_metadata": {"role": "admin"}, "aud": "authenticated",
                      "exp": int(time.time()) + 3600}, "placeholder-jwt-secret", algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


class TestRefCode:
    def test_stable_short_code_from_uuid(self):
        assert demo_ref_code(LEAD) == CODE
        assert demo_ref_code(LEAD.upper()) == CODE
        assert len(CODE) == 11

    def test_code_matches_cloud_decoded_ref_rule(self):
        import re
        assert re.fullmatch(r"[A-Za-z0-9_-]{4,64}", CODE)  # core/funnel_events.py _REF_RE

    @pytest.mark.parametrize("bad", [None, "", "lead-1", "not-a-uuid-at-all", 42])
    def test_non_uuid_gives_no_code_and_plain_url(self, bad):
        assert demo_ref_code(bad) is None
        assert demo_url_for_lead(bad) == CLOUD_DECODED_DEMO_URL


class TestAttach:
    def test_bare_url_gets_ref_including_trailing_punctuation(self):
        text = f"Try it here: {CLOUD_DECODED_DEMO_URL}. Or ({CLOUD_DECODED_DEMO_URL})"
        assert attach_demo_ref(text, LEAD) == f"Try it here: {REF_URL}. Or ({REF_URL})"

    def test_idempotent(self):
        once = attach_demo_ref(f"See {CLOUD_DECODED_DEMO_URL}", LEAD)
        assert attach_demo_ref(once, LEAD) == once

    def test_hand_typed_ref_is_left_alone(self):
        text = f"{CLOUD_DECODED_DEMO_URL}?ref=manual123"
        assert attach_demo_ref(text, LEAD) == text

    def test_other_urls_untouched(self):
        text = "https://thdagentic.com and https://theclouddecoded.com/demos and https://theclouddecoded.com/"
        assert attach_demo_ref(text, LEAD) == text

    def test_no_code_no_change(self):
        text = f"See {CLOUD_DECODED_DEMO_URL}"
        assert attach_demo_ref(text, "lead-1") == text

    def test_if_fits_refuses_to_exceed_limit_and_never_trims(self):
        text = "x" * (LINKEDIN_NOTE_MAX - len(CLOUD_DECODED_DEMO_URL) - 1) + " " + CLOUD_DECODED_DEMO_URL
        assert len(text) == LINKEDIN_NOTE_MAX
        out, changed = attach_demo_ref_if_fits(text, LEAD, LINKEDIN_NOTE_MAX)
        assert (out, changed) == (text, False)


class TestGenerationFit:
    def test_fits_normally(self):
        assert writer._fit_demo_ref(f"Worth a look: {CLOUD_DECODED_DEMO_URL}", LEAD, 700) == f"Worth a look: {REF_URL}"

    def test_over_limit_trims_body_never_url(self):
        # A touch exactly at its limit: the ref'd URL can't fit without trimming.
        text = ("This is a sentence that fills space. " * 7) + "\n\n" + CLOUD_DECODED_DEMO_URL
        limit = len(text)
        out = writer._fit_demo_ref(text, LEAD, limit)
        assert out.endswith(REF_URL)                      # URL intact, ref attached
        assert len(out) <= limit                           # within the cap
        assert out.count(CLOUD_DECODED_DEMO_URL) == 1      # no duplicate link
        assert out.startswith("This is a sentence that fills space.")  # body trimmed at a sentence

    def test_no_demo_link_unchanged(self):
        assert writer._fit_demo_ref("No link here.", LEAD, 300) == "No link here."


class TestApproval:
    def _seq(self, **over):
        row = {"id": "seq-1", "status": "pending_hitl", "lead_source": "apollo", "lead_id": LEAD,
               "lead_finder_lead_id": None, "linkedin_lead_id": None, "product_id": "cloud-decoded",
               "drafted_for_route": "outbound_email",
               "touch_1": "Saw your posting about pipeline failures. Worth a quick look?",
               "touch_2": f"Here is the demo: {CLOUD_DECODED_DEMO_URL}",
               "touch_3": None}
        row.update(over)
        return row

    def test_existing_draft_gets_ref_in_the_guarded_update(self, monkeypatch, fake_db):
        fake_db.responses["mse_dm_sequences"] = [self._seq()]
        monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)
        resp = client.post("/outreach/dm-sequences/seq-1/approve", json={"resolved_by": "kelvin"}, headers=_auth())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["demo_ref"] == CODE and body["demo_ref_attached"] == ["touch_2"]
        upd = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"][0]
        assert upd._payload["touch_2"] == f"Here is the demo: {REF_URL}"
        assert "touch_1" not in upd._payload
        assert ("status", "pending_hitl") in upd._filters
        ev = [c for c in fake_db.executed if c.table_name == "agent_events"][0]
        assert CODE in ev._payload["action"]

    def test_linkedin_note_over_limit_keeps_plain_link(self, monkeypatch, fake_db):
        note = "x" * (LINKEDIN_NOTE_MAX - len(CLOUD_DECODED_DEMO_URL) - 1) + " " + CLOUD_DECODED_DEMO_URL
        fake_db.responses["mse_dm_sequences"] = [self._seq(drafted_for_route="manual_linkedin", lead_source="linkedin_manual",
                                                           touch_1=note, touch_2="No link.")]
        monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)
        resp = client.post("/outreach/dm-sequences/seq-1/approve", json={"resolved_by": "kelvin"}, headers=_auth())
        assert resp.status_code == 200, resp.text
        assert resp.json()["demo_ref_skipped_over_limit"] == ["touch_1"]
        upd = [c for c in fake_db.executed if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"][0]
        assert "touch_1" not in upd._payload

    def test_preview_shows_ref_for_pending_linkedin_draft(self, monkeypatch, fake_db):
        fake_db.responses["mse_dm_sequences"] = [self._seq(lead_source="linkedin_manual", drafted_for_route="manual_linkedin")]
        monkeypatch.setattr(outreach_router, "get_supabase", lambda: fake_db)
        resp = client.get("/outreach/dm-sequences/seq-1/preview", headers=_auth())
        assert resp.status_code == 200, resp.text
        assert resp.json()["touch_2"] == f"Here is the demo: {REF_URL}"


class TestLaneCard:
    def _card(self, fake_db, touch_2):
        from api.routers.outreach_lanes import _card

        fake_db.responses["mse_leads"] = [{"id": LEAD, "company": "Acme", "lead_route": "outbound_email"}]
        seq = {"id": "seq-1", "status": "pending_hitl", "lead_source": "lead_finder",
               "lead_finder_lead_id": LEAD, "product_id": "p1", "touch_1": "Hi", "touch_2": touch_2}
        return _card(fake_db, seq, {"p1": {"name": "Cloud Decoded"}})

    def test_card_carries_server_computed_ref_when_demo_link_present(self, fake_db):
        assert self._card(fake_db, f"Demo: {REF_URL}")["demo_ref"] == CODE

    def test_card_has_no_ref_without_a_demo_link(self, fake_db):
        assert self._card(fake_db, "https://thdagentic.com")["demo_ref"] is None
