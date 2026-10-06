"""Route-aware copy (Kelvin's decision 1, 2026-10-06).

The bug this replaces: copy was selected by lead_source (provenance) and the
channel was baked into whichever prompt that happened to pick. Consulting
leads got a LinkedIn sequence -- touch_1 a "connection request note", touch_2
"sent 3 days after the connection request is accepted" -- and scraper v2
routed them to outbound_email, so MKT-O5 EMAILED that text. The copy described
a channel it was not delivered on.

So the guards here are on both sides: the channel selection, and the delivered
TEXT. A prompt asking for email is not evidence that the model wrote email.
"""

import pytest

from agents.marketing.outreach_copy import (
    CLOUD_DECODED_OFFER,
    CONSULTING_OFFER,
    EMAIL_CHANNEL,
    LINKEDIN_CHANNEL,
    LINKEDIN_NOTE_MAX,
    ROUTE_EMAIL,
    ROUTE_LINKEDIN,
    build_system_prompt,
    channel_for_route,
    channel_violations,
)

# ── Channel selection ────────────────────────────────────────────────────

def test_email_route_selects_the_email_channel():
    assert channel_for_route("outbound_email") is EMAIL_CHANNEL


def test_linkedin_route_selects_the_linkedin_channel():
    assert channel_for_route("manual_linkedin") is LINKEDIN_CHANNEL


@pytest.mark.parametrize("route", [None, "", "   ", "reject", "something_new"])
def test_an_unknown_route_falls_back_to_the_manual_channel(route):
    """Getting it wrong on LinkedIn costs a human one confused read; defaulting
    to email would hand an unroutable lead to the automated sender."""
    assert channel_for_route(route) is LINKEDIN_CHANNEL


# ── Channel shape ────────────────────────────────────────────────────────

def test_only_the_email_channel_asks_for_a_subject():
    assert EMAIL_CHANNEL.wants_subject is True
    assert LINKEDIN_CHANNEL.wants_subject is False


def test_the_connection_note_respects_linkedins_hard_limit():
    """300 chars is a platform limit, not a style choice: over it, the note
    cannot be sent at all."""
    assert LINKEDIN_CHANNEL.limits["touch_1"] == LINKEDIN_NOTE_MAX == 300


def test_the_email_opener_is_not_capped_at_a_linkedin_limit():
    """The old 300-char cap was LinkedIn's, applied to email, and trimming at
    it deleted Earnin's closing URL and ask."""
    assert EMAIL_CHANNEL.limits["touch_1"] > LINKEDIN_NOTE_MAX


def test_both_channels_run_three_touches_on_the_same_cadence():
    assert EMAIL_CHANNEL.touches == LINKEDIN_CHANNEL.touches == ("touch_1", "touch_2", "touch_3")
    for ch in (EMAIL_CHANNEL, LINKEDIN_CHANNEL):
        assert "3 DAYS" in ch.instructions.upper()
        assert "5 DAYS" in ch.instructions.upper()
        assert "no reply" in ch.instructions.lower()


# ── Prompt composition ───────────────────────────────────────────────────

def test_the_email_prompt_forbids_linkedin_mechanics():
    p = build_system_prompt(EMAIL_CHANNEL, CONSULTING_OFFER)
    assert "EMAIL sequence" in p
    assert "connection request" in p.lower()      # named in the prohibition
    assert "FORBIDDEN VOCABULARY" in p
    assert "subject =" in p


def test_the_linkedin_prompt_asks_for_a_connection_note_and_no_subject():
    p = build_system_prompt(LINKEDIN_CHANNEL, CONSULTING_OFFER)
    assert "CONNECTION REQUEST NOTE" in p
    assert "AFTER THE CONNECTION IS ACCEPTED" in p
    assert '"subject"' not in p, "the LinkedIn channel has no subject"


def test_every_prompt_carries_the_shared_factual_rules():
    """A rule fixed on one channel and left wrong on the other is exactly what
    happened when the Boeing correction landed in the consulting prompt while
    the Cloud Decoded prompt kept its own copy."""
    for ch in (EMAIL_CHANNEL, LINKEDIN_CHANNEL):
        for offer in (CONSULTING_OFFER, CLOUD_DECODED_OFFER):
            p = build_system_prompt(ch, offer)
            assert "NDT inspection" in p
            assert "FIRST PERSON ONLY" in p
            assert "FIRST NAME" in p
            assert "FedRAMP" in p


