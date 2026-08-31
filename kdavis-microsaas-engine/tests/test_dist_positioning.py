import json

import pytest

from agents.dist import positioning_researcher, wedge_validator


class FakeQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._filters = []
        self._payload = None
        self._order_desc = False
        self._limit = None

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, key, value):
        self._filters.append((key, value))
        return self

    def order(self, *args, **kwargs):
        self._order_desc = kwargs.get("desc", False)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self._single = True
        return self

    def _matching(self, rows):
        result = rows
        for key, value in self._filters:
            result = [r for r in result if r.get(key) == value]
        return result

    def execute(self):
        rows = self.store.data.get(self.name, [])
        if self._payload is not None and self.name == "mse_positioning" and "id" not in self._payload:
            # insert
            new_row = {**self._payload, "id": f"row-{len(rows) + 1}"}
            self.store.data.setdefault(self.name, []).append(new_row)
            return type("Result", (), {"data": [new_row]})()
        if self._payload is not None:
            # update: matched rows get merged in place
            matched = self._matching(rows)
            for r in matched:
                r.update(self._payload)
            return type("Result", (), {"data": matched})()

        matched = self._matching(rows)
        if self._order_desc:
            matched = sorted(matched, key=lambda r: r.get("version", 0), reverse=True)
        if self._limit is not None:
            matched = matched[: self._limit]
        if getattr(self, "_single", False):
            if not matched:
                return None
            return type("Result", (), {"data": matched[0]})()
        return type("Result", (), {"data": matched})()


class FakeSupabase:
    def __init__(self, data=None):
        self.data = data or {}

    def table(self, name):
        return FakeQuery(self, name)


def _product_row(slug="tradesdesk", pid="p1"):
    return {"id": pid, "slug": slug, "name": "TradesDesk"}


# ---- positioning_researcher.research_positioning -----------------------


async def test_research_positioning_raises_on_missing_product():
    db = FakeSupabase(data={"mse_products": []})
    with pytest.raises(ValueError, match="No mse_products row"):
        await positioning_researcher.research_positioning("nonexistent", "context", supabase_client=db)


