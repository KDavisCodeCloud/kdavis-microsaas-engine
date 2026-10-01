"""
tests/test_buyer_research.py

Buyer-research queue (Kelvin's decision 5b, 2026-10-01).

Scraper v2 qualifies a COMPANY without needing a contact, so the 2026-10-01
runs left five companies parked as company_qualified/contact_pending that
neither free sources nor Brave could name a buyer for. This router turns
those into a human task lane and, when Kelvin pastes a profile, creates the
contact and kicks off email grading.

The validation tests are the important ones: a pasted /company/ URL or a
half-filled form would otherwise be stored as a person and handed to MKT-O2
as asserted fact.
"""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

import api.routers.buyer_research as br


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._op = None
        self._payload = None
        self._single = False
        self._filters: list[tuple] = []

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self._filters.append((k, v))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def execute(self):
        if self._op == "update":
            self.store.writes.append((self.name, self._payload, dict(self._filters)))
            return FakeResult([{"id": "lead-1"}])
        rows = self.store.responses.get(self.name, [])
        # emulate the linkedin_url clash lookup
        for k, v in self._filters:
            if k == "linkedin_url":
                rows = [r for r in self.store.responses.get("_by_linkedin", []) if r.get("linkedin_url") == v]
        if self._single:
            return FakeResult(rows[0] if rows else None)
        return FakeResult(list(rows))


class FakeDb:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.writes = []

    def table(self, name):
        return FakeTable(self, name)


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *a, **kw):
        self.tasks.append((fn, a, kw))


PENDING_LEAD = {
    "id": "lead-1", "product_id": "prod-consulting", "company": "Northwind Systems",
    "domain": "northwind.example", "domain_source": "constructed_verified",
    "job_posting_title": "Senior Platform Engineer",
    "job_posting_url": "https://boards.greenhouse.io/northwind/jobs/1",
    "open_role_count": 2, "size_proxy_open_roles": 12,
    "stack_tags": ["Azure", "Terraform"], "company_tags": [],
    "fit_score": 0.73, "intent_score": 0.68,
    "score_reasons": ["size proxy in band", "domain resolved"],
    "contact_attempts": 2, "location": "Austin, TX",
    "status": "company_qualified", "contact_status": "pending",
}


@pytest.fixture
def no_auth(monkeypatch):
    monkeypatch.setattr(br, "require_marketing_api_key", lambda _a: None)


class TestListTasks:
    @pytest.mark.asyncio
    async def test_task_carries_everything_needed_to_act(self, no_auth, monkeypatch):
        """The lane has to be answerable without opening another tab: the
        domain, the role signal that qualified them, and the titles to look
        for."""
        db = FakeDb({"mse_leads": [PENDING_LEAD]})
        monkeypatch.setattr(br, "get_supabase", lambda: db)

        out = await br.list_buyer_research_tasks(authorization="Bearer k")
        assert out["count"] == 1
        task = out["tasks"][0]
        assert task["headline"] == "Find the buyer at Northwind Systems"
        assert task["domain"] == "northwind.example"
        assert task["role_signal"]["title"] == "Senior Platform Engineer"
        assert task["role_signal"]["open_roles_on_board"] == 12
        assert task["target_titles"], "the human must be told which titles count"
        assert task["why_qualified"], "Stage 1 reasoning must be visible"
        assert task["automated_attempts"] == 2

    @pytest.mark.asyncio
    async def test_cloud_decoded_gets_its_own_target_titles(self, no_auth, monkeypatch):
        lead = dict(PENDING_LEAD, product_id=br.CLOUD_DECODED_PRODUCT_ID)
        db = FakeDb({"mse_leads": [lead]})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        out = await br.list_buyer_research_tasks(authorization="Bearer k")
        assert out["tasks"][0]["target_titles"] == br.TARGET_TITLES_BY_PRODUCT[br.CLOUD_DECODED_PRODUCT_ID]

    @pytest.mark.asyncio
    async def test_empty_queue_is_not_an_error(self, no_auth, monkeypatch):
        monkeypatch.setattr(br, "get_supabase", lambda: FakeDb({"mse_leads": []}))
        out = await br.list_buyer_research_tasks(authorization="Bearer k")
        assert out == {"tasks": [], "count": 0}


class TestSaveContactValidation:
    """A bad paste must be refused, not stored. These fields are handed to
    MKT-O2 as asserted fact."""

    @pytest.mark.parametrize("url", [
        "https://www.linkedin.com/company/northwind",        # company, not person
        "https://www.linkedin.com/search/results/people/",   # a search
        "https://example.com/in/janedoe",                    # not linkedin
        "janedoe",                                           # not a url
        "",
    ])
    def test_non_profile_urls_are_rejected(self, url):
        with pytest.raises(ValidationError):
            br.SaveContactRequest(linkedin_url=url, name="Jane Doe", title="CTO")

    @pytest.mark.parametrize("url", [
        "https://www.linkedin.com/in/janedoe",
        "https://linkedin.com/in/jane-doe-123/",
        "https://uk.linkedin.com/in/janedoe",
    ])
    def test_real_profile_urls_are_accepted(self, url):
        assert br.SaveContactRequest(linkedin_url=url, name="Jane Doe", title="CTO")

    @pytest.mark.parametrize("field", ["name", "title"])
    def test_blank_name_or_title_is_rejected(self, field):
        kwargs = {"linkedin_url": "https://www.linkedin.com/in/x", "name": "Jane Doe", "title": "CTO"}
        kwargs[field] = "   "
        with pytest.raises(ValidationError):
            br.SaveContactRequest(**kwargs)


