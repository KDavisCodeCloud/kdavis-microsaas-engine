"""2026-10-19: lead-source schedules, pain rescan, partner lane order, the 8am
enrollment run record + skips + watchdog, and the Loop Map lines for them."""

from datetime import datetime, timedelta, timezone

import pytest

from agents.marketing import lead_schedule
from agents.marketing.lead_schedule import Job, claim, partner_page_offset, run_job, tick
from agents.marketing.lead_sources import _pain_update, scan_pain_phrases
from agents.marketing.mkt_o2_cold_dm_writer import skip_code
from agents.marketing.pain_phrases import PainConfig, match_pain_phrases
from api.routers.outreach_lanes import order_partner_lane, partner_rank
from core import dm_watchdog
from core.loop_map_rules import evaluate

UTC = timezone.utc


# ── an in-memory table store that honours filters and one unique index ──

class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.op, self.payload, self.filters = db, name, "select", None, []
        self._order = None

    def select(self, *a, **k):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, k, v):
        self.filters.append(lambda r: r.get(k) == v)
        return self

    def in_(self, k, vs):
        vs = list(vs)
        self.filters.append(lambda r: r.get(k) in vs)
        return self

    def gte(self, k, v):
        self.filters.append(lambda r: str(r.get(k) or "") >= v)
        return self

    def lt(self, k, v):
        self.filters.append(lambda r: str(r.get(k) or "") < v)
        return self

    def is_(self, k, v):
        self.filters.append(lambda r: r.get(k) is None)
        return self

    def order(self, k, desc=False):
        self._order = (k, desc)
        return self

    def limit(self, n):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        R = type("R", (), {})
        if self.op == "insert":
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for p in new:
                row = {"id": f"{self.name}-{len(rows) + 1}", "claimed_at": self.db.now.isoformat(),
                       "started_at": self.db.now.isoformat(), **p}
                if self.name == "mse_scheduled_job_runs" and any(
                        (r["job_key"], r["product_id"], r["period_key"]) ==
                        (row["job_key"], row["product_id"], row["period_key"]) for r in rows):
                    raise Exception("duplicate key value violates unique constraint (23505)")
                rows.append(row)
                out.append(row)
            r = R(); r.data = out; return r
        hit = [x for x in rows if all(f(x) for f in self.filters)]
        if self.op == "update":
            for x in hit:
                x.update(self.payload)
        if self._order:
            hit = sorted(hit, key=lambda x: str(x.get(self._order[0]) or ""), reverse=self._order[1])
        r = R(); r.data = [dict(x) for x in hit]; return r


class MemDB:
    def __init__(self, now=None, **tables):
        self.now = now or datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}

    def table(self, name):
        return _Q(self, name)


def _job(key="hn_scout", source="hn_thread", period="2026-11", run=None):
    return Job(key, "p1", source, lambda now: period, run or (lambda db, run_id: {"leads_written": 3}))


# ── the lock ─────────────────────────────────────────────────────────────

def test_claim_is_once_per_period_even_across_instances():
    db = MemDB()
    job = _job()
    assert claim(db, job, "2026-11")
    lead_schedule.INSTANCE_TOKEN, saved = "other-replica", lead_schedule.INSTANCE_TOKEN
    try:
        assert claim(db, job, "2026-11") is None
    finally:
        lead_schedule.INSTANCE_TOKEN = saved


def test_own_retried_insert_is_still_ours():
    """A dropped connection after a successful insert replays it; the 23505
    that follows must not make this process stand down from its own claim."""
    db = MemDB()
    job = _job()
    first = claim(db, job, "2026-11")
    assert claim(db, job, "2026-11") == first


def test_run_job_writes_run_row_claim_and_audit():
    db = MemDB()
    out = run_job(db, _job(), "2026-11")
    assert out["status"] == "complete" and out["leads_written"] == 3
    claim_row = db.tables["mse_scheduled_job_runs"][0]
    assert claim_row["status"] == "complete" and claim_row["run_id"] == out["run_id"]
    assert db.tables["mse_lead_finder_runs"][0]["sources_used"] == ["hn_thread"]
    assert db.tables["audit_log"][0]["outcome"] == "win"


