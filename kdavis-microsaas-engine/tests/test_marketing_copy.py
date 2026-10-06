"""Copy guards for outbound marketing (Kelvin's decision 4, 2026-10-05).

TWO LAYERS, deliberately.

  1. PROMPT guards -- the instruction must be present and must not have been
     quietly reworded away. These are the cheap, fast regression tests.
  2. OUTPUT guards -- a reusable check (`copy_violations`) run against real
     generated text. A prompt rule is a request, not a guarantee: the model
     can still produce a third-person sentence or name Boeing. Anything that
     will be seen by a prospect gets checked, not just the prompt that asked
     for it.

The factual rule is the important one. Before today the consulting prompt
instructed the model to claim "production infrastructure work in aerospace and
other regulated environments (Boeing, Honeywell Aerospace)". That is false --
those were NDT inspection roles with cloud/sysadmin work alongside, and CorVel
is the cloud engineering role -- and it was going out in live outreach.
"""

import re

import pytest

from agents.marketing.mkt_o2_cold_dm_writer import (
    CLOUD_DECODED_DEMO_URL,
    CONSULTING_OFFER_URL,
    _CLOUD_DECODED_JOB_SIGNAL_SYSTEM_PROMPT,
    _INFRA_CONSULTING_SYSTEM_PROMPT,
)

PROSPECT_FACING_PROMPTS = {
    "consulting": _INFRA_CONSULTING_SYSTEM_PROMPT,
    "cloud_decoded": _CLOUD_DECODED_JOB_SIGNAL_SYSTEM_PROMPT,
}

# Compliance regimes the copy must never introduce on its own. Each one is a
# claim about a customer's legal obligations, and guessing wrong in a cold
# email to a CTO is worse than saying nothing.
COMPLIANCE_TERMS = ("FedRAMP", "IL2", "IL4", "IL5", "IL6", "SOC 2", "HIPAA",
                    "ITAR", "CMMC", "PCI")

