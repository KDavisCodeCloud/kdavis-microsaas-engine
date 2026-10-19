"""
agents/marketing/outreach_copy.py

Route-aware outreach copy (Kelvin's decision 1, 2026-10-06).

WHAT WAS WRONG. Copy was selected by lead_source -- a PROVENANCE fact ("a job
posting found this company") -- and the channel was baked into whichever prompt
that provenance happened to pick. So consulting leads got a LinkedIn sequence
(touch_1 a "connection request note", touch_2 "sent 3 days after the
connection request is accepted") while scraper v2 routed them to
outbound_email and MKT-O5 emailed that text verbatim. The copy described a
channel it was not delivered on.

THE SPLIT. Two things vary independently and are now composed rather than
conflated:

  CHANNEL  (from lead_route)  what shape the sequence takes -- subject line
                              vs connection note, what the delays mean, which
                              vocabulary is forbidden.
  OFFER    (from product_id)  what is being sold, and where the close points.

A prompt is CHANNEL + OFFER + the shared copy rules. Adding a product means
adding an offer block; adding a channel means adding a channel block. Neither
requires touching the other, which is what stopped being true when the two
were welded together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ROUTE_EMAIL = "outbound_email"
ROUTE_LINKEDIN = "manual_linkedin"

# LinkedIn's connection-note limit is a real platform constraint, so it is the
# one cap that is not a style choice.
LINKEDIN_NOTE_MAX = 300
LINKEDIN_DM_MAX = 700
EMAIL_SUBJECT_MAX = 90
EMAIL_TOUCH_MAX = 900

CONSULTING_OFFER_URL = "https://thdagentic.com"
CLOUD_DECODED_DEMO_URL = "https://theclouddecoded.com/demo"

# Vocabulary that must never appear in EMAIL-route copy. Each of these
# describes a LinkedIn mechanic; in an email they are simply false, and a
# prospect reading "once you accept my connection request" in their inbox
# learns that nobody checked what was sent.
LINKEDIN_ONLY_PHRASES = (
    "connection request",
    "connection note",
    "connect with you",
    "once we're connected",
    "once we are connected",
    "after you accept",
    "accept my request",
    "accept the request",
    "accepting my connection",
    "my invitation to connect",
    "invite to connect",
    "linkedin",
    "inmail",
    "dm",
)

# And the reverse: an email mechanic has no meaning in a connection note.
EMAIL_ONLY_PHRASES = (
    "subject line",
    "unsubscribe",
    "this inbox",
    "reply to this email",
    "forward this email",
)

# ── Shared copy rules (every channel, every product) ─────────────────────
#
# These are the 2026-10-05 factual corrections. They live in one string so a
# rule cannot be fixed on one channel and silently left wrong on the other --
# which is exactly what happened when the Boeing claim was corrected in the
# consulting prompt while the Cloud Decoded prompt kept its own copy.
SHARED_COPY_RULES = """\
Rules, non-negotiable:
- FACTUAL: never claim production-infrastructure, cloud, platform or DevOps work at Boeing or \
Honeywell. Those were NDT inspection roles with cloud/sysadmin work alongside. CorVel is the cloud \
engineering role. Do not name Boeing or Honeywell at all.
- Do NOT lead with employer names, job titles or credentials. Open with the prospect's own situation, \
taken from the signal given below.
- FIRST PERSON ONLY. You are writing AS Kelvin. Never write "Kelvin" or refer to him in the third \
person -- write "I".
- Address the contact by FIRST NAME, taken from the lead context. Never "Hi {company} team", never "Hi \
there", never a company name as the greeting.
- State ONLY what the job posting actually says. Do not infer or name a compliance regime (FedRAMP, \
IL2-IL6, SOC 2, HIPAA, ITAR, CMMC, PCI) unless the posting text given below names it explicitly. Do not \
speculate about clearances, contracts or customers.
- Every specific claim about the company (their hiring, their funding, their content) must come from \
the signal context given below -- never invented.
- No hype words ("game-changing", "revolutionary"), no generic flattery, no "I noticed you..." as a \
generic opener with nothing specific behind it.
- Never use dollar-amount/"make more money" framing -- this buyer is not that buyer."""


@dataclass(frozen=True)
class Offer:
    """What is being sold, and where the close points."""

    name: str
    pitch: str
    close_url: str


CONSULTING_OFFER = Offer(
    name="infrastructure consulting",
    pitch=(
        "Kelvin takes on cloud/platform engineering work on contract -- helping a team move faster on "
        "infrastructure while they hire, or when the work outpaces the headcount they have. He is a "
        "senior cloud and platform engineer, currently at CorVel. This is his own time, not a product."
    ),
    close_url=CONSULTING_OFFER_URL,
)

CLOUD_DECODED_OFFER = Offer(
    name="Cloud Decoded",
    pitch=(
        "Cloud Decoded is an 11-agent DevOps/platform automation product (CI/CD triage, Kubernetes "
        "alert remediation, IAM minimization, FinOps, drift detection). Frame it as force-"
        "multiplication for the team they are building or the person they are hiring -- it makes that "
        "hire faster and covers gaps day to day. NEVER a substitute for making the hire, and never "
        "imply they should not hire."
    ),
    close_url=CLOUD_DECODED_DEMO_URL,
)


@dataclass(frozen=True)
class ChannelSpec:
    """The shape of a sequence on one channel."""

    route: str
    touches: tuple[str, ...]
    wants_subject: bool
    limits: dict
    instructions: str


EMAIL_CHANNEL = ChannelSpec(
    route=ROUTE_EMAIL,
    touches=("touch_1", "touch_2", "touch_3"),
    wants_subject=True,
    limits={"subject": EMAIL_SUBJECT_MAX, "touch_1": EMAIL_TOUCH_MAX,
            "touch_2": EMAIL_TOUCH_MAX, "touch_3": 400},
    instructions=f"""\