def test_run_job_failure_marks_run_and_claim_failed():
    def boom(db, run_id):
        raise RuntimeError("HN API down")
    db = MemDB()
    out = run_job(db, _job(run=boom), "2026-11")
    assert out["status"] == "failed"
    assert db.tables["mse_lead_finder_runs"][0]["status"] == "failed"
    assert "HN API down" in db.tables["mse_lead_finder_runs"][0]["error_message"]
    assert db.tables["mse_scheduled_job_runs"][0]["status"] == "failed"
    assert db.tables["audit_log"][0]["outcome"] == "lose"


def test_tick_runs_each_due_job_once():
    calls = []
    job = _job(run=lambda db, run_id: calls.append(run_id) or {"leads_written": 0})
    db = MemDB()
    tick(db, now=db.now, job_list=[job])
    tick(db, now=db.now + timedelta(minutes=15), job_list=[job])
    assert len(calls) == 1


def test_watchdog_job_has_no_run_row():
    db = MemDB()
    job = Job("dm_8am_watchdog", "p1", None, lambda now: "2026-11-01", lambda db, run_id: {"alerted": False})
    out = run_job(db, job, "2026-11-01")
    assert out["status"] == "complete" and "mse_lead_finder_runs" not in db.tables


def test_abandoned_claim_fails_run_and_claim():
    db = MemDB(mse_scheduled_job_runs=[{"id": "c1", "job_key": "partner_scout", "product_id": "p1",
                                         "period_key": "2026-11", "status": "claimed", "run_id": "r1",
                                         "claimed_at": "2026-11-01T01:00:00+00:00"}],
               mse_lead_finder_runs=[{"id": "r1", "status": "running"}])
    assert lead_schedule.fail_abandoned(db, db.now) == 1
    assert db.tables["mse_lead_finder_runs"][0]["status"] == "failed"
    assert db.tables["mse_scheduled_job_runs"][0]["status"] == "failed"


# ── when each job is due ─────────────────────────────────────────────────

def _period(key, product=None):
    return next(j for j in lead_schedule.jobs() if j.key == key and (product is None or j.product_id == product))


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 11, 1, 13, 59, tzinfo=UTC), None),
    (datetime(2026, 11, 1, 14, 0, tzinfo=UTC), "2026-11"),
    (datetime(2026, 11, 5, 3, 0, tzinfo=UTC), "2026-11"),   # catch-up window
    (datetime(2026, 11, 6, 15, 0, tzinfo=UTC), None),
])
def test_partner_scout_is_due_on_the_first(when, expected):
    assert _period("partner_scout").period(when) == expected


def test_hn_is_due_on_the_third_not_the_first():
    hn = _period("hn_scout")
    assert hn.period(datetime(2026, 11, 1, 15, 0, tzinfo=UTC)) is None
    assert hn.period(datetime(2026, 11, 3, 14, 30, tzinfo=UTC)) == "2026-11"


def test_pain_rescan_is_sunday_period_through_monday():
    job = _period("pain_rescan")
    sunday = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)
    assert job.period(sunday) == "2026-10-25"
    assert job.period(sunday + timedelta(days=1)) == "2026-10-25"
    assert job.period(sunday - timedelta(days=1)) is None
    assert job.period(sunday.replace(hour=12)) is None


def test_pain_rescan_waits_for_that_sundays_scout():
    sunday = datetime(2026, 10, 25, 14, 0, tzinfo=UTC)
    running = MemDB(now=sunday, mse_lead_finder_runs=[
        {"product_id": "p1", "status": "running", "sources_used": ["job_posting_signal"],
         "started_at": "2026-10-25T13:00:01+00:00"}])
    assert lead_schedule.scout_still_running(running, "p1", "2026-10-25", sunday)
    # ... but not past the deadline
    assert not lead_schedule.scout_still_running(running, "p1", "2026-10-25", sunday.replace(hour=20))
    done = MemDB(now=sunday, mse_lead_finder_runs=[
        {"product_id": "p1", "status": "complete", "sources_used": ["job_posting_signal"],
         "started_at": "2026-10-25T13:00:01+00:00"}])
    assert not lead_schedule.scout_still_running(done, "p1", "2026-10-25", sunday)