class TestSaveContact:
    def _req(self):
        return br.SaveContactRequest(
            linkedin_url="https://www.linkedin.com/in/janedoe", name="Jane Doe", title="VP of Engineering")

    @pytest.mark.asyncio
    async def test_saves_contact_and_moves_the_lead_into_the_mkt_o2_queue(self, no_auth, monkeypatch):
        db = FakeDb({"mse_leads": [PENDING_LEAD], "_by_linkedin": []})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        tasks = FakeBackgroundTasks()

        out = await br.save_contact("lead-1", self._req(), tasks, authorization="Bearer k")

        assert out["contact_status"] == "found"
        assert out["status"] == "pending_dm"
        assert out["email_grading"] == "queued"
        payload = db.writes[0][1]
        assert payload["first_name"] == "Jane"
        assert payload["last_name"] == "Doe"
        assert payload["title"] == "VP of Engineering"
        assert payload["linkedin_url"] == "https://www.linkedin.com/in/janedoe"
        assert len(tasks.tasks) == 1, "grading must run in the background, not inline"

    @pytest.mark.asyncio
    async def test_grading_is_not_queued_without_a_domain(self, no_auth, monkeypatch):
        """And says so, rather than silently skipping."""
        db = FakeDb({"mse_leads": [dict(PENDING_LEAD, domain=None)], "_by_linkedin": []})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        tasks = FakeBackgroundTasks()
        out = await br.save_contact("lead-1", self._req(), tasks, authorization="Bearer k")
        assert "no domain" in out["email_grading"]
        assert tasks.tasks == []

    @pytest.mark.asyncio
    async def test_single_word_name_does_not_queue_a_pattern_guess(self, no_auth, monkeypatch):
        db = FakeDb({"mse_leads": [PENDING_LEAD], "_by_linkedin": []})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        tasks = FakeBackgroundTasks()
        req = br.SaveContactRequest(
            linkedin_url="https://www.linkedin.com/in/cher", name="Cher", title="CTO")
        out = await br.save_contact("lead-1", req, tasks, authorization="Bearer k")
        assert "first and last name" in out["email_grading"]
        assert tasks.tasks == []

    @pytest.mark.asyncio
    async def test_duplicate_profile_is_a_409_naming_the_other_company(self, no_auth, monkeypatch):
        """linkedin_url is UNIQUE; the raw database error would be opaque."""
        db = FakeDb({
            "mse_leads": [PENDING_LEAD],
            "_by_linkedin": [{"id": "other-lead", "company": "Globex",
                              "linkedin_url": "https://www.linkedin.com/in/janedoe"}],
        })
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        with pytest.raises(br.HTTPException) as exc:
            await br.save_contact("lead-1", self._req(), FakeBackgroundTasks(), authorization="Bearer k")
        assert exc.value.status_code == 409
        assert "Globex" in exc.value.detail

    @pytest.mark.asyncio
    async def test_unknown_lead_is_a_404(self, no_auth, monkeypatch):
        monkeypatch.setattr(br, "get_supabase", lambda: FakeDb({"mse_leads": []}))
        with pytest.raises(br.HTTPException) as exc:
            await br.save_contact("nope", self._req(), FakeBackgroundTasks(), authorization="Bearer k")
        assert exc.value.status_code == 404


class TestEmailGradingTask:
    def test_verified_on_a_catch_all_domain_is_stored_as_risky(self, monkeypatch):
        from core.email_finder import EmailResult, VerificationResult

        db = FakeDb({})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        with patch("core.email_finder.find_email", return_value=EmailResult(
                    email="jane.doe@northwind.example", pattern_used="first.last",
                    verification_status="verified", confidence_score=0.95)), \
             patch("core.email_finder.verify_email", return_value=VerificationResult(
                    status="verified", smtp_code=250, message="ok")):
            br._grade_email_for_lead("lead-1", "Jane", "Doe", "northwind.example")

        payload = db.writes[-1][1]
        assert payload["email_grade"] == "risky", (
            "a catch-all accepts every RCPT TO, so a 250 proves the domain answers, "
            "not that the mailbox exists"
        )

    def test_clean_verified_domain_is_valid(self, monkeypatch):
        from core.email_finder import EmailResult, VerificationResult

        db = FakeDb({})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        with patch("core.email_finder.find_email", return_value=EmailResult(
                    email="jane.doe@northwind.example", pattern_used="first.last",
                    verification_status="verified", confidence_score=0.95)), \
             patch("core.email_finder.verify_email", return_value=VerificationResult(
                    status="invalid", smtp_code=550, message="no such user")):
            br._grade_email_for_lead("lead-1", "Jane", "Doe", "northwind.example")
        assert db.writes[-1][1]["email_grade"] == "valid"

    def test_a_grading_failure_is_recorded_not_left_null(self, monkeypatch):
        """'unknown' with no address is a real, actionable result -- it routes
        the lead to the manual track. NULL would read as 'never graded'."""
        db = FakeDb({})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        with patch("core.email_finder.find_email", side_effect=RuntimeError("smtp exploded")):
            br._grade_email_for_lead("lead-1", "Jane", "Doe", "northwind.example")
        assert db.writes[-1][1]["email_grade"] == "unknown"


class TestSkip:
    @pytest.mark.asyncio
    async def test_skip_leaves_the_lane_without_deleting(self, no_auth, monkeypatch):
        db = FakeDb({"mse_leads": [PENDING_LEAD]})
        monkeypatch.setattr(br, "get_supabase", lambda: db)
        out = await br.skip_task("lead-1", authorization="Bearer k")
        assert out["contact_status"] == "none_found"
        assert db.writes[0][1] == {"contact_status": "none_found"}
