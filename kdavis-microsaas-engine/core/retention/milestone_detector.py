from datetime import datetime, timezone
from core.supabase_client import get_supabase


def check_milestones(tenant_id: str, event_type: str) -> list[str]:
    """
    Called after every usage_event insert.
    Returns list of milestone_keys newly achieved this call.
    """
    db = get_supabase()

    event_count = (
        db.table("usage_events")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .execute()
        .count
    )

    pending = (
        db.table("milestones")
        .select("id,milestone_key,threshold")
        .eq("tenant_id", tenant_id)
        .is_("achieved_at", "null")
        .lte("threshold", event_count)
        .execute()
        .data
    )

    achieved: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for m in pending:
        db.table("milestones").update({"achieved_at": now}).eq("id", m["id"]).execute()
        achieved.append(m["milestone_key"])

    return achieved