def test_partner_page_offset_moves_each_month():
    assert partner_page_offset(datetime(2026, 10, 1).date()) == 0
    assert partner_page_offset(datetime(2026, 11, 1).date()) == 1
    assert partner_page_offset(datetime(2027, 3, 1).date()) == 0  # cycles through 5 pages


def test_scheduled_partner_run_is_capped_at_20():
    assert lead_schedule.PARTNER_SCHEDULED_MAX_QUERIES <= 20


# ── pain rescan ──────────────────────────────────────────────────────────

PAIN = PainConfig(phrases=("tech debt", "technical debt", "modernize", "Key Vault"), per_match=0.10, max_boost=0.20)


def test_rescan_boosts_only_for_new_phrases():
    lead = {"pain_phrases": ["tech debt"], "pain_evidence": [{"phrase": "tech debt", "snippet": "x"}],
            "intent_score": 0.6, "score_reasons": []}
    m = match_pain_phrases("We carry tech debt and must modernize our stack.", PAIN.phrases)
    up = _pain_update(lead, m, PAIN, label="rescan")
    assert up["pain_phrases"] == ["tech debt", "modernize"]
    assert up["intent_score"] == 0.7
    assert "modernize" in up["score_reasons"][-1] and "rescan" in up["score_reasons"][-1]


def test_rescan_respects_the_cap_already_spent():
    lead = {"pain_phrases": ["tech debt", "Key Vault"], "intent_score": 0.8}
    m = match_pain_phrases("tech debt, Key Vault, and we must modernize", PAIN.phrases)
    up = _pain_update(lead, m, PAIN, label="rescan")
    assert up["pain_phrases"][-1] == "modernize"
    assert "intent_score" not in up  # +0.20 was already applied
    assert "already at cap" in up["score_reasons"][-1]


def test_rescan_with_nothing_new_writes_nothing():
    lead = {"pain_phrases": ["tech debt"], "intent_score": 0.7}
    m = match_pain_phrases("only tech debt here", PAIN.phrases)
    assert _pain_update(lead, m, PAIN, label="rescan") is None


def test_never_scanned_lead_with_no_match_is_marked_scanned():
    m = match_pain_phrases("nothing relevant", PAIN.phrases)
    assert _pain_update({"pain_phrases": None}, m, PAIN, label="backfill") == {"pain_phrases": []}


def test_modernize_is_literal_not_modernization():
    assert not match_pain_phrases("a modernization effort", ["modernize"])
    assert match_pain_phrases("help us modernize the platform", ["modernize"])


class _Posting:
    def __init__(self, url, text):
        self.url, self.description_text = url, text


class _Boards:
    def __init__(self, postings):
        self.postings, self.board_status = postings, {}

    def fetch_board(self, ref):
        return self.postings


def test_scan_prefers_the_leads_own_posting(monkeypatch):
    from agents.marketing import mkt_lead_finder
    monkeypatch.setattr(mkt_lead_finder, "_get_icp_config",
                        lambda db, pid: {"pain_phrases": ["modernize", "Key Vault"]})
    db = MemDB(mse_leads=[{"id": "l1", "product_id": "p1", "source": "job_posting_signal",
                           "job_posting_url": "https://job-boards.greenhouse.io/acme/jobs/111",
                           "pain_phrases": [], "intent_score": 0.5}])
    boards = _Boards([_Posting("https://boards.greenhouse.io/acme/jobs/222", "Key Vault everywhere"),
                      _Posting("https://boards.greenhouse.io/acme/jobs/111", "We need to modernize")])
    out = scan_pain_phrases(db, "p1", rescan=True, client=boards)
    lead = db.tables["mse_leads"][0]
    assert lead["pain_phrases"] == ["modernize"]  # not the sibling posting's Key Vault
    assert out["own_posting"] == 1 and out["newly_matched_leads"] == 1