async def test_research_positioning_inserts_draft_row(monkeypatch):
    db = FakeSupabase(data={"mse_products": [_product_row()], "mse_positioning": []})

    fake_llm_output = json.dumps({
        "icp": "Solo plumber, 1-3 trucks",
        "trigger_event": "Missed a job because of a scheduling conflict",
        "substitute_set": [
            {"name": "Orcatec", "kind": "free_tool", "price_to_buyer": 0, "monetization": None,
             "switching_cost_hours": 3, "source_url": "https://orcatec.com", "verified_at": "2026-08-30",
             "configurable_options": [], "researched": True},
            {"name": "Jobber", "kind": "paid_tool", "price_to_buyer": 49, "monetization": "subscription",
             "switching_cost_hours": 8, "source_url": "https://getjobber.com", "verified_at": "2026-08-30",
             "configurable_options": [], "researched": True},
            {"name": "Pen and paper", "kind": "do_nothing", "price_to_buyer": 0, "monetization": None,
             "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
        ],
        "wedge": "Flat pricing regardless of crew size",
        "wedge_type": "temporary",
        "wedge_evidence": {"claims": []},
        "price_rationale": "Argued against Orcatec at $0",
        "kill_criteria": "Below $2K MRR by month 6 -> stop funding",
    })
    monkeypatch.setattr(positioning_researcher, "analyze_with_web_search", lambda *a, **k: fake_llm_output)

    row = await positioning_researcher.research_positioning("tradesdesk", "FSM for trades", supabase_client=db)

    assert row["status"] == "draft"
    assert row["version"] == 1
    assert row["product_id"] == "p1"
    assert len(row["substitute_set"]) == 3
    assert any(s["kind"] == "do_nothing" for s in row["substitute_set"])


async def test_research_positioning_increments_version(monkeypatch):
    db = FakeSupabase(data={
        "mse_products": [_product_row()],
        "mse_positioning": [{"id": "existing", "product_id": "p1", "version": 1}],
    })
    fake_llm_output = json.dumps({
        "icp": "x", "trigger_event": "x",
        "substitute_set": [
            {"name": "A", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30", "configurable_options": [], "researched": True},
            {"name": "B", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30", "configurable_options": [], "researched": True},
            {"name": "C", "kind": "do_nothing", "price_to_buyer": 0, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
        ],
        "wedge": "x", "wedge_type": "temporary", "wedge_evidence": {}, "price_rationale": "x", "kill_criteria": "x",
    })
    monkeypatch.setattr(positioning_researcher, "analyze_with_web_search", lambda *a, **k: fake_llm_output)

    row = await positioning_researcher.research_positioning("tradesdesk", "ctx", supabase_client=db)
    assert row["version"] == 2


async def test_research_positioning_strips_markdown_fences(monkeypatch):
    db = FakeSupabase(data={"mse_products": [_product_row()], "mse_positioning": []})
    fenced = "```json\n" + json.dumps({
        "icp": "x", "trigger_event": "x",
        "substitute_set": [
            {"name": "A", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30", "configurable_options": [], "researched": True},
            {"name": "B", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30", "configurable_options": [], "researched": True},
            {"name": "C", "kind": "do_nothing", "price_to_buyer": 0, "monetization": None, "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
        ],
        "wedge": "x", "wedge_type": "temporary", "wedge_evidence": {}, "price_rationale": "x", "kill_criteria": "x",
    }) + "\n```"
    monkeypatch.setattr(positioning_researcher, "analyze_with_web_search", lambda *a, **k: fenced)

    row = await positioning_researcher.research_positioning("tradesdesk", "ctx", supabase_client=db)
    assert row["status"] == "draft"


# ---- wedge_validator.validate_wedge -------------------------------------


async def test_validate_wedge_raises_on_missing_row():
    db = FakeSupabase(data={"mse_positioning": []})
    with pytest.raises(ValueError, match="No mse_positioning row"):
        await wedge_validator.validate_wedge("missing", supabase_client=db)


def _base_brief(wedge_type="structural"):
    return {
        "id": "b1", "product_id": "p1", "icp": "x", "trigger_event": "x",
        "substitute_set": [{"name": "A", "kind": "do_nothing"}],
        "wedge": "x", "wedge_type": wedge_type, "wedge_evidence": {}, "price_rationale": "x",
        "review_log": [],
    }


async def test_validate_wedge_never_writes_approved(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "corrected_wedge_type": "temporary",
        "downgrade_reason": "Substitute could ship this in one sprint",
        "unsourced_claims": ["claim X is asserted, not sourced"],
        "price_rationale_flag": None,
        "q4_evidence": [],
        "verdict": "pending_review",
        "notes": "Real finding.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)

    assert row["status"] == "pending_review"
    assert row["status"] != "approved"
    assert row["wedge_type"] == "temporary"
    assert row["moat_risk"] is True
    assert row["next_review_due"] is None


async def test_validate_wedge_downgrade_sets_moat_risk_true(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "corrected_wedge_type": "structural",
        "downgrade_reason": None,
        "unsourced_claims": [],
        "price_rationale_flag": None,
        "q4_evidence": [],
        "verdict": "pending_review",
        "notes": "Confirmed structural.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["moat_risk"] is False
    assert row["wedge_type"] == "structural"


async def test_validate_wedge_appends_review_log(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [{
        **_base_brief(),
        "review_log": [{"date": "2026-08-01", "reviewer": "DIST-P1", "outcome": "draft", "notes": "initial"}],
    }]})
    findings = json.dumps({
        "corrected_wedge_type": "temporary", "downgrade_reason": "x", "unsourced_claims": [],
        "price_rationale_flag": None, "q4_evidence": [], "verdict": "pending_review", "notes": "second review",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert len(row["review_log"]) == 2
    assert row["review_log"][-1]["notes"] == "second review"


# ---- wedge_validator.validate_wedge — Q4 / three-tier calibration -------


async def test_validate_wedge_q4_jobber_shipping_cadence_resolves_temporary(monkeypatch):
    # Calibration fixture: Jobber shipping comparable custom-object features
    # on ~6-week cycles -- actively closing the gap -> temporary, not
    # execution, even though the feature isn't shipped as of today.
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "corrected_wedge_type": "temporary",
        "downgrade_reason": "Jobber ships comparable custom-object features on ~6-week release cycles per its public changelog",
        "unsourced_claims": [],
        "price_rationale_flag": None,
        "q4_evidence": [],
        "verdict": "pending_review",
        "notes": "Jobber changelog shows custom-field/object work shipped 3 times in the last 18 weeks -- actively closing this gap on a normal cadence.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["wedge_type"] == "temporary"
    assert row["next_review_due"] is None


async def test_validate_wedge_q4_long_standing_free_competitor_resolves_execution(monkeypatch):
    # Calibration fixture: a free competitor that has existed for years
    # without addressing the gap, with no roadmap signal -> execution,
    # with sourced q4_evidence populated.
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "corrected_wedge_type": "execution",
        "downgrade_reason": "Free substitute could technically build this but has not in 6+ years on the market",
        "unsourced_claims": [],
        "price_rationale_flag": None,
        "q4_evidence": [
            {
                "claim": "Substitute has had no public roadmap item or changelog entry addressing this gap since its 2020 launch",
                "source_url": "https://example.com/substitute/changelog",
                "verified_at": "2026-08-31",
            }
        ],
        "verdict": "pending_review",
        "notes": "6 years in market, no roadmap signal, no shipped movement toward this -- underserved today, sourced.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["wedge_type"] == "execution"
    assert row["moat_risk"] is True
    assert row["wedge_evidence"]["q4_evidence"]
    assert row["wedge_evidence"]["q4_evidence"][0]["source_url"]
    assert row["next_review_due"] is not None


async def test_validate_wedge_sph_ach_absorption_resolves_structural(monkeypatch):
    # Calibration fixture: SPH's tenant-fee (ACH) absorption -- closing the
    # gap would break the substitute's own revenue model -> structural,
    # unchanged by the taxonomy amendment.
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "corrected_wedge_type": "structural",
        "downgrade_reason": None,
        "unsourced_claims": [],
        "price_rationale_flag": None,
        "q4_evidence": [],
        "verdict": "pending_review",
        "notes": "Substitutes monetize via the tenant-side ACH/convenience fee -- absorbing it breaks their own revenue model, not a roadmap gap.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["wedge_type"] == "structural"
    assert row["moat_risk"] is False


# ---- B.1: configurable_options requirement -------------------------------


def _substitute(kind="free_tool", configurable_options=None, researched=None, **overrides):
    entry = {
        "name": "X", "kind": kind, "price_to_buyer": 0, "monetization": None,
        "switching_cost_hours": 0, "source_url": "https://example.com", "verified_at": "2026-08-30",
    }
    if configurable_options is not None:
        entry["configurable_options"] = configurable_options
    if researched is not None:
        entry["researched"] = researched
    entry.update(overrides)
    return entry


def test_validate_configurable_options_raises_on_missing_field():
    substitute_set = [_substitute(kind="free_tool")]  # no configurable_options key at all
    with pytest.raises(ValueError, match="missing configurable_options"):
        positioning_researcher._validate_configurable_options(substitute_set)


def test_validate_configurable_options_raises_on_empty_without_researched():
    substitute_set = [_substitute(kind="paid_tool", configurable_options=[])]  # empty, no researched marker
    with pytest.raises(ValueError, match="no researched: true marker"):
        positioning_researcher._validate_configurable_options(substitute_set)


def test_validate_configurable_options_accepts_empty_with_researched_true():
    substitute_set = [_substitute(kind="free_tool", configurable_options=[], researched=True)]
    positioning_researcher._validate_configurable_options(substitute_set)  # must not raise


def test_validate_configurable_options_accepts_populated_array():
    substitute_set = [_substitute(kind="paid_tool", configurable_options=[
        {"option": "landlord absorbs tenant ACH fee", "available": True,
         "cost_shift": "landlord pays instead of tenant",
         "source_url": "https://innago.com/faq", "verified_at": "2026-08-31"},
    ])]
    positioning_researcher._validate_configurable_options(substitute_set)  # must not raise


def test_validate_configurable_options_skips_do_nothing_and_manual_kinds():
    substitute_set = [
        _substitute(kind="do_nothing", name="Pen and paper"),
        _substitute(kind="manual_process", name="Spreadsheet"),
        _substitute(kind="in_house_build", name="Custom tool"),
        _substitute(kind="agency_service", name="Bookkeeper"),
    ]
    for entry in substitute_set:
        entry.pop("configurable_options", None)
        entry.pop("researched", None)
    positioning_researcher._validate_configurable_options(substitute_set)  # must not raise -- no vendor settings page to check


async def test_research_positioning_raises_on_missing_configurable_options(monkeypatch):
    db = FakeSupabase(data={"mse_products": [_product_row()], "mse_positioning": []})
    fake_llm_output = json.dumps({
        "icp": "x", "trigger_event": "x",
        "substitute_set": [
            {"name": "A", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None,
             "switching_cost_hours": 0, "source_url": "https://a.example", "verified_at": "2026-08-30"},
            {"name": "B", "kind": "paid_tool", "price_to_buyer": 1, "monetization": None,
             "switching_cost_hours": 0, "source_url": "https://b.example", "verified_at": "2026-08-30",
             "configurable_options": [], "researched": True},
            {"name": "C", "kind": "do_nothing", "price_to_buyer": 0, "monetization": None,
             "switching_cost_hours": 0, "source_url": None, "verified_at": "2026-08-30"},
        ],
        "wedge": "x", "wedge_type": "temporary", "wedge_evidence": {}, "price_rationale": "x", "kill_criteria": "x",
    })
    monkeypatch.setattr(positioning_researcher, "analyze_with_web_search", lambda *a, **k: fake_llm_output)

    with pytest.raises(ValueError, match="missing configurable_options"):
        await positioning_researcher.research_positioning("tradesdesk", "ctx", supabase_client=db)

    # Nothing inserted -- reject before the DB write, same policy as every other malformed-shape case.
    assert db.data["mse_positioning"] == []


# ---- B.2: Q0 pre-check -----------------------------------------------------


async def test_validate_wedge_q0_fires_forces_invalid(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "q0": {"already_offered": True, "substitute": "Innago",
               "source_url": "https://innago.com/faq", "verified_at": "2026-08-31"},
        "corrected_wedge_type": "invalid",
        "downgrade_reason": "Innago already lets the landlord absorb the tenant ACH fee via account settings",
        "unsourced_claims": [], "price_rationale_flag": None, "q4_evidence": [],
        "verdict": "pending_review", "notes": "Q0 fired -- Innago already offers this as a setting.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["wedge_type"] == "invalid"
    assert row["status"] == "pending_review"  # still owner-reviewed, not auto-rejected
    assert row["moat_risk"] is True
    assert row["wedge_evidence"]["q0"]["already_offered"] is True
    assert row["wedge_evidence"]["q0"]["substitute"] == "Innago"


async def test_validate_wedge_q0_fires_without_source_is_rejected(monkeypatch):
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "q0": {"already_offered": True, "substitute": "Innago", "source_url": None, "verified_at": None},
        "corrected_wedge_type": "invalid",
        "downgrade_reason": "x", "unsourced_claims": [], "price_rationale_flag": None, "q4_evidence": [],
        "verdict": "pending_review", "notes": "x",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    with pytest.raises(ValueError, match="requires both substitute and source_url"):
        await wedge_validator.validate_wedge("b1", supabase_client=db)


async def test_validate_wedge_q0_true_but_wedge_type_not_invalid_is_rejected(monkeypatch):
    # Internal inconsistency in the model's own output -- must not be silently accepted.
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "q0": {"already_offered": True, "substitute": "Innago",
               "source_url": "https://innago.com/faq", "verified_at": "2026-08-31"},
        "corrected_wedge_type": "temporary",  # inconsistent with q0.already_offered=true
        "downgrade_reason": "x", "unsourced_claims": [], "price_rationale_flag": None, "q4_evidence": [],
        "verdict": "pending_review", "notes": "x",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    with pytest.raises(ValueError, match="must be 'invalid'"):
        await wedge_validator.validate_wedge("b1", supabase_client=db)


async def test_validate_wedge_q0_false_leaves_normal_taxonomy_unaffected(monkeypatch):
    # Regression: Q0 not firing must not change any existing temporary/structural/execution behavior.
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "q0": {"already_offered": False, "substitute": None, "source_url": None, "verified_at": None},
        "corrected_wedge_type": "temporary",
        "downgrade_reason": "Could ship in one sprint", "unsourced_claims": [], "price_rationale_flag": None,
        "q4_evidence": [], "verdict": "pending_review", "notes": "Q0 checked, nothing already offered.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["wedge_type"] == "temporary"
    assert row["wedge_evidence"]["q0"]["already_offered"] is False


async def test_validate_wedge_missing_q0_key_defaults_to_not_fired(monkeypatch):
    # Backward compatibility: older-shaped findings with no "q0" key at all
    # (e.g. a stale cached prompt run) must not crash -- defaults to not-fired.
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "corrected_wedge_type": "structural",
        "downgrade_reason": None, "unsourced_claims": [], "price_rationale_flag": None,
        "q4_evidence": [], "verdict": "pending_review", "notes": "No q0 key present.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    row = await wedge_validator.validate_wedge("b1", supabase_client=db)
    assert row["wedge_type"] == "structural"
    assert row["wedge_evidence"]["q0"]["already_offered"] is False


async def test_validate_wedge_execution_verdict_with_empty_q4_evidence_is_rejected(monkeypatch):
    # A.2's hard rule: absence of evidence is not evidence. An LLM claiming
    # "execution" with no sourced q4_evidence must be rejected outright --
    # not silently downgraded, not written to the DB at all.
    db = FakeSupabase(data={"mse_positioning": [_base_brief()]})
    findings = json.dumps({
        "corrected_wedge_type": "execution",
        "downgrade_reason": "Substitute could close this but seems like they haven't",
        "unsourced_claims": [],
        "price_rationale_flag": None,
        "q4_evidence": [],
        "verdict": "pending_review",
        "notes": "Unsourced assertion of inaction.",
    })
    monkeypatch.setattr(wedge_validator, "analyze_with_web_search", lambda *a, **k: findings)

    with pytest.raises(ValueError, match="no sourced q4_evidence"):
        await wedge_validator.validate_wedge("b1", supabase_client=db)

    # Nothing was written -- the row is untouched.
    row = db.table("mse_positioning").select("*").eq("id", "b1").maybe_single().execute()
    assert row.data["wedge_type"] == "structural"
    assert "status" not in row.data
