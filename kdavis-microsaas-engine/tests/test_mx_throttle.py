"""Per-MX throttling (Kelvin's decision 1c, 2026-10-02).

The property that matters is the one the old code got wrong: spacing is owed
to a MAIL SERVER, not to our process. Different servers must not wait on each
other; the same server must.
"""

import pytest

import core.email_finder as ef


@pytest.fixture(autouse=True)
def _clean():
    ef.reset_mx_throttle_state()
    yield
    ef.reset_mx_throttle_state()


class FakeClock:
    """Monotonic clock that only advances when something sleeps, so a test
    measures the throttle's own arithmetic rather than real elapsed time."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds

    def advance(self, seconds):
        self.t += seconds


# ── tier selection ───────────────────────────────────────────────────────

@pytest.mark.parametrize("mx", [
    "aspmx.l.google.com",
    "alt1.aspmx.l.google.com",
    "acme-com.mail.protection.outlook.com",
    "mx0a-00123456.pphosted.com",
    "us-smtp-inbound-1.mimecast.com",
])
def test_hyperscale_providers_get_the_short_delay(mx):
    assert ef.mx_delay_for(mx) == ef.HYPERSCALE_MX_DELAY_SECONDS


@pytest.mark.parametrize("mx", [
    "mail.smallcompany.com",
    "mx.selfhosted.io",
    "notgoogle.com.evil.example",   # must NOT match on a substring
    None,
])
def test_everything_else_gets_the_conservative_delay(mx):
    assert ef.mx_delay_for(mx) == ef.SAME_MX_DELAY_SECONDS


def test_suffix_match_is_boundary_anchored():
    """'evilgoogle.com' must not be treated as Google."""
    assert ef.mx_delay_for("mail.evilgoogle.com") == ef.SAME_MX_DELAY_SECONDS
    assert ef.mx_delay_for("google.com") == ef.HYPERSCALE_MX_DELAY_SECONDS


# ── the core behaviour ───────────────────────────────────────────────────

def test_first_probe_to_a_server_never_waits():
    c = FakeClock()
    slept = ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c)
    assert slept == 0.0
    assert c.slept == []


def test_different_mail_servers_do_not_wait_on_each_other():
    """THE regression this replaces: probing 5 different companies in a row
    used to cost 5 x 180-360s. It must now cost nothing."""
    c = FakeClock()
    total = 0.0
    for host in ("mail.a.com", "mail.b.com", "mail.c.com", "mail.d.com", "mail.e.com"):
        total += ef.throttle_for_mx(host, sleep=c.sleep, clock=c)
    assert total == 0.0
    assert c.slept == []


def test_same_mail_server_twice_waits_the_full_interval():
    c = FakeClock()
    ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c)
    slept = ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c)
    assert slept == pytest.approx(ef.SAME_MX_DELAY_SECONDS)


def test_waiting_only_covers_the_remaining_interval():
    """Time already spent on the SMTP conversation counts toward the spacing;
    sleeping the full interval again would double-charge it."""
    c = FakeClock()
    ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c)
    c.advance(20.0)  # the probe itself took 20s
    slept = ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c)
    assert slept == pytest.approx(ef.SAME_MX_DELAY_SECONDS - 20.0)


def test_no_wait_once_the_interval_has_already_elapsed():
    c = FakeClock()
    ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c)
    c.advance(ef.SAME_MX_DELAY_SECONDS + 5)
    assert ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c) == 0.0


def test_two_different_domains_on_the_SAME_provider_are_throttled_together():
    """Both tenants resolve to aspmx.l.google.com, so they are one server's
    load even though they are different companies. The old per-call sleep
    could not express this."""
    c = FakeClock()
    ef.throttle_for_mx("aspmx.l.google.com", sleep=c.sleep, clock=c)
    slept = ef.throttle_for_mx("aspmx.l.google.com", sleep=c.sleep, clock=c)
    assert slept == pytest.approx(ef.HYPERSCALE_MX_DELAY_SECONDS)


def test_mx_host_comparison_ignores_case_and_trailing_dot():
    c = FakeClock()
    ef.throttle_for_mx("Mail.Acme.COM.", sleep=c.sleep, clock=c)
    slept = ef.throttle_for_mx("mail.acme.com", sleep=c.sleep, clock=c)
    assert slept > 0, "the same host spelled differently must still throttle"


# ── find_email integration ───────────────────────────────────────────────

class FakeAnswer:
    def __init__(self, host, pref=10):
        self.exchange, self.preference = host, pref


class FakeResolver:
    def __init__(self, host="mail.acme.com"):
        self.host = host
        self.calls = 0

    def resolve(self, domain, rtype):
        self.calls += 1
        return [FakeAnswer(self.host)]


class RejectingSMTP:
    """Every RCPT TO is refused, so find_email exhausts all candidates --
    the worst case for pacing, which is what should be measured."""

    def helo(self, *a): return (250, b"ok")
    def mail(self, *a): return (250, b"ok")
    def rcpt(self, *a): return (550, b"no such user")
    def quit(self): pass


def test_find_email_resolves_mx_once_not_per_candidate(monkeypatch):
    monkeypatch.setattr(ef, "get_known_patterns", lambda d, supabase_client=None: [])
    monkeypatch.setattr(ef, "record_attempt", lambda *a, **k: None)
    c, r = FakeClock(), FakeResolver()
    ef.find_email("jane", "doe", "acme.com", smtp_client=RejectingSMTP(),
                  resolver=r, max_candidates=4, sleep=c.sleep, clock=c)
    assert r.calls == 1, f"MX resolved {r.calls} times for one domain"


def test_find_email_spaces_same_domain_candidates(monkeypatch):
    monkeypatch.setattr(ef, "get_known_patterns", lambda d, supabase_client=None: [])
    monkeypatch.setattr(ef, "record_attempt", lambda *a, **k: None)
    c = FakeClock()
    ef.find_email("jane", "doe", "acme.com", smtp_client=RejectingSMTP(),
                  resolver=FakeResolver(), max_candidates=4, sleep=c.sleep, clock=c)
    # 4 candidates, one server: first free, then three spaced.
    assert len(c.slept) == 3
    assert all(s == pytest.approx(ef.SAME_MX_DELAY_SECONDS) for s in c.slept)


def test_consecutive_leads_on_different_domains_cost_nothing(monkeypatch):
    """The wall-clock win: the old code charged 180-360s per extra candidate
    regardless of domain. One candidate each across 10 domains is now free."""
    monkeypatch.setattr(ef, "get_known_patterns", lambda d, supabase_client=None: [])
    monkeypatch.setattr(ef, "record_attempt", lambda *a, **k: None)
    c = FakeClock()
    for i in range(10):
        ef.find_email("jane", "doe", f"company{i}.com", smtp_client=RejectingSMTP(),
                      resolver=FakeResolver(host=f"mail.company{i}.com"),
                      max_candidates=1, sleep=c.sleep, clock=c)
    assert c.slept == [], f"slept {c.slept} across 10 distinct mail servers"


def test_the_old_unconditional_sleep_is_gone():
    """Guards against a reintroduced global sleep: find_email must not call
    time.sleep directly, nor use the legacy random spacing."""
    import inspect
    src = inspect.getsource(ef.find_email)
    assert "time.sleep" not in src
    assert "MIN_VERIFY_DELAY_SECONDS" not in src
    assert "throttle_for_mx" in src