# ── partner lane order ───────────────────────────────────────────────────

def test_partner_lane_order():
    items = [{"partner_type": t} for t in ("ai_agency", "fractional_ciso", "vc_operating_partner",
                                           "compliance_firm", None, "vc_operating_partner")]
    got = [i["partner_type"] for i in order_partner_lane(items, "partners", 50)]
    assert got == ["vc_operating_partner", "vc_operating_partner", "compliance_firm",
                   "fractional_ciso", "ai_agency", None]
    assert partner_rank("vc_operating_partner") == 0


def test_prospect_lane_order_untouched():
    items = [{"partner_type": "ai_agency"}, {"partner_type": None}]
    assert order_partner_lane(items, "prospects", 50) == items


# ── partner discovery: new firms first ───────────────────────────────────

def test_known_firms_do_not_eat_the_max_firms_cap(monkeypatch):
    from agents.marketing import partner_sourcing

    class _Scraper:
        def _search(self, q, offset=0):
            self.offset = offset
            return [{"link": f"https://known{i}.com/"} for i in range(3)] + [{"link": "https://newfirm.com/"}]

    seen = []
    monkeypatch.setattr(partner_sourcing, "_allowed", lambda host, path, http_get: seen.append(host) or False)
    s = _Scraper()
    _, stats = partner_sourcing.find_partner_leads(
        [{"segment": "compliance_firm", "query": "soc 2 audit firm"}], scraper=s, http_get=lambda *a, **k: None,
        max_queries=1, max_firms=1, existing_domains={"known0.com", "known1.com", "known2.com"},
        sleep=lambda x: None, page_offset=2)
    assert seen == ["newfirm.com"]
    assert stats.duplicates == 3 and s.offset == 2


# ── 8am enrollment: skip codes, watchdog ─────────────────────────────────

@pytest.mark.parametrize("reason,code", [
    ("contact_status='pending', need 'found'", "no_named_buyer"),
    ("contact fit rejected -- COO is not a buyer", "contact_fit_rejected"),
    ("a pending_hitl sequence already exists", "sequence_exists"),
    ("no title: contact fit cannot be assessed without one", "no_title"),
])
def test_skip_codes(reason, code):
    assert skip_code(reason) == code


def test_run_records_audit_row_and_skips(monkeypatch):
    from agents.marketing import mkt_o2_cold_dm_writer
    from api.routers.linkedin_intake import run_linkedin_dm_sequences

    monkeypatch.setattr(mkt_o2_cold_dm_writer, "run_o2_for_linkedin_leads", lambda **k: {
        "sequences_written": 0, "pending_dm_total": 1, "skip_reasons": {"contact_fit_rejected": 1},
        "skipped": [{"lead_id": "l1", "company": "Clutch", "code": "contact_fit_rejected",
                     "reason": "contact fit rejected -- COO"}]})
    db = MemDB(mse_linkedin_leads=[], mse_leads=[{"product_id": "p1", "status": "pending_dm"}],
               mse_research_reports=[])
    out = run_linkedin_dm_sequences(db)
    assert out["errors"] == {}
    audit = db.tables["audit_log"][0]
    assert audit["action"] == "linkedin_dm_sequences_run" and audit["outcome"] == "win"
    skip = db.tables["mse_dm_enrollment_skips"][0]
    assert skip["reason"] == "contact_fit_rejected" and "Clutch" in skip["detail"]


def test_run_records_a_lose_when_a_product_raises(monkeypatch):
    from agents.marketing import mkt_o2_cold_dm_writer
    from api.routers.linkedin_intake import run_linkedin_dm_sequences

    def boom(**k):
        raise RuntimeError("Server disconnected")
    monkeypatch.setattr(mkt_o2_cold_dm_writer, "run_o2_for_linkedin_leads", boom)
    db = MemDB(mse_linkedin_leads=[], mse_leads=[{"product_id": "p1", "status": "pending_dm"}],
               mse_research_reports=[])
    out = run_linkedin_dm_sequences(db)
    assert "p1" in out["errors"]
    assert db.tables["audit_log"][0]["outcome"] == "lose"


