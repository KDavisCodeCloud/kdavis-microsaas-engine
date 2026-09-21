import pytest

from core.email_compliance import (
    append_compliance_footer,
    build_list_unsubscribe_headers,
    build_unsubscribe_url,
    daily_send_cap,
    generate_unsubscribe_token,
    is_suppressed,
    sends_today,
    suppress_email,
    verify_unsubscribe_token,
)


def test_token_round_trips():
    token = generate_unsubscribe_token("lead@example.com")
    assert verify_unsubscribe_token("lead@example.com", token)


def test_token_is_case_and_whitespace_insensitive_on_the_email():
    token = generate_unsubscribe_token("Lead@Example.com")
    assert verify_unsubscribe_token(" lead@example.com ", token)


def test_wrong_token_is_rejected():
    assert not verify_unsubscribe_token("lead@example.com", "not-the-real-token")


def test_token_for_a_different_email_is_rejected():
    token = generate_unsubscribe_token("lead@example.com")
    assert not verify_unsubscribe_token("someone-else@example.com", token)


def test_empty_token_is_rejected():
    assert not verify_unsubscribe_token("lead@example.com", "")


def test_build_unsubscribe_url_contains_email_and_valid_token():
    url = build_unsubscribe_url("lead@example.com")
    assert url.startswith("https://mse-api-production-f8bd.up.railway.app/marketing/unsubscribe?")
    assert "email=lead%40example.com" in url
    token = generate_unsubscribe_token("lead@example.com")
    assert f"token={token}" in url


def test_build_list_unsubscribe_headers_rfc_8058():
    headers = build_list_unsubscribe_headers("lead@example.com")
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    url = build_unsubscribe_url("lead@example.com")
    assert headers["List-Unsubscribe"] == f"<{url}>"


def test_is_suppressed_true_when_row_exists(fake_db):
    fake_db.responses["mse_email_suppressions"] = [{"id": "sup-1", "email": "lead@example.com"}]
    assert is_suppressed(fake_db, "lead@example.com") is True


def test_is_suppressed_false_when_no_row(fake_db):
    fake_db.responses["mse_email_suppressions"] = []
    assert is_suppressed(fake_db, "lead@example.com") is False


def test_suppress_email_upserts_normalized_address(fake_db):
    suppress_email(fake_db, "  Lead@Example.com  ", reason="unsubscribed")

    upserts = [c for c in fake_db.executed if c.table_name == "mse_email_suppressions" and c.calls[0][0] == "upsert"]
    assert len(upserts) == 1
    assert upserts[0]._payload == {"email": "lead@example.com", "reason": "unsubscribed"}


def test_append_compliance_footer_includes_address_and_unsubscribe_link():
    body = append_compliance_footer("Hi there, quick question.", "lead@example.com")
    assert "Hi there, quick question." in body
    assert "[test address]" in body  # COMPLIANCE_MAILING_ADDRESS, conftest.py
    assert "/marketing/unsubscribe?email=lead%40example.com" in body


def test_append_compliance_footer_raises_without_mailing_address(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_MAILING_ADDRESS", raising=False)
    with pytest.raises(KeyError):
        append_compliance_footer("Hi there.", "lead@example.com")


def test_sends_today_counts_only_mkt_o5_wins(fake_db):
    fake_db.responses["audit_log"] = [
        {"id": "1", "agent_id": "mkt-o5", "action": "sequence_send", "outcome": "win"},
        {"id": "2", "agent_id": "mkt-o5", "action": "sequence_send", "outcome": "win"},
    ]
    assert sends_today(fake_db) == 2


def test_daily_send_cap_defaults_to_200(monkeypatch):
    monkeypatch.delenv("MARKETING_DAILY_SEND_CAP", raising=False)
    assert daily_send_cap() == 200


def test_daily_send_cap_reads_env_override(monkeypatch):
    monkeypatch.setenv("MARKETING_DAILY_SEND_CAP", "50")
    assert daily_send_cap() == 50
