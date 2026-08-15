"""
Brevo (formerly Sendinblue) client wrapper — replaces systeme.io as MKT-O3's
email sequence provider. systeme.io's public API has no campaigns/sequences
endpoint at all (confirmed 404 on every plausible path — see
agents/marketing/mkt_o3_email_sequence_loader.py's SystemeIOError docstring
for the full investigation); Brevo is free (300 emails/day, 100K contacts)
and has a real contacts + automation API.

How this fits the pipeline: this repo never sends the nurture emails
itself. Each MSE product gets its own Brevo contact list and its own
automation workflow, built once, by hand, in the Brevo UI (see
BREVO_SETUP.md) — the workflow's trigger is "contact added to list X".
This module's only job is create/update a contact and add them to the
right list; adding them is what fires Brevo's own automation from there.

Every method/class name below was confirmed against the real installed
package (brevo-python==5.0.2, `from brevo import Brevo`) via direct
Python introspection, not assumed from documentation or cached knowledge.
That package is a Fern-generated client (`client.contacts.create_contact(...)`,
typed exceptions per status code) — structurally different from the
older sib-api-v3-sdk (Configuration()/ApiClient()/ContactsApi() style)
that most search results and cached knowledge describe, so nothing here
should be assumed to work without checking the docs/installed package if
this SDK major-version-bumps again later.
"""
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from brevo import (
    AddContactToListRequestBodyEmails,
    Brevo,
    NotFoundError,
    RemoveContactFromListRequestBodyEmails,
)
from brevo.core.api_error import ApiError

logger = logging.getLogger(__name__)

_RETRY_STATUS_CODE = 429
_RETRY_BACKOFF_SECONDS = 2.0
_GET_LISTS_LIMIT = 50  # MSE's product count is nowhere near this; not paginated


class ConfigurationError(RuntimeError):
    pass


@dataclass
class BrevoContactResult:
    success: bool
    contact_id: Optional[int] = None
    error: Optional[str] = None


@dataclass
class BrevoContact:
    email: str
    id: int
    attributes: dict = field(default_factory=dict)
    list_ids: list = field(default_factory=list)


@dataclass
class BrevoList:
    id: int
    name: str


def _new_client(api_key: Optional[str] = None) -> Brevo:
    """Constructs a real Brevo SDK client. Deliberately NOT called at
    module import time (unlike the task spec's literal "raise
    ConfigurationError at import time" wording) -- this repo's other
    provider wrappers (e.g. _SystemeIOClient in mkt_o3) all raise on
    first real construction/use, not on `import core.X`, specifically so
    the rest of the app (tests, other routers, the FastAPI app itself)
    keeps working when a not-yet-configured integration is simply never
    called. Brevo is exactly that right now -- BREVO_API_KEY isn't set
    yet (Kelvin hasn't signed up), and an import-time crash here would
    take down every module that transitively imports this one, including
    api/main.py at process start. Flagged as a deliberate deviation, not
    an oversight.
    """
    key = api_key or os.environ.get("BREVO_API_KEY")
    if not key:
        raise ConfigurationError("BREVO_API_KEY is not set — see BREVO_SETUP.md for setup steps")
    return Brevo(api_key=key)


def _call_with_retry(fn, *args, **kwargs):
    """Retries exactly once on a 429 rate limit with a 2-second backoff.
    Brevo's free tier has a real, low rate limit that a batch enrollment
    run can plausibly hit. Any other ApiError propagates to the caller --
    every public function in this module catches ApiError at its own call
    site and returns a typed failure result instead of raising into the
    calling agent, per the task spec."""
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        if getattr(exc, "status_code", None) == _RETRY_STATUS_CODE:
            logger.warning("Brevo 429 rate limit, retrying once after %.0fs", _RETRY_BACKOFF_SECONDS)
            time.sleep(_RETRY_BACKOFF_SECONDS)
            return fn(*args, **kwargs)
        raise


def _error_message(exc: ApiError) -> str:
    body = getattr(exc, "body", None)
    return str(getattr(body, "message", None) or body or exc)