def test_watchdog_quiet_when_todays_run_won():
    db = MemDB(audit_log=[{"action": "linkedin_dm_sequences_run", "outcome": "win",
                           "created_at": "2026-11-01T15:00:40+00:00", "metadata": {}}])
    sent = []
    out = dm_watchdog.check(db, now=datetime(2026, 11, 1, 15, 45, tzinfo=UTC), send=sent.append)
    assert out["alerted"] is False and not sent


def test_watchdog_emails_when_no_run_today(monkeypatch):
    monkeypatch.setenv("KELVIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "outreach@outreach.example.com")
    db = MemDB(audit_log=[{"action": "linkedin_dm_sequences_run", "outcome": "win",
                           "created_at": "2026-10-31T15:00:40+00:00", "metadata": {}}])
    sent = []
    out = dm_watchdog.check(db, now=datetime(2026, 11, 1, 15, 45, tzinfo=UTC),
                            send=lambda p: sent.append(p) or {"id": "e1"})
    assert out["alerted"] is True
    assert sent[0]["from"].endswith("<alerts@outreach.example.com>") and sent[0]["to"] == ["owner@example.com"]
    assert "No 08:00 LinkedIn enrollment run" in sent[0]["text"]


def test_watchdog_emails_on_a_failed_run(monkeypatch):
    monkeypatch.setenv("KELVIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "outreach@outreach.example.com")
    db = MemDB(audit_log=[{"action": "linkedin_dm_sequences_run", "outcome": "lose",
                           "created_at": "2026-11-01T15:00:40+00:00",
                           "metadata": {"errors": {"p1": "RuntimeError: boom"}}}])
    sent = []
    dm_watchdog.check(db, now=datetime(2026, 11, 1, 15, 45, tzinfo=UTC), send=lambda p: sent.append(p) or {})
    assert "boom" in sent[0]["text"]


def test_watchdog_not_due_before_0845_mst():
    assert dm_watchdog.due_period(datetime(2026, 11, 1, 15, 44, tzinfo=UTC)) is None
    assert dm_watchdog.due_period(datetime(2026, 11, 1, 15, 45, tzinfo=UTC)) == "2026-11-01"


# ── Loop Map lines ───────────────────────────────────────────────────────

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)


def _S(**kw):
    base = {"runs": [], "leads": [], "seqs": [], "convs": [], "brave_budget": {"allowed": True},
            "dm_runs": [], "dm_skips": []}
    base.update(kw)
    return base


def _run(src, status="complete", days_ago=1, leads=5, **extra):
    t = (NOW - timedelta(days=days_ago)).isoformat()
    return {"product_id": "p1", "status": status, "started_at": t, "completed_at": t,
            "leads_found": leads, "sources_used": [src], "funnel_stats": extra.get("funnel", {}),
            "error_message": extra.get("error")}


def test_partner_desk_uses_a_monthly_window():
    S = _S(runs=[_run("partner_signal", days_ago=20)])
    st = evaluate("outbound.lead_run", S, "p1", NOW)
    assert st.status == "working"
    S = _S(runs=[_run("partner_signal", days_ago=40)])
    assert "expected monthly" in evaluate("outbound.lead_run", S, "p1", NOW).evidence


def test_weekly_desk_still_weekly():
    S = _S(runs=[_run("job_posting_signal", days_ago=10)])
    st = evaluate("outbound.lead_run", S, "p1", NOW)
    assert st.status == "blocked" and "expected weekly" in st.evidence


def test_hn_line_on_find_desk_and_failure_blocks():
    S = _S(runs=[_run("job_posting_signal"), _run("hn_thread", leads=4)])
    st = evaluate("outbound.lead_run", S, "p1", NOW)
    assert st.status == "working" and "Hacker News scout" in st.evidence and "4 new leads" in st.evidence
    S = _S(runs=[_run("job_posting_signal"), _run("hn_thread", status="failed", error="HN 503")])
    st = evaluate("outbound.lead_run", S, "p1", NOW)
    assert st.status == "blocked" and "HN 503" in st.evidence


def test_pain_rescan_line_on_qualify_desk():
    S = _S(runs=[_run("job_posting_signal", funnel={"leads_qualified": 6}),
                 _run("pain_rescan", leads=0, funnel={"scanned": 80, "newly_matched_leads": 7})])
    st = evaluate("outbound.qualify", S, "p1", NOW)
    assert st.status == "working" and "80 leads re-read, 7 gained a pain phrase" in st.evidence
    # and the rescan never stands in for the weekly sweep
    assert "Run completed" in evaluate("outbound.lead_run", S, "p1", NOW).evidence


def test_draft_desk_shows_skip_reasons():
    run_at = "2026-11-02T15:00:02.123456+00:00"
    S = _S(seqs=[{"product_id": "p1", "status": "pending_hitl", "created_at": "2026-11-02T15:00:30+00:00"}],
           dm_runs=[{"created_at": "2026-11-02T15:00:40+00:00", "outcome": "win", "metadata": {"run_at": run_at}}],
           dm_skips=[{"product_id": "p1", "run_at": "2026-11-02T15:00:02.123+00:00", "reason": "contact_fit_rejected"},
                     {"product_id": "p1", "run_at": "2026-11-02T15:00:02.123+00:00", "reason": "contact_fit_rejected"},
                     {"product_id": "p2", "run_at": "2026-11-02T15:00:02.123+00:00", "reason": "no_title"}])
    st = evaluate("outbound.draft_enrollment", S, "p1", NOW)
    assert "skipped 2: 2 contact fit rejected" in st.evidence


def test_draft_desk_blocked_when_todays_run_missing():
    S = _S(seqs=[{"product_id": "p1", "status": "pending_hitl", "created_at": "2026-11-01T15:00:30+00:00"}],
           dm_runs=[{"created_at": "2026-11-01T15:00:40+00:00", "outcome": "win", "metadata": {}}])
    st = evaluate("outbound.draft_enrollment", S, "p1", NOW)
    assert st.status == "blocked" and "No 08:00 enrollment run today" in st.evidence


def test_d6_topics_blocked_on_a_dead_scheduler_despite_recent_topics():
    S = _S(products=[{"id": "d6", "slug": "decodedsix"}],
           d6={"slug": "decodedsix", "topics_7d": 6, "topics_latest": "2026-11-01T22:14:00+00:00",
               "discovery_latest": "2026-11-01T22:00:00+00:00"})
    st = evaluate("d6.topics", S, "d6", datetime(2026, 11, 2, 18, 0, tzinfo=UTC))
    assert st.status == "blocked" and "has not fetched since" in st.evidence
    S["d6"]["discovery_latest"] = "2026-11-02T16:00:00+00:00"
    assert evaluate("d6.topics", S, "d6", datetime(2026, 11, 2, 18, 0, tzinfo=UTC)).status == "working"


def test_funnel_trend_survives_an_unreadable_source():
    from api.routers.loop_map import _funnel_trend
    S = {"leads": {"_error": "RemoteProtocolError"}, "seqs": [], "convs": []}
    assert _funnel_trend(S, "p1", NOW)  # no AttributeError


def test_brave_spend_includes_domain_lookups():
    """Domain-resolution lookups were left out of the ledger (2026-10-19)."""
    from agents.marketing.company_first_sourcing import FunnelStats
    from agents.marketing.mkt_lead_finder import _record_brave_spend
    stats = FunnelStats()
    stats.queries_discovery, stats.queries_decision_maker, stats.brave_domain_queries = 20, 10, 6
    db = MemDB()
    assert _record_brave_spend(db, stats) == 36
    assert db.tables["usage_events"][0]["metadata"]["count"] == 36
