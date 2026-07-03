from fastapi import APIRouter, Request
from core.retention.reengagement_trigger import evaluate_reengagement

router = APIRouter(prefix="/reengagement", tags=["reengagement"])


@router.post("/evaluate/{tenant_id}")
async def evaluate(tenant_id: str, request: Request):
    """
    Called by n8n daily cron for each active tenant.
    Returns which sequences were triggered this run.
    """
    triggered = evaluate_reengagement(tenant_id)
    return {"tenant_id": tenant_id, "triggered": triggered}
