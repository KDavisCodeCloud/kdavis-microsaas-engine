"""
core/brevo_client.py — wrapper around the real brevo-python SDK (v5.0.2).
The SDK client's `contacts` namespace is mocked at the method level
(MagicMock); this covers this module's own retry/error-handling logic,
not Brevo's actual live API behavior. The real request/response shapes
(field names, upsert semantics, attribute casing) were separately
verified by hand against the installed package via httpx.MockTransport
before writing this module — see its docstring.
"""
from unittest.mock import MagicMock

from brevo import BadRequestError, NotFoundError, TooManyRequestsError

import core.brevo_client as bc


def _fake_client():
    return MagicMock()


def test_create_or_update_contact_succeeds_with_valid_mock_response():
    client = _fake_client()
    client.contacts.create_contact.return_value = MagicMock(id=42)

    result = bc.create_or_update_contact(
        email="jane@example.com", first_name="Jane", last_name="Doe",
        attributes={"product_id": "prod-1", "plan_tier": "solo"},
        list_ids=[7], brevo_client=client,
    )

    assert result == bc.BrevoContactResult(success=True, contact_id=42, error=None)
    call_kwargs = client.contacts.create_contact.call_args.kwargs
    assert call_kwargs["email"] == "jane@example.com"
    assert call_kwargs["attributes"] == {
        "FIRSTNAME": "Jane", "LASTNAME": "Doe", "PRODUCT_ID": "prod-1", "PLAN_TIER": "solo",
    }
    assert call_kwargs["list_ids"] == [7]
    assert call_kwargs["update_enabled"] is True


def test_create_or_update_contact_returns_typed_failure_on_api_error_without_raising():
    client = _fake_client()
    client.contacts.create_contact.side_effect = BadRequestError(body=MagicMock(message="Attribute not found"))

    result = bc.create_or_update_contact("jane@example.com", "Jane", "Doe", brevo_client=client)

    assert result.success is False
    assert result.error


def test_create_or_update_contact_handles_429_rate_limit_with_retry():
    client = _fake_client()
    client.contacts.create_contact.side_effect = [
        TooManyRequestsError(body="rate limited"),
        MagicMock(id=99),
    ]

    result = bc.create_or_update_contact("jane@example.com", "Jane", "Doe", brevo_client=client)

    assert result == bc.BrevoContactResult(success=True, contact_id=99, error=None)
    assert client.contacts.create_contact.call_count == 2


def test_create_or_update_contact_does_not_retry_twice_on_repeated_429():
    """Retries exactly once, per the task spec -- a second consecutive
    429 must propagate as a failed result, not loop forever."""
    client = _fake_client()
    client.contacts.create_contact.side_effect = [
        TooManyRequestsError(body="rate limited"),
        TooManyRequestsError(body="still rate limited"),
    ]

    result = bc.create_or_update_contact("jane@example.com", "Jane", "Doe", brevo_client=client)

    assert result.success is False
    assert client.contacts.create_contact.call_count == 2


def test_get_contact_returns_none_on_not_found():
    client = _fake_client()
    client.contacts.get_contact_info.side_effect = NotFoundError(body="not found")

    assert bc.get_contact("nobody@example.com", brevo_client=client) is None


def test_get_contact_converts_attributes_to_plain_dict():
    client = _fake_client()
    attrs = MagicMock()
    attrs.model_dump.return_value = {"FIRSTNAME": "Jane", "PRODUCT_ID": "prod-1"}
    client.contacts.get_contact_info.return_value = MagicMock(
        email="jane@example.com", id=1, attributes=attrs, list_ids=[7],
    )

    result = bc.get_contact("jane@example.com", brevo_client=client)

    assert result.attributes == {"FIRSTNAME": "Jane", "PRODUCT_ID": "prod-1"}
    assert isinstance(result.attributes, dict)


def test_add_to_list_returns_true_on_success():
    client = _fake_client()
    client.contacts.add_contact_to_list.return_value = MagicMock()

    assert bc.add_to_list("jane@example.com", 7, brevo_client=client) is True
    call_kwargs = client.contacts.add_contact_to_list.call_args.kwargs
    assert call_kwargs["list_id"] == 7
    assert call_kwargs["request"].emails == ["jane@example.com"]


def test_add_to_list_returns_false_on_api_error():
    client = _fake_client()
    client.contacts.add_contact_to_list.side_effect = BadRequestError(body="bad list id")

    assert bc.add_to_list("jane@example.com", 999, brevo_client=client) is False


def test_remove_from_list_returns_true_on_success():
    client = _fake_client()
    client.contacts.remove_contact_from_list.return_value = MagicMock()

    assert bc.remove_from_list("jane@example.com", 7, brevo_client=client) is True


def test_get_lists_returns_typed_list():
    # MagicMock(name=...) sets the mock's own repr name, not a `.name`
    # attribute -- must be assigned after construction to actually stick.
    list_item = MagicMock(id=7)
    list_item.name = "Showing Signal Trial Nurture"
    client = _fake_client()
    client.contacts.get_lists.return_value = MagicMock(lists=[list_item])

    result = bc.get_lists(brevo_client=client)
    assert result == [bc.BrevoList(id=7, name="Showing Signal Trial Nurture")]


def test_get_lists_returns_empty_list_on_api_error():
    client = _fake_client()
    client.contacts.get_lists.side_effect = BadRequestError(body="boom")

    assert bc.get_lists(brevo_client=client) == []


def test_raises_configuration_error_without_api_key_when_first_used(monkeypatch):
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    try:
        bc.create_or_update_contact("jane@example.com", "Jane", "Doe")
        assert False, "expected ConfigurationError"
    except bc.ConfigurationError:
        pass