This is an EMAIL sequence. It is delivered to the contact's inbox.

subject = the subject line for touch_1, max {EMAIL_SUBJECT_MAX} chars. Specific to THEIR situation, \
lower-case or sentence case, no colons-and-buzzwords, no "Re:" and no fake-reply tricks. It must read \
like a person wrote it to one person.

touch_1 = the opening email, max {EMAIL_TOUCH_MAX} chars. Open on their situation from the signal \
below. One short sentence of relevant credibility in first person. End with one low-friction question.

touch_2 = sent 3 DAYS LATER if there has been no reply, max {EMAIL_TOUCH_MAX} chars. A specific \
observation about their infrastructure challenge inferred only from the signal given. Then the ask, \
and the close URL exactly once as a bare URL with no tracking parameters.

touch_3 = sent 5 DAYS AFTER touch_2, ONLY if there has still been no reply, max 400 chars. One short \
paragraph, a different angle, and it is the last message -- say so plainly.

FORBIDDEN VOCABULARY. This is email, so LinkedIn mechanics are meaningless and must never appear: no \
"connection request", no "once we're connected", no "after you accept", no "DM", no mention of \
LinkedIn at all. The delays are plain days between emails, not time waiting for an invitation to be \
accepted.""",
)

LINKEDIN_CHANNEL = ChannelSpec(
    route=ROUTE_LINKEDIN,
    touches=("touch_1", "touch_2", "touch_3"),
    wants_subject=False,
    limits={"touch_1": LINKEDIN_NOTE_MAX, "touch_2": LINKEDIN_DM_MAX, "touch_3": 300},
    instructions=f"""\
This is a LINKEDIN sequence. A human sends each message by hand from Kelvin's own account.

