"""
rag/outcome_writer.py — called from the CEO dashboard's Outcome Feedback
card once real MRR data exists for a launched product. Updates the
matching mse_rag_outcomes row and computes verdict_accuracy so future
retrievals include ground truth, not just the original projection.
"""
import logging
from typing import Optional

from core.supabase_client import get_supabase
from rag.embedder import embed_and_store, _build_embedding_text

log = logging.getLogger(__name__)


def _compute_accuracy(actual: int, projected: Optional[int]) -> Optional[str]:
    if projected is None or projected == 0:
        return None
    delta = (actual - projected) / projected
    if delta > 0.25:
        return "OVER"
    if delta < -0.25:
        return "UNDER"
    return "ACCURATE"


async def write_outcome(
    product_name: str,
    actual_mrr_at_90_days: int,
    actual_mrr_at_180_days: int,
) -> Optional[dict]:
    """
    Matches by product_name -- if multiple rows exist (re-submissions),
    updates the most recent (created_at desc). Returns the updated row's
    key fields, or None if no matching row was found.
    """
    db = get_supabase()

    rows = (
        db.table("mse_rag_outcomes")
        .select("*")
        .eq("product_name", product_name)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        log.warning("[rag.outcome_writer] no mse_rag_outcomes row found for product_name=%s", product_name)
        return None

    row = rows[0]
    accuracy = _compute_accuracy(actual_mrr_at_90_days, row.get("projected_mrr_floor"))
    outcome_actual = f"Launched. ${actual_mrr_at_90_days:,} MRR at 90 days."

    update_fields = {
        "actual_mrr_at_90_days":  actual_mrr_at_90_days,
        "actual_mrr_at_180_days": actual_mrr_at_180_days,
        "verdict_accuracy":       accuracy,
        "outcome_actual":         outcome_actual,
    }
    db.table("mse_rag_outcomes").update(update_fields).eq("id", row["id"]).execute()

    # Re-embed so the vector reflects the full picture (projection + actual
    # outcome), not just the original pre-launch submission.
    full_input = {
        "product_name": row["product_name"],
        "vertical":     row["vertical"],
        "verdict":      row["verdict"],
        "rationale":    f"{row.get('rationale', '')} | Outcome: {outcome_actual} | Accuracy: {accuracy}",
        "pain_themes":  row.get("review_pain_themes") or [],
        "competitor_names": [],
    }
    embed_and_store(row["id"], full_input)

    try:
        db.table("audit_log").insert({
            "agent_id": "rag_outcome_writer",
            "action": "rag_outcome_recorded",
            "outcome": f"{product_name} outcome recorded. Verdict accuracy: {accuracy}.",
            "metadata": {"product_name": product_name, "verdict_accuracy": accuracy, "actual_mrr_at_90_days": actual_mrr_at_90_days},
        }).execute()
    except Exception as e:
        # Activity-log visibility is nice-to-have, never load-bearing for
        # the outcome write itself, which has already succeeded above.
        log.warning("[rag.outcome_writer] audit_log write failed (outcome itself was still saved): %s", e)

    return {
        "id": row["id"],
        "product_name": product_name,
        "verdict_accuracy": accuracy,
        "outcome_actual": outcome_actual,
    }
