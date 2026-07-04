from pathlib import Path

_SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

MRR_FLOOR        = 4000
SCORE_READY      = 80
SCORE_VALIDATED  = 60
SCORE_WATCH      = 40

_ALL_GATES = [
    "gate_1_mrr_floor",
    "gate_2_mrr_math",
    "gate_3_stack_compatibility",
    "gate_4_pain_evidence",
    "gate_5_retention_hooks",
    "gate_6_competition_density",
    "gate_7_confidence_floor",
]


def run(raw_findings: list[dict]) -> list[dict]:
    """Evaluate every opportunity card through all 7 gates. Returns stamped results."""
    return [_evaluate(opp) for opp in raw_findings]


def _evaluate(opp: dict) -> dict:
    gates = {}

    # Gate 1 — MRR floor
    mrr = opp.get("conservative_mrr_potential", 0)
    gates["gate_1_mrr_floor"] = "pass" if mrr >= MRR_FLOOR else "fail"
    if gates["gate_1_mrr_floor"] == "fail":
        return _reject(opp, gates, f"MRR floor not met. Potential: ${mrr:,.0f}. Minimum: $4,000.")

    # Gate 2 — MRR math validity
    mrr_calc = (opp.get("mrr_calculation") or "").strip()
    gates["gate_2_mrr_math"] = "pass" if len(mrr_calc) > 15 else "fail"
    if gates["gate_2_mrr_math"] == "fail":
        return _reject(opp, gates, "MRR calculation missing or insufficient. Must show customer count × price math.")

    # Gate 3 — Stack compatibility
    stack_ok = opp.get("stack_compatible", False)
    notes = (opp.get("stack_compatibility_notes") or "").lower()
    blockers = ["hipaa", "soc 2", "pci", "proprietary api", "third-party licensing"]
    has_blocker = any(b in notes for b in blockers)
    gates["gate_3_stack_compatibility"] = "fail" if not stack_ok or has_blocker else "pass"
    if gates["gate_3_stack_compatibility"] == "fail":
        reason = opp.get("stack_compatibility_notes") or "stack_compatible is false"
        return _reject(opp, gates, f"Stack incompatibility: {reason}.")

    # Gate 4 — Pain point evidence (minimum 2 specific sources)
    sources = opp.get("source_evidence") or []
    gates["gate_4_pain_evidence"] = "pass" if len(sources) >= 2 else "fail"
    if gates["gate_4_pain_evidence"] == "fail":
        return _reject(opp, gates, f"Insufficient pain point evidence. {len(sources)} source(s) found, minimum 2 required.")

    # Gate 5 — Retention hooks completeness
    hooks     = opp.get("retention_hooks") or {}
    required  = ["weekly_value_metric", "adjacent_pain", "natural_integration", "churn_risk_window"]
    missing   = [f for f in required if not hooks.get(f)]
    milestones = hooks.get("milestone_sequence") or []
    if len(milestones) < 3:
        missing.append("milestone_sequence (minimum 3 entries)")
    gates["gate_5_retention_hooks"] = "fail" if missing else "pass"
    if gates["gate_5_retention_hooks"] == "fail":
        return _reject(opp, gates, f"Retention hooks incomplete. Missing: {', '.join(missing)}.")

    # Gate 6 — Competition density
    density = (opp.get("competition_density") or "red").lower()
    score   = opp.get("build_confidence_score", 0)
    gates["gate_6_competition_density"] = "fail" if density == "red" and score < 80 else "pass"
    if gates["gate_6_competition_density"] == "fail":
        return _reject(opp, gates,
            f"Red competition density without sufficient differentiation "
            f"(confidence: {score}/100 — minimum 80 required in red markets).")

    # Gate 7 — Build confidence floor
    if score < SCORE_WATCH:
        gates["gate_7_confidence_floor"] = "fail"
        return _reject(opp, gates, f"Build confidence score too low: {score}/100. Minimum to proceed: 40.")
    elif score < SCORE_VALIDATED:
        gates["gate_7_confidence_floor"] = "watch"
        status = "watch"
    elif score < SCORE_READY:
        gates["gate_7_confidence_floor"] = "validated"
        status = "validated"
    else:
        gates["gate_7_confidence_floor"] = "ready"
        status = "READY_TO_BUILD"

    return {
        "opportunity_id":      opp.get("opportunity_id", ""),
        "vertical":            opp.get("vertical", ""),
        "solution_concept":    opp.get("solution_concept", ""),
        "gate_results":        gates,
        "status":              status,
        "rejection_reason":    None,
        "aggregator_notes":    _build_notes(opp, status),
        "recommended_owner":   "Kelvin",
        "recommended_build_slot": opp.get("recommended_thursday_build_slot", "TBD"),
    }


def _reject(opp: dict, gates: dict, reason: str) -> dict:
    for g in _ALL_GATES:
        gates.setdefault(g, "skipped")
    return {
        "opportunity_id":       opp.get("opportunity_id", ""),
        "vertical":             opp.get("vertical", ""),
        "solution_concept":     opp.get("solution_concept", ""),
        "gate_results":         gates,
        "status":               "rejected",
        "rejection_reason":     reason,
        "aggregator_notes":     None,
        "recommended_owner":    None,
        "recommended_build_slot": None,
    }


def _build_notes(opp: dict, status: str) -> str | None:
    notes = []
    score = opp.get("build_confidence_score", 0)
    mrr   = opp.get("conservative_mrr_potential", 0)

    if status == "validated":
        notes.append(f"Confidence {score}/100 — requires Kelvin manual review before build.")
    if status == "watch":
        notes.append(f"Borderline confidence ({score}/100). Monitor for additional signal.")
    if mrr > 8000:
        notes.append(f"High MRR potential: ${mrr:,.0f}/mo.")

    return " ".join(notes) if notes else None