touch_1 = the CONNECTION REQUEST NOTE that accompanies the invitation, max {LINKEDIN_NOTE_MAX} chars \
(LinkedIn's own hard limit -- going over means it cannot be sent). Reference something specific and \
real from the signal below. One sentence on what Kelvin does, in first person. NO pitch and NO ask: \
this note's only job is to get the invitation accepted.

touch_2 = the first DM, sent 3 days AFTER THE CONNECTION IS ACCEPTED, max {LINKEDIN_DM_MAX} chars. \
This is where the observation and the ask go, plus the close URL exactly once as a bare URL. It may \
refer to having connected, because that genuinely happened.

touch_3 = sent 5 days after touch_2, ONLY if there has been no reply, max 300 chars. One line, a \
different angle, and the last message.

FORBIDDEN VOCABULARY. There is no subject line, no inbox and no unsubscribe on this channel -- never \
mention any of them, and never write anything that only makes sense in an email.""",
)

CHANNELS = {ROUTE_EMAIL: EMAIL_CHANNEL, ROUTE_LINKEDIN: LINKEDIN_CHANNEL}


def channel_for_route(route: Optional[str]) -> ChannelSpec:
    """The channel spec for a lead's route.

    An unknown or missing route falls back to LINKEDIN, the manual channel:
    getting it wrong there costs a human one confused read, while defaulting
    to email would hand an unroutable lead to the automated sender.
    """
    return CHANNELS.get((route or "").strip(), LINKEDIN_CHANNEL)


def build_system_prompt(channel: ChannelSpec, offer: Offer, *, extra_rules: str = "") -> str:
    """CHANNEL + OFFER + shared rules, in that order. `extra_rules` are
    per-lead rules (the pain-phrase rule) appended to the shared ones."""
    schema_keys = (["subject"] if channel.wants_subject else []) + list(channel.touches)
    schema = "{\n" + ",\n".join(f'  "{k}": str' for k in schema_keys) + "\n}"
    return (
        f"You are writing a cold outreach sequence for Kelvin Davis, selling {offer.name}, "
        f"to a technical decision-maker at a company found publicly hiring.\n\n"
        f"WHAT YOU ARE SELLING\n{offer.pitch}\n\n"
        f"{channel.instructions}\n\n"
        f"Close every sequence to this URL and no other: {offer.close_url}\n\n"
        f"Return ONLY a single JSON object -- no prose, no markdown fences -- matching exactly:\n\n"
        f"{schema}\n\n"
        f"{SHARED_COPY_RULES}\n"
        + (f"{extra_rules}\n" if extra_rules else "")
        + f"- The sequence stops entirely if they reply at any point; touch_3 is only ever sent on "
        f"zero reply."
    )


def channel_violations(text: Optional[str], route: Optional[str]) -> list[str]:
    """Phrases that belong to the OTHER channel.

    Used by the copy tests and by the writer itself, so a generation that
    drifts across channels is caught rather than shipped. The check is on the
    delivered text, not the prompt: the prompt asking for email is not
    evidence that the model wrote email.
    """
    low = (text or "").lower()
    route = (route or "").strip()
    banned = LINKEDIN_ONLY_PHRASES if route == ROUTE_EMAIL else EMAIL_ONLY_PHRASES
    other = "LinkedIn" if route == ROUTE_EMAIL else "email"
    problems = []
    for phrase in banned:
        # Word-boundary anchored so "dm" does not fire inside "admin" and
        # "linkedin.com" in a URL is still caught as a mention.
        import re
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", low):
            problems.append(f"uses {other}-only phrasing on the {route} route: {phrase!r}")
    return problems


# ── Pain phrases in touch 1 (2026-10-18) ─────────────────────────────────

PAIN_RULE = """\
- PAIN FROM THEIR POSTING: the context includes `pain_evidence` -- sentences copied verbatim from \
their own job posting. touch_1 MUST name that exact pain in the posting's own words (for example "the \
ARM-to-Bicep migration your posting mentions"). Use the phrase as written; do not paraphrase it into \
something broader, and do not add pains the posting does not state."""


def pain_phrase_missing(touch_1: Optional[str], phrases: list[str]) -> bool:
    """True when the lead has pain phrases and touch_1 names none of them.
    Whitespace- and hyphen-tolerant, like the matcher that found them."""
    if not phrases:
        return False
    from agents.marketing.pain_phrases import match_pain_phrases
    return not match_pain_phrases(touch_1 or "", phrases)


# ── Partner lane (2026-10-18) ────────────────────────────────────────────
#
# A partnership note is not a sales sequence: no pain-of-the-week, no demo
# link, no ask to buy. One firm that refers twice a year is the goal.

PARTNER_ANGLES = {
    "compliance_firm": (
        "the implementation partner their clients need AFTER an audit: when a SOC 2, HIPAA or ISO "
        "assessment finds gaps in cloud infrastructure, IAM, secrets management or CI/CD, Kelvin is the "
        "engineer who closes them, so the firm's client passes the next audit and the firm stays the advisor"),
    "fractional_ciso": (
        "the hands-on cloud and IAM engineer behind a fractional CISO: the CISO sets the policy, Kelvin "
        "implements it in Azure/AWS -- least-privilege IAM, Key Vault and secrets, landing zones, pipeline "
        "controls -- white-label if they prefer"),
    "ai_agency": (
        "their white-label cloud and infrastructure arm: AI agencies ship agents and apps, and their clients "
        "then need the production side -- cloud environments, IAM, secrets, CI/CD, cost control. Kelvin "
        "takes that work so the agency does not have to staff it"),
    "vc_operating_partner": (
        "a vetted contract cloud/platform engineer for portfolio companies that are hiring for DevOps or "
        "platform roles and cannot wait for the hire -- someone the operating or talent partner can "
        "introduce when a founder asks 'who do you know?'"),
}

PARTNER_COPY_RULES = """\
Rules, non-negotiable:
- This is a PARTNERSHIP note to a peer, not a sales pitch. Never ask them to buy anything. The ask is a \
short conversation about working together or referring work.
- FIRST PERSON ONLY. You are writing AS Kelvin. Never write "Kelvin" or refer to him in the third person.
- Address the contact by FIRST NAME from the context. Never a company name as the greeting.
- Every claim about their firm comes from the signal context given below (their own site's words or \
the team page that lists them) -- never invented. Do not claim to know their clients.
- FACTUAL: Kelvin is a senior cloud and platform engineer, currently at CorVel, taking contract work on \
his own time. Never claim production infrastructure work at Boeing or Honeywell, and do not name them.
- No hype words, no flattery, no dollar amounts, no "I noticed you..." with nothing specific behind it."""


def build_partner_prompt(partner_type: Optional[str]) -> str:
    """LinkedIn channel + partner angle + partner rules. Always the manual
    LinkedIn channel: partner notes are pasted by hand."""
    angle = PARTNER_ANGLES.get((partner_type or "").strip(), PARTNER_ANGLES["ai_agency"])
    channel = LINKEDIN_CHANNEL
    schema = "{\n" + ",\n".join(f'  "{k}": str' for k in channel.touches) + "\n}"
    return (
        "You are writing a short LinkedIn PARTNERSHIP note sequence for Kelvin Davis to a possible "
        "referral partner.\n\n"
        f"THE PARTNERSHIP\nKelvin would be {angle}.\n\n"
        f"{channel.instructions}\n\n"
        f"If a URL is used at all, it is {CONSULTING_OFFER_URL} and no other.\n\n"
        f"Return ONLY a single JSON object -- no prose, no markdown fences -- matching exactly:\n\n{schema}\n\n"
        f"{PARTNER_COPY_RULES}\n"
        "- The sequence stops entirely if they reply at any point; touch_3 is only ever sent on zero reply."
    )
