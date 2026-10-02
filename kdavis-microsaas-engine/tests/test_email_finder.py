"""
core/email_finder.py — pattern candidate generation and SMTP verification.
smtplib/dns.resolver are fully mocked (real_client-shaped MagicMocks); this
covers the module's own logic, not a live network probe.
"""
from unittest.mock import MagicMock

from core.email_finder import build_pattern_candidates, find_email, verify_email
from tests.conftest import FakeSupabase


def test_build_pattern_candidates_generates_standard_patterns_in_order():
    candidates = build_pattern_candidates("Jane", "Doe", "example.com")
    assert candidates == [
        # Decision 3a (2026-10-01): six patterns, ordered by real-world
        # prevalence so the earliest SMTP probe is the likeliest hit. The
        # first four were the original set; f.lastname and firstname_lastname
        # were added after 10 of 19 contacts graded "invalid" -- a verdict
        # that only means "none of the patterns we TRIED exists".
        "jane.doe@example.com", "jane@example.com", "jdoe@example.com",
        "janedoe@example.com", "j.doe@example.com", "jane_doe@example.com",
    ]


def test_build_pattern_candidates_prioritizes_known_pattern_first():
    candidates = build_pattern_candidates("Jane", "Doe", "example.com", known_patterns=["flastname"])
    assert candidates[0] == "jdoe@example.com"


def test_build_pattern_candidates_empty_without_first_name_or_domain():
    assert build_pattern_candidates("", "Doe", "example.com") == []
    assert build_pattern_candidates("Jane", "Doe", "") == []


def test_build_pattern_candidates_handles_missing_last_name():
    # patterns needing a last name (flastname, firstname.lastname,
    # firstnamelastname) are skipped when there isn't one -- only
    # "firstname" survives.
    candidates = build_pattern_candidates("Jane", "", "example.com")
    assert candidates == ["jane@example.com"]


class _FakeSmtpClient:
    """Mirrors smtplib.SMTP's real .helo()/.mail()/.rcpt() interface —
    each returns (code, message)."""

    def __init__(self, rcpt_responses: dict):
        self.rcpt_responses = rcpt_responses
        self.calls = []

    def helo(self, name):
        return (250, b"ok")

    def mail(self, addr):
        return (250, b"ok")

    def rcpt(self, addr):
        self.calls.append(addr)
        return self.rcpt_responses.get(addr, (550, b"no such user"))

    def quit(self):
        pass


class _FakeMxAnswer:
    def __init__(self, exchange, preference=10):
        self.exchange = exchange
        self.preference = preference


class _FakeResolver:
    def resolve(self, domain, record_type):
        return [_FakeMxAnswer(f"mx.{domain}")]


def test_verify_email_returns_verified_on_250_and_no_catchall():
    smtp = _FakeSmtpClient({"jane@example.com": (250, b"ok")})  # fake probe address not in dict -> 550
    result = verify_email("jane@example.com", domain="example.com", smtp_client=smtp, resolver=_FakeResolver())
    assert result.status == "verified"
    assert result.smtp_code == 250


def test_verify_email_returns_catch_all_when_fake_address_also_accepted():
    class _CatchAllSmtp(_FakeSmtpClient):
        def rcpt(self, addr):
            self.calls.append(addr)
            return (250, b"ok")  # accepts everything, including the fake probe

    smtp = _CatchAllSmtp({})
    result = verify_email("jane@example.com", domain="example.com", smtp_client=smtp, resolver=_FakeResolver())
    assert result.status == "catch_all"
    assert len(smtp.calls) == 2  # real candidate + fake probe


def test_verify_email_returns_invalid_on_550():
    smtp = _FakeSmtpClient({})  # every address falls to the 550 default
    result = verify_email("jane@example.com", domain="example.com", smtp_client=smtp, resolver=_FakeResolver())
    assert result.status == "invalid"
    assert result.smtp_code == 550


def test_verify_email_returns_unverified_on_4xx():
    class _GreylistSmtp(_FakeSmtpClient):
        def rcpt(self, addr):
            return (450, b"try again later")

    result = verify_email("jane@example.com", domain="example.com", smtp_client=_GreylistSmtp({}), resolver=_FakeResolver())
    assert result.status == "unverified"
    assert result.smtp_code == 450


def test_verify_email_returns_unverified_when_no_mx_record():
    class _NoMxResolver:
        def resolve(self, domain, record_type):
            raise Exception("NXDOMAIN")

    result = verify_email("jane@example.com", domain="example.com", resolver=_NoMxResolver())
    assert result.status == "unverified"
    assert result.smtp_code is None


def test_find_email_stops_at_first_verified_candidate(monkeypatch):
    fake_db = FakeSupabase(responses={"mse_email_patterns": []})
    monkeypatch.setattr("core.email_finder.time.sleep", lambda *_: None)

    smtp = _FakeSmtpClient({"jane@example.com": (250, b"ok")})
    result = find_email("Jane", "Doe", "example.com", supabase_client=fake_db, smtp_client=smtp, resolver=_FakeResolver())

    assert result.email == "jane@example.com"
    assert result.pattern_used == "firstname"
    assert result.verification_status == "verified"
    assert result.confidence_score == 0.95

    # Only the one verified candidate's real probe should have run --
    # find_email must not keep trying more patterns once it finds a hit.
    assert smtp.calls.count("jane@example.com") == 1


def test_find_email_records_every_attempt_to_pattern_db(monkeypatch):
    fake_db = FakeSupabase(responses={"mse_email_patterns": []})
    monkeypatch.setattr("core.email_finder.time.sleep", lambda *_: None)

    smtp = _FakeSmtpClient({"jane@example.com": (250, b"ok")})
    find_email("Jane", "Doe", "example.com", supabase_client=fake_db, smtp_client=smtp, resolver=_FakeResolver())

    upserts = [c for c in fake_db.executed if c.table_name == "mse_email_patterns" and c.calls[0][0] == "upsert"]
    # Decision 3a (2026-10-01) reordered the patterns so firstname.lastname is
    # tried FIRST, which makes this test demonstrate its own point better than
    # before: the failed first attempt is recorded too, not just the success.
    # The pattern database only learns from failures if they are written.
    assert len(upserts) == 2
    assert upserts[0]._payload["pattern"] == "firstname.lastname"
    assert upserts[0]._payload["success_count"] == 0
    assert upserts[1]._payload["pattern"] == "firstname"
    assert upserts[1]._payload["success_count"] == 1