_THIRD_PERSON_RE = re.compile(
    r"\bKelvin\s+(?:has|is|was|does|built|brings|spent|worked|leads|runs)\b"
    r"|\bKelvin's\b"
    r"|\b(?:he|his)\s+(?:background|experience|work|team)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(r"\b(?:hi|hey|hello)\s+(?:there|team|folks|all)\b", re.IGNORECASE)
# SCOPED inline flag, not re.IGNORECASE on the whole pattern: the company name
# must stay case-SENSITIVE ([A-Z] to mean "a capitalised name"), while the
# greeting itself is case-insensitive. A blanket re.IGNORECASE would make
# [A-Z] match lowercase and the class would stop meaning anything -- the exact
# trap that produced 15 bogus contacts from the name parser on 2026-10-01.
_TEAM_GREETING_RE = re.compile(r"\b(?i:hi|hey|hello)\s+[A-Z][\w.&-]*\s+(?i:team)\b")


def copy_violations(text: str, *, jd_text: str = "", first_name: str = "",
                    expect_first_name: bool = False) -> list[str]:
    """Every rule violation in one piece of prospect-facing copy.

    `jd_text` is the job posting the copy was derived from: a compliance term
    is only a violation when the posting did NOT name it.

    `expect_first_name` additionally requires the copy to actually ADDRESS the
    contact. Checking only for bad greetings was not enough -- the first
    regenerated batch avoided "Hi <Company> team" and then opened with a bare
    "Hi —" or no greeting at all, which satisfies the prohibition while still
    failing the instruction.
    """
    problems = []
    low = (text or "")

    for employer in ("Boeing", "Honeywell"):
        if re.search(rf"\b{employer}\b", low, re.IGNORECASE):
            problems.append(f"names {employer}, which was an NDT inspection role")

    if _THIRD_PERSON_RE.search(low):
        problems.append("refers to Kelvin in the third person; copy must be first person")

    if _TEAM_GREETING_RE.search(low):
        problems.append('greets a company rather than a person ("Hi <Company> team")')
    if _GREETING_RE.search(low):
        problems.append("uses a generic greeting instead of the contact's first name")

    if expect_first_name and first_name:
        if not re.search(rf"\b{re.escape(first_name)}\b", low, re.IGNORECASE):
            problems.append(f"does not address the contact by first name ({first_name})")

    for term in COMPLIANCE_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", low, re.IGNORECASE) and \
           not re.search(rf"\b{re.escape(term)}\b", jd_text or "", re.IGNORECASE):
            problems.append(f"introduces {term}, which the job posting does not name")

    return problems


# ── Layer 1: prompt guards ───────────────────────────────────────────────

@pytest.mark.parametrize("name", list(PROSPECT_FACING_PROMPTS))
def test_prompt_forbids_the_boeing_honeywell_claim(name):
    p = PROSPECT_FACING_PROMPTS[name]
    assert "NDT inspection" in p, f"{name}: the factual correction is missing"
    assert "CorVel is the cloud engineering role" in p
    assert "Do not" in p and "Boeing" in p


@pytest.mark.parametrize("name", list(PROSPECT_FACING_PROMPTS))
def test_prompt_never_asserts_infrastructure_work_at_the_aerospace_employers(name):
    """The exact phrasing that was live until 2026-10-05 must not return."""
    p = PROSPECT_FACING_PROMPTS[name]
    bad = re.search(
        r"production infrastructure work in aerospace|"
        r"background includes production infrastructure.{0,60}Boeing",
        p, re.IGNORECASE)
    assert bad is None, f"{name}: the false aerospace-infrastructure claim is back"


@pytest.mark.parametrize("name", list(PROSPECT_FACING_PROMPTS))
def test_prompt_requires_first_person(name):
    assert "FIRST PERSON ONLY" in PROSPECT_FACING_PROMPTS[name]


@pytest.mark.parametrize("name", list(PROSPECT_FACING_PROMPTS))
def test_prompt_requires_a_first_name_greeting(name):
    p = PROSPECT_FACING_PROMPTS[name]
    assert "FIRST NAME" in p
    assert "team" in p, "the 'Hi <company> team' prohibition is missing"


@pytest.mark.parametrize("name", list(PROSPECT_FACING_PROMPTS))
def test_prompt_forbids_leading_with_credentials(name):
    assert "Do NOT lead with employer names" in PROSPECT_FACING_PROMPTS[name]


@pytest.mark.parametrize("name", list(PROSPECT_FACING_PROMPTS))
def test_prompt_forbids_unsupported_compliance_claims(name):
    p = PROSPECT_FACING_PROMPTS[name]
    assert "FedRAMP" in p and "unless the posting" in p


def test_each_product_closes_to_its_own_url():
    assert CONSULTING_OFFER_URL == "https://thdagentic.com"
    assert CLOUD_DECODED_DEMO_URL == "https://theclouddecoded.com/demo"
    assert CONSULTING_OFFER_URL in _INFRA_CONSULTING_SYSTEM_PROMPT
    assert CLOUD_DECODED_DEMO_URL in _CLOUD_DECODED_JOB_SIGNAL_SYSTEM_PROMPT
    # Cross-contamination would send a consulting prospect to the product.
    assert CLOUD_DECODED_DEMO_URL not in _INFRA_CONSULTING_SYSTEM_PROMPT
    assert CONSULTING_OFFER_URL not in _CLOUD_DECODED_JOB_SIGNAL_SYSTEM_PROMPT


# ── Layer 2: output guards ───────────────────────────────────────────────

def test_clean_copy_passes():
    good = ("Cory — saw Onebrief is hiring a Senior SRE for application "
            "reliability. I do cloud and platform engineering, currently at "
            "CorVel. Worth a 20-minute call? https://thdagentic.com")
    assert copy_violations(good) == []


@pytest.mark.parametrize("bad,expect", [
    ("I spent years at Boeing building production infrastructure.", "Boeing"),
    ("My Honeywell Aerospace work covered regulated cloud systems.", "Honeywell"),
    ("Kelvin has deep platform experience.", "third person"),
    ("Kelvin's background is infrastructure.", "third person"),
    ("His background includes regulated environments.", "third person"),
    ("Hi Onebrief team — saw your SRE posting.", "greets a company"),
    ("Hi there — saw your SRE posting.", "generic greeting"),
])
def test_real_violations_are_caught(bad, expect):
    problems = copy_violations(bad)
    assert problems, f"no violation found in {bad!r}"
    assert any(expect in p for p in problems), problems


def test_a_compliance_term_is_fine_when_the_posting_names_it():
    """The rule is about SPECULATION, not about the word."""
    copy = "Saw the FedRAMP requirement on your platform engineer posting."
    jd = "... must have experience with FedRAMP Moderate authorization ..."
    assert copy_violations(copy, jd_text=jd) == []


def test_a_compliance_term_the_posting_never_mentions_is_a_violation():
    copy = "Hiring a cleared SRE suggests you're heading for IL5 workloads."
    jd = "Senior SRE, Arlington VA. Secret clearance required."
    problems = copy_violations(copy, jd_text=jd)
    assert any("IL5" in p for p in problems), problems


def test_the_real_onebrief_draft_is_checked_against_its_own_posting():
    """The live Onebrief touch_2 speculated beyond the posting. Keeping the
    real text here so the guard is anchored to something that actually
    happened rather than an invented example."""
    live = ("Hiring a cleared SRE in Arlington for application reliability "
            "tells me Onebrief is at a stage where platform resilience — "
            "uptime, incident response, and compliance posture — starts "
            "compounding.")
    jd = "Senior Site Reliability Engineer. Secret clearance. Arlington, VA."
    # "compliance posture" is vague rather than a named regime, so it is not
    # caught here -- documenting that limit explicitly instead of implying
    # the guard catches all speculation.
    assert copy_violations(live, jd_text=jd) == []


# ── Length caps must never cut a word (2026-10-05) ───────────────────────
# The first regeneration under the new rules produced prospect-facing copy
# ending "...an experienced c" and "Worth a 20-minu": the added first-person
# credibility line plus the URL pushed consulting touch_2 past its 500-char
# cap, and the cap was a bare str[:limit] slice.

from agents.marketing.mkt_o2_cold_dm_writer import _trim_copy


def test_short_copy_is_untouched():
    assert _trim_copy("Hi Cory — worth a call?", 500) == "Hi Cory — worth a call?"


def test_trimming_prefers_a_sentence_boundary():
    text = ("Saw the SRE posting. I do cloud and platform engineering, currently "
            "at CorVel. Worth a 20-minute call?")
    out = _trim_copy(text, 80)
    assert out.endswith("."), f"expected a sentence end, got {out!r}"
    assert len(out) <= 80


def test_trimming_never_ends_mid_word():
    text = "Worth a twenty minute conversation about your platform reliability work"
    for limit in range(12, len(text)):
        out = _trim_copy(text, limit)
        assert len(out) <= limit
        if out and not text.startswith(out + " ") and out != text:
            # Whatever the cut point, the result must end at a real boundary:
            # either the full text, or text followed by a space in the original.
            assert text[len(out):len(out) + 1] in (" ", "", ".", "?", "!"), \
                f"limit {limit} cut mid-word: {out!r}"


def test_the_real_truncations_would_now_be_clean():
    """The two actual broken endings from the 2026-10-05 regeneration."""
    earnin = ("The Senior AI Platform Engineer role signals real investment. "
              "I do cloud and platform engineering, currently at CorVel. If you are "
              "carrying that hiring effort while keeping existing infra moving, an "
              "experienced contractor helps.")
    out = _trim_copy(earnin, 180)
    assert not out.endswith("an experienced c")
    assert out.endswith((".", "!", "?")), out


def test_an_unsplittable_token_still_respects_the_limit():
    """A single long token has no boundary; the cap must still hold."""
    assert len(_trim_copy("Supercalifragilisticexpialidocious", 10)) <= 10


# ── The first-name requirement ───────────────────────────────────────────

def test_missing_first_name_is_flagged_when_expected():
    copy = "Hi — saw Security Risk Advisors is hiring a Cloud Security Engineer."
    problems = copy_violations(copy, first_name="Mike", expect_first_name=True)
    assert any("first name" in p for p in problems), problems


def test_present_first_name_passes():
    copy = "Mike — saw Security Risk Advisors is hiring a Cloud Security Engineer."
    assert copy_violations(copy, first_name="Mike", expect_first_name=True) == []


def test_first_name_is_not_required_unless_asked():
    """touch_3 is a one-line PS and does not re-greet."""
    copy = "Last note — happy to connect if the timing makes sense."
    assert copy_violations(copy, first_name="Mike") == []


def test_both_writers_pass_the_first_name_into_the_prompt():
    """The greeting rule is unsatisfiable unless the name is in context --
    which is exactly why the first regenerated batch said "Hi —"."""
    import inspect
    from agents.marketing import mkt_o2_cold_dm_writer as o2
    for fn in (o2._write_infra_consulting_dm_for_lead,
               o2._write_cloud_decoded_job_signal_dm_for_lead):
        src = inspect.getsource(fn)
        assert '"first_name": lead.get("first_name")' in src, \
            f"{fn.__name__} does not pass first_name into the prompt context"


def test_touch_2_has_room_for_the_closing_url():
    """The cap must not be able to trim away the CTA. The first regenerated
    Earnin touch_2 lost "https://thdagentic.com — worth a 20-minute call?"
    because 500 chars (a LinkedIn limit) was applied to an email."""
    from agents.marketing.mkt_o2_cold_dm_writer import (
        CONSULTING_OFFER_URL, TOUCH_2_INFRA_MAX_CHARS, TOUCH_2_CD_JOB_SIGNAL_MAX_CHARS,
    )
    # An observation + a credibility line + the ask + the URL does not fit in
    # 500; it does in 900.
    closing = f"{CONSULTING_OFFER_URL} — worth a 20-minute call?"
    assert TOUCH_2_INFRA_MAX_CHARS >= 700 + len(closing) - 100
    assert TOUCH_2_CD_JOB_SIGNAL_MAX_CHARS >= 700


# ── The closing link must not depend on model compliance ─────────────────

from agents.marketing.mkt_o2_cold_dm_writer import _ensure_close_url


def test_a_missing_close_url_is_appended():
    """GoReel's regenerated Cloud Decoded touch_2 shipped with no link at all
    despite the prompt asking for one."""
    body = "Nothing replaces the hire — but a smoother first 90 days helps."
    out = _ensure_close_url(body, "https://theclouddecoded.com/demo", 900)
    assert out.endswith("https://theclouddecoded.com/demo")
    assert body in out


def test_an_existing_url_is_not_duplicated():
    body = "Worth a look at https://theclouddecoded.com/demo?"
    assert _ensure_close_url(body, "https://theclouddecoded.com/demo", 900) == body


def test_appending_trims_the_body_not_the_url():
    """A truncated URL is worse than a shorter message."""
    url = "https://thdagentic.com"
    body = "A" * 880 + ". Worth a call?"
    out = _ensure_close_url(body, url, 900)
    assert len(out) <= 900
    assert out.endswith(url), "the URL must survive intact"


def test_no_url_configured_leaves_the_text_alone():
    assert _ensure_close_url("body", "", 900) == "body"


def test_both_closing_touches_guarantee_their_url():
    import inspect
    from agents.marketing import mkt_o2_cold_dm_writer as o2
    for fn, url_const in ((o2._write_infra_consulting_dm_for_lead, "CONSULTING_OFFER_URL"),
                          (o2._write_cloud_decoded_job_signal_dm_for_lead, "CLOUD_DECODED_DEMO_URL")):
        src = inspect.getsource(fn)
        assert "_ensure_close_url(" in src, f"{fn.__name__} does not guarantee its close URL"
        assert url_const in src, f"{fn.__name__} uses the wrong URL constant"