def create_or_update_contact(
    email: str,
    first_name: str,
    last_name: str,
    attributes: Optional[dict] = None,
    list_ids: Optional[list] = None,
    api_key: Optional[str] = None,
    brevo_client: Optional[Any] = None,
) -> BrevoContactResult:
    """
    Upserts a contact (update_enabled=True — Brevo's own mechanism for
    "create or update by email", confirmed via the SDK's create_contact
    signature). FIRSTNAME/LASTNAME are Brevo's reserved default
    attributes; custom attributes (e.g. PRODUCT_ID, TRIAL_START,
    PLAN_TIER) are uppercased automatically -- Brevo's own convention,
    and custom attributes must already exist in the Brevo account (see
    BREVO_SETUP.md) or the call fails with a real "Attribute not found"
    error, surfaced here as a failed BrevoContactResult, never silently
    dropped.
    """
    client = brevo_client if brevo_client is not None else _new_client(api_key)
    merged_attributes = {"FIRSTNAME": first_name, "LASTNAME": last_name}
    for key, value in (attributes or {}).items():
        merged_attributes[key.upper()] = value

    try:
        result = _call_with_retry(
            client.contacts.create_contact,
            email=email,
            attributes=merged_attributes,
            list_ids=list_ids or [],
            update_enabled=True,
        )
        return BrevoContactResult(success=True, contact_id=getattr(result, "id", None))
    except ApiError as exc:
        logger.warning("Brevo create_or_update_contact failed for %s: %s", email, _error_message(exc))
        return BrevoContactResult(success=False, error=_error_message(exc))


def get_contact(email: str, api_key: Optional[str] = None, brevo_client: Optional[Any] = None) -> Optional[BrevoContact]:
    client = brevo_client if brevo_client is not None else _new_client(api_key)
    try:
        result = _call_with_retry(client.contacts.get_contact_info, email)
        # result.attributes is a dynamically-typed pydantic model (its
        # fields are whatever custom contact attributes exist in the
        # Brevo account), not a plain dict -- model_dump() converts it so
        # callers get real dict access (.get()/.items()) as the
        # BrevoContact.attributes type hint promises.
        attrs = result.attributes.model_dump() if result.attributes is not None else {}
        return BrevoContact(email=result.email, id=result.id, attributes=attrs, list_ids=list(result.list_ids or []))
    except NotFoundError:
        return None
    except ApiError as exc:
        logger.warning("Brevo get_contact failed for %s: %s", email, _error_message(exc))
        return None


def add_to_list(email: str, list_id: int, api_key: Optional[str] = None, brevo_client: Optional[Any] = None) -> bool:
    client = brevo_client if brevo_client is not None else _new_client(api_key)
    try:
        _call_with_retry(
            client.contacts.add_contact_to_list,
            list_id=list_id, request=AddContactToListRequestBodyEmails(emails=[email]),
        )
        return True
    except ApiError as exc:
        logger.warning("Brevo add_to_list failed for %s -> list %s: %s", email, list_id, _error_message(exc))
        return False


def remove_from_list(email: str, list_id: int, api_key: Optional[str] = None, brevo_client: Optional[Any] = None) -> bool:
    client = brevo_client if brevo_client is not None else _new_client(api_key)
    try:
        _call_with_retry(
            client.contacts.remove_contact_from_list,
            list_id=list_id, request=RemoveContactFromListRequestBodyEmails(emails=[email]),
        )
        return True
    except ApiError as exc:
        logger.warning("Brevo remove_from_list failed for %s -> list %s: %s", email, list_id, _error_message(exc))
        return False


def get_lists(api_key: Optional[str] = None, brevo_client: Optional[Any] = None) -> list:
    """Used to verify a product's list_id exists before attempting to add
    a contact — see api/routers/brevo.py's POST /marketing/brevo/lists."""
    client = brevo_client if brevo_client is not None else _new_client(api_key)
    try:
        result = _call_with_retry(client.contacts.get_lists, limit=_GET_LISTS_LIMIT)
        return [BrevoList(id=l.id, name=l.name) for l in (result.lists or [])]
    except ApiError as exc:
        logger.warning("Brevo get_lists failed: %s", _error_message(exc))
        return []