def test_each_offer_closes_to_its_own_url_on_both_channels():
    for ch in (EMAIL_CHANNEL, LINKEDIN_CHANNEL):
        consulting = build_system_prompt(ch, CONSULTING_OFFER)
        cloud = build_system_prompt(ch, CLOUD_DECODED_OFFER)
        assert CONSULTING_OFFER.close_url in consulting
        assert CLOUD_DECODED_OFFER.close_url not in consulting
        assert CLOUD_DECODED_OFFER.close_url in cloud
        assert CONSULTING_OFFER.close_url not in cloud


# ── THE output guard: LinkedIn phrasing in email copy ────────────────────

@pytest.mark.parametrize("text", [
    "Once you accept my connection request I'll share more.",
    "Sent you a connection request earlier this week.",
    "Happy to connect with you on LinkedIn.",
    "Once we're connected I'll send the details.",
    "After you accept, I'll follow up.",
    "Shot you a DM as well.",
])
def test_linkedin_phrasing_fails_on_the_email_route(text):
    """The real defect, in test form: this is the vocabulary that was being
    emailed."""
    problems = channel_violations(text, ROUTE_EMAIL)
    assert problems, f"no violation found in {text!r}"
    assert all("outbound_email" in p for p in problems)


def test_the_exact_live_phrasing_is_caught():
    """Verbatim from the prompt that was generating emailed copy."""
    live = "sent 3 days after the connection request is accepted"
    assert channel_violations(live, ROUTE_EMAIL)


@pytest.mark.parametrize("text", [
    "Cory — saw Onebrief is hiring a Senior SRE. Worth a 20-minute call?",
    "I do cloud and platform engineering, currently at CorVel.",
    "Following up on my note from a few days ago.",
])
def test_clean_email_copy_passes(text):
    assert channel_violations(text, ROUTE_EMAIL) == []


@pytest.mark.parametrize("text", [
    "Check your inbox for the subject line I used.",
    "Reply to this email if that's useful.",
    "You can unsubscribe at any time.",
])
def test_email_phrasing_fails_on_the_linkedin_route(text):
    problems = channel_violations(text, ROUTE_LINKEDIN)
    assert problems, f"no violation found in {text!r}"


def test_linkedin_copy_may_reference_connecting():
    """On the LinkedIn route that genuinely happened, so it must NOT be a
    violation -- the ban is channel-specific, not a global word filter."""
    assert channel_violations(
        "Thanks for connecting — following up on the SRE posting.", ROUTE_LINKEDIN) == []
    assert channel_violations(
        "Since we connected I've been thinking about your platform work.", ROUTE_LINKEDIN) == []


def test_the_dm_check_does_not_fire_inside_other_words():
    """A substring check would flag "admin", "random" and "freedom"."""
    for safe in ("Your admin team", "a random sample", "freedom to choose",
                 "the DMZ configuration"):
        assert channel_violations(safe, ROUTE_EMAIL) == [], safe


def test_a_linkedin_url_in_email_copy_is_caught():
    """Linking to a LinkedIn profile from a cold email is still a LinkedIn
    mention and reads as copy written for the wrong channel."""
    assert channel_violations("See linkedin.com/in/kelvin for background", ROUTE_EMAIL)


# ── The writer honours the route ─────────────────────────────────────────

def test_the_writer_selects_by_route_not_lead_source():
    import inspect
    from agents.marketing import mkt_o2_cold_dm_writer as o2
    src = inspect.getsource(o2.run_o2_cold_dm_writer)
    assert "_write_route_aware_sequence(" in src
    # The two lead_source-selected prompts must no longer drive the choice.
    assert 'lead_source == "job_posting_signal"' not in src, \
        "channel is still being chosen from provenance"
    assert 'lead_source == "cloud_decoded_job_signal"' not in src


def test_the_writer_rejects_cross_channel_drift():
    """Raising, not scrubbing: the wrong channel's vocabulary usually means the
    whole message is framed wrong, and find-and-replace would leave incoherent
    copy behind."""
    import inspect
    from agents.marketing import mkt_o2_cold_dm_writer as o2
    src = inspect.getsource(o2._write_route_aware_sequence)
    assert "channel_violations(" in src
    assert "raise ValueError" in src


def test_a_route_change_regenerates_the_draft():
    import inspect
    from agents.marketing import mkt_o2_cold_dm_writer as o2
    src = inspect.getsource(o2.run_o2_cold_dm_writer)
    assert "drafted_for_route" in src
    assert "route changed" in src


def test_mkt_o5_prefers_the_approved_subject():
    """The subject was the only prospect-facing text no human reviewed."""
    import inspect
    from agents.marketing import mkt_o5_sequence_sender as o5
    src = inspect.getsource(o5.run_send_touch_1)
    assert 'seq.get("subject")' in src
    idx_stored = src.index('seq.get("subject")')
    idx_default = src.index("Quick question")
    assert idx_stored < idx_default, "the invented subject must only be a fallback"
