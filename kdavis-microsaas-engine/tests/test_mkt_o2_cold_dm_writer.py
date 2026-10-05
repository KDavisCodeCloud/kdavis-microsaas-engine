import pytest


# ── Consulting outreach must have a destination (2026-10-02) ─────────────
# Every stored consulting touch contained no link at all, so an interested
# prospect had nowhere to go but a reply. thdagentic.com already exists and
# is live; the copy just never pointed at it.

def test_consulting_prompt_carries_the_live_offer_url():
    from agents.marketing.mkt_o2_cold_dm_writer import (
        CONSULTING_OFFER_URL, _INFRA_CONSULTING_SYSTEM_PROMPT,
    )
    assert CONSULTING_OFFER_URL == "https://thdagentic.com"
    assert CONSULTING_OFFER_URL in _INFRA_CONSULTING_SYSTEM_PROMPT


def test_the_url_is_scoped_to_touch_2_only():
    """touch_1's own spec is "no pitch, no ask", and a link in a first cold
    email raises spam scoring while the domain is still in warmup."""
    from agents.marketing.mkt_o2_cold_dm_writer import _INFRA_CONSULTING_SYSTEM_PROMPT as p
    assert "Do NOT put a URL in touch_1 or touch_3" in p
    # The instruction must sit in the touch_2 paragraph, not touch_1's.
    t1 = p.index("touch_1 = LinkedIn CONNECTION REQUEST NOTE")
    t2 = p.index("touch_2 = sent 3 days after")
    t3 = p.index("touch_3 = sent 5 days after")
    url_at = p.index("https://thdagentic.com")
    assert t2 < url_at < t3, "the URL instruction must live in the touch_2 section"
    assert not (t1 < url_at < t2)


def test_no_tracking_parameters_are_requested():
    """A bare URL: tracking params on a cold email are both a spam signal and
    a consent problem we have not asked for."""
    from agents.marketing.mkt_o2_cold_dm_writer import _INFRA_CONSULTING_SYSTEM_PROMPT as p
    assert "no tracking parameters" in p
    assert "utm_" not in p


# ── Contact-first gate (Kelvin's decision 2, 2026-10-05) ─────────────────
# Six of eleven pending sequences were addressed to people contact_fit
# rejects, because contact_status='found' only ever meant "a name was
# discovered", never "the buyer was found".

from agents.marketing.mkt_o2_cold_dm_writer import contact_is_draftable


def _lead(**kw):
    base = {"id": "l1", "company": "Acme", "contact_status": "found",
            "first_name": "Cory", "last_name": "Ondrejka",
            "title": "Chief Technology Officer", "open_role_count": 3}
    base.update(kw)
    return base


def test_a_fit_accepted_cto_is_draftable():
    ok, reason = contact_is_draftable(_lead())
    assert ok is True
    assert "executive technology title" in reason


@pytest.mark.parametrize("title", [
    "Vice President of Marketing",          # Ogury
    "director of Software Development",     # Alongside
    "Director, IT Applications Architecture",  # MeridianLink
    "COO",                                  # Clutch
    "Director",                             # SingleStore, bare
])
def test_the_six_real_wrong_buyers_are_all_parked(title):
    """Every title that actually reached the approval queue on 2026-10-05."""
    ok, reason = contact_is_draftable(_lead(title=title))
    assert ok is False, f"{title!r} should not be draftable"
    assert "contact fit rejected" in reason


@pytest.mark.parametrize("status", ["pending", "none_found", "needs_review", "", None])
def test_only_contact_status_found_is_draftable(status):
    ok, reason = contact_is_draftable(_lead(contact_status=status))
    assert ok is False
    assert "need 'found'" in reason


def test_a_missing_first_name_parks_the_lead():
    """The copy addresses the contact by first name, so a nameless contact
    cannot produce a sendable draft."""
    ok, reason = contact_is_draftable(_lead(first_name=None))
    assert ok is False
    assert "first name" in reason


def test_a_missing_title_parks_rather_than_assuming_fit():
    ok, reason = contact_is_draftable(_lead(title=""))
    assert ok is False
    assert "contact fit cannot be assessed" in reason


def test_a_founder_at_a_large_company_is_parked():
    """The founder size gate still applies at draft time."""
    ok, _ = contact_is_draftable(_lead(title="CEO", open_role_count=40))
    assert ok is False


