

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
