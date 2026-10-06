"""
core/linkedin_urls.py

LinkedIn profile-URL validation, in one place.

Extracted 2026-10-06 when the Approve-Drafts card needed the same check
api/routers/buyer_research.py already had. Two copies of this regex would
disagree the first time one of them learned about a new URL shape, and the
thing they guard is whether a human can actually find the person -- a
/company/ URL stored as a profile means the paste-and-send lane has no one to
message.
"""

import re
from typing import Optional

# A PERSONAL profile: linkedin.com/in/<slug>, optionally on a country
# subdomain (fr.linkedin.com, ua.linkedin.com -- both seen in real Brave
# results). Deliberately NOT matching /company/, /school/, /pub/ or search
# URLs: pasting a company page is the common mistake, and storing it as a
# person's profile is silent and unrecoverable later.
LINKEDIN_PROFILE_RE = re.compile(
    r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%./]+/?$", re.IGNORECASE
)

PROFILE_URL_HELP = (
    "linkedin_url must be a personal profile URL of the form "
    "https://www.linkedin.com/in/<slug>"
)


def is_profile_url(value: Optional[str]) -> bool:
    return bool(LINKEDIN_PROFILE_RE.match((value or "").strip()))


def normalise_profile_url(value: Optional[str]) -> str:
    """Trimmed, or "" if it is not a personal profile URL.

    Returns "" rather than raising so a caller can decide whether a bad paste
    is a validation error (an API) or simply "not set yet" (a display).
    """
    v = (value or "").strip()
    return v if is_profile_url(v) else ""