def test_the_writer_parks_instead_of_drafting(fake_db, monkeypatch):
    """The gate must stop the LLM call entirely -- not draft and then discard,
    which would still spend tokens and still create a row to clean up."""
    import agents.marketing.mkt_o2_cold_dm_writer as o2
    fake_db.responses["mse_icp_configs"] = [{"product_id": "p1", "selling_stage": "active"}]
    fake_db.responses["mse_dm_sequences"] = [{"id": "seq-new"}]
    calls = []
    monkeypatch.setattr(o2, "_write_infra_consulting_dm_for_lead",
                        lambda lead, **kw: calls.append(lead) or {"touch_1": "a", "touch_2": "b"})

    result = o2.run_o2_cold_dm_writer(
        product_id="p1", research_report={}, campaign_build_id=None,
        leads=[_lead(id="bad", title="COO"), _lead(id="good")],
        lead_source="job_posting_signal", supabase_client=fake_db,
    )

    drafted = [l["id"] for l in calls]
    assert drafted == ["good"], f"drafted {drafted}; the COO must never reach the LLM"
    assert result["sequences_written"] == 1


def test_a_parked_sequence_regenerates_in_place(fake_db, monkeypatch):
    """"They regenerate automatically once I add the buyer" -- and the
    sequence id must stay stable so existing links still resolve."""
    import agents.marketing.mkt_o2_cold_dm_writer as o2
    fake_db.responses["mse_icp_configs"] = [{"product_id": "p1", "selling_stage": "active"}]
    fake_db.responses["mse_dm_sequences"] = [
        {"id": "seq-parked", "status": "awaiting_contact", "lead_finder_lead_id": "l1"},
    ]
    monkeypatch.setattr(o2, "_write_infra_consulting_dm_for_lead",
                        lambda lead, **kw: {"touch_1": "new1", "touch_2": "new2"})

    o2.run_o2_cold_dm_writer(
        product_id="p1", research_report={}, campaign_build_id=None,
        leads=[_lead(id="l1")], lead_source="job_posting_signal", supabase_client=fake_db,
    )

    updates = [c for c in fake_db.executed
               if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "update"
               and (c._payload or {}).get("status") == "pending_hitl"]
    assert updates, "the parked row must be updated back to pending_hitl"
    assert updates[0]._payload["touch_1"] == "new1"
    assert updates[0]._payload["rejection_reason"] is None, "the parked note must be cleared"
    inserts = [c for c in fake_db.executed
               if c.table_name == "mse_dm_sequences" and c.calls[0][0] == "insert"]
    assert not inserts, "regeneration must not insert a duplicate"


def test_a_lead_with_a_live_sequence_is_not_drafted_twice(fake_db, monkeypatch):
    """The real duplicate: TWO Clutch sequences existed for one lead."""
    import agents.marketing.mkt_o2_cold_dm_writer as o2
    fake_db.responses["mse_icp_configs"] = [{"product_id": "p1", "selling_stage": "active"}]
    fake_db.responses["mse_dm_sequences"] = [
        {"id": "seq-live", "status": "pending_hitl", "lead_finder_lead_id": "l1"},
    ]
    calls = []
    monkeypatch.setattr(o2, "_write_infra_consulting_dm_for_lead",
                        lambda lead, **kw: calls.append(lead) or {"touch_1": "a", "touch_2": "b"})

    result = o2.run_o2_cold_dm_writer(
        product_id="p1", research_report={}, campaign_build_id=None,
        leads=[_lead(id="l1")], lead_source="job_posting_signal", supabase_client=fake_db,
    )
    assert calls == [], "must not redraft a lead that already has a live sequence"
    assert result["sequences_written"] == 0


def test_a_rejected_sequence_does_not_block_a_fresh_draft(fake_db, monkeypatch):
    """Terminal statuses must not permanently lock a company out."""
    import agents.marketing.mkt_o2_cold_dm_writer as o2
    fake_db.responses["mse_icp_configs"] = [{"product_id": "p1", "selling_stage": "active"}]
    fake_db.responses["mse_dm_sequences"] = [
        {"id": "seq-old", "status": "rejected_hitl", "lead_finder_lead_id": "l1"},
    ]
    calls = []
    monkeypatch.setattr(o2, "_write_infra_consulting_dm_for_lead",
                        lambda lead, **kw: calls.append(lead) or {"touch_1": "a", "touch_2": "b"})
    o2.run_o2_cold_dm_writer(
        product_id="p1", research_report={}, campaign_build_id=None,
        leads=[_lead(id="l1")], lead_source="job_posting_signal", supabase_client=fake_db,
    )
    assert len(calls) == 1, "a rejected sequence must not block a new draft"
