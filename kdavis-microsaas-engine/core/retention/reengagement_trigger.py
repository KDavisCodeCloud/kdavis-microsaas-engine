from datetime import datetime, timezone, timedelta
from core.supabase_client import get_supabase


def evaluate_reengagement(tenant_id: str) -> list[str]:
    """
    Evaluate which reengagement sequences should fire for a tenant.
    Returns list of sequence_types triggered this call.

    Called by n8n daily cron — not on every event.
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)
    triggered: list[str] = []

    events_7d = (
        db.table("usage_events")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .gte("created_at", (now - timedelta(days=7)).isoformat())
        .execute()
        .count
    )

    if events_7d == 0:
        _fire_if_not_active(db, tenant_id, "reengagement_7d", triggered)

    events_this_week = events_7d
    events_last_week = (
        db.table("usage_events")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .gte("created_at", (now - timedelta(days=14)).isoformat())
        .lt("created_at", (now - timedelta(days=7)).isoformat())
        .execute()
        .count
    )

    if events_last_week > 0 and events_this_week < events_last_week * 0.5:
        _fire_if_not_active(db, tenant_id, "reengagement_21d", triggered)

    tenant = db.table("tenants").select("stripe_subscription_id,status").eq("id", tenant_id).single().execute().data
    if tenant and tenant.get("stripe_subscription_id") and events_7d < 5:
        _fire_if_not_active(db, tenant_id, "prebilling", triggered)

    return triggered


def _fire_if_not_active(db, tenant_id: str, seq_type: str, triggered: list[str]) -> None:
    existing = (
        db.table("retention_sequences")
        .select("id,status")
        .eq("tenant_id", tenant_id)
        .eq("sequence_type", seq_type)
        .eq("status", "active")
        .execute()
        .data
    )
    if not existing:
        db.table("retention_sequences").insert({
            "tenant_id": tenant_id,
            "sequence_type": seq_type,
            "current_step": 0,
            "last_triggered_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
        }).execute()
        triggered.append(seq_type)
