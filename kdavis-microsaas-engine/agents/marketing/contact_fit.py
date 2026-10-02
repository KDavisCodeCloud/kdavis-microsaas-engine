"""
agents/marketing/contact_fit.py

Contact-title fit validation (Kelvin's decision 4, 2026-10-01).

WHY SENIORITY IS NOT ENOUGH. lead_qualification.classify_seniority answers
"how senior is this person", which is a different question from "is this
person the buyer". Both of the contacts that survived the 2026-10-01 runs
failed that second question and nothing caught them:

  Earnin      "VP of Data"  -- VP-level, and the wrong function entirely.
  SingleStore "CEO"         -- C-level, but SingleStore is a large company
                              (36+ open roles); its CEO does not take cold
                              outreach about infrastructure consulting.

So a title must now clear TWO gates:

  1. FUNCTION. Either an executive technology title (CTO / VP Engineering /
     Head of Engineering and the like), or a functional title in
     engineering / infrastructure / platform / SRE / DevOps / security
     infrastructure. "VP of Data", "VP of Sales", "VP of Product" are all
     VP-level and all out.

  2. COMPANY SIZE, for founder-shaped titles only. Founder / CEO /
     co-founder is a real buyer at a small company, where the founder still
     owns infrastructure decisions, and not at a large one. Threshold is
     <=50 people, proxied by <=10 open roles until a real headcount source
     exists (ATS job descriptions essentially never state headcount -- see
     role_taxonomy's size-proxy note).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Founder-shaped titles: valid only at a small company.
FOUNDER_TITLE_RE = re.compile(
    r"\b(?:founder|co-?founder|founding\s+partner|owner|CEO|chief\s+executive)\b", re.IGNORECASE
)

# Executive technology ownership -- valid at any size.
EXEC_TECH_TITLE_RE = re.compile(
    r"\bCTO\b|\bchief\s+technology\s+officer\b|\bchief\s+information\s+officer\b|\bCIO\b"
    r"|\bchief\s+information\s+security\s+officer\b|\bCISO\b"
    # `[^;]` not `[^,;]`: real titles put a comma there -- "SVP, Infrastructure",
    # "VP, Platform Engineering".
    r"|\b(?:VP|vice\s+president|SVP|EVP|head|director)\b[^;]{0,30}?"
    r"\b(?:engineering|infrastructure|platform|technology|technical\s+operations|devops|SRE|cloud)\b",
    re.IGNORECASE,
)

# Functional infrastructure ownership -- valid at any size.
FUNCTIONAL_TITLE_RE = re.compile(
    r"\b(?:infrastructure|platform|devops|dev[\s-]?sec[\s-]?ops|SRE|site\s+reliability"
    r"|cloud|systems?|network)\b[^,;]{0,24}?"
    r"\b(?:engineer|engineering|architect|lead|manager|director|head|owner)\b"
    r"|\b(?:engineer|engineering|architect|lead|manager|director|head)\b[^,;]{0,24}?"
    r"\b(?:infrastructure|platform|devops|SRE|site\s+reliability|cloud)\b"
    r"|\bsecurity\s+infrastructure\b|\bhead\s+of\s+engineering\b",
    re.IGNORECASE,
)

# Functions that read senior and are NOT the buyer. Checked first, because
# "VP of Data Platform" would otherwise match the functional pattern on
# "platform" alone.
WRONG_FUNCTION_RE = re.compile(
    r"\b(?:data|analytics|sales|marketing|product|finance|people|hr|legal|design"
    r"|customer\s+success|support|revenue|operations\s+manager|business\s+development)\b",
    re.IGNORECASE,
)

# "Data Platform Engineer" and "Head of Data Infrastructure" ARE infrastructure
# roles despite containing "data". Narrow, deliberate exemptions.
WRONG_FUNCTION_EXEMPT_RE = re.compile(
    r"\bdata\s+(?:platform|infrastructure)\s+(?:engineer|engineering|architect|lead)\b"
    r"|\bhead\s+of\s+data\s+(?:platform|infrastructure)\b"
    # "Cloud Operations Manager" / "Platform Operations Lead" are
    # infrastructure roles; only a BARE "Operations Manager" is business ops.
    r"|\b(?:cloud|platform|infrastructure|systems?|network|technical)\s+operations\b",
    re.IGNORECASE,
)

# <=50 people, proxied by open-role count. ATS descriptions do not state
# headcount, so this is the only size signal available for free.
FOUNDER_MAX_OPEN_ROLES = 10


@dataclass
class ContactFit:
    """Whether this person is the buyer, and why."""

    accepted: bool
    reason: str
    category: Optional[str] = None  # exec_tech | functional | founder_small


def evaluate_contact_fit(
    title: Optional[str],
    *,
    open_role_count: Optional[int] = None,
    founder_max_open_roles: int = FOUNDER_MAX_OPEN_ROLES,
) -> ContactFit:
    """
    Decide whether `title` is a buyer for infrastructure work.

    `open_role_count` is the company's TOTAL open roles, used only for the
    founder-title size gate.
    """
    if not title or not title.strip():
        return ContactFit(False, "no title", None)

    t = title.strip()

    # Executive technology ownership is valid at any company size, and is
    # checked before the wrong-function screen so "CTO" never trips it.
    if EXEC_TECH_TITLE_RE.search(t) and not WRONG_FUNCTION_RE.search(t):
        return ContactFit(True, f"executive technology title: {t!r}", "exec_tech")

    # Wrong function, regardless of seniority. This is what "VP of Data" and
    # "VP of Sales" fail on -- both VP-level, neither the buyer.
    if WRONG_FUNCTION_RE.search(t) and not WRONG_FUNCTION_EXEMPT_RE.search(t):
        wrong = WRONG_FUNCTION_RE.search(t).group(0)
        return ContactFit(False, f"wrong function ({wrong}): {t!r}", None)

    if FUNCTIONAL_TITLE_RE.search(t):
        return ContactFit(True, f"infrastructure function: {t!r}", "functional")

    # Founder-shaped: only at a small company, where the founder still owns
    # infrastructure decisions.
    if FOUNDER_TITLE_RE.search(t):
        if open_role_count is None:
            return ContactFit(
                False,
                f"founder title {t!r} but company size unknown -- cannot confirm <=50 people",
                None,
            )
        if open_role_count <= founder_max_open_roles:
            return ContactFit(
                True,
                f"founder title at a small company ({open_role_count} open roles)",
                "founder_small",
            )
        return ContactFit(
            False,
            f"founder title {t!r} at a company with {open_role_count} open roles "
            f"(> {founder_max_open_roles}, so not a <=50-person company)",
            None,
        )

    return ContactFit(False, f"not a buyer title: {t!r}", None)
