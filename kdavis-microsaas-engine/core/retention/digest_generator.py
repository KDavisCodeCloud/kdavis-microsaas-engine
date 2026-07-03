import json
from datetime import datetime, timezone, timedelta
from core.supabase_client import get_supabase
from core.llm_router import analyze
from core.sanitization import DataSanitizationShield

_SYSTEM_PROMPT = """You generate weekly value digest emails for B2B SaaS customers.
Your output is plain text (no HTML). Be specific about numbers and outcomes.
Never use marketing language. Prove ROI with the data provided.
Keep it under 200 words. Address the reader as "your team"."""


def generate_digest(tenant_id: str) -> dict | None:
    """
    Returns {'subject': str, 'body': str, 'value_metrics': dict} or None if no usage.
    Caller (n8n) handles sending via Resend and logging.
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()

    events = (
        db.table("usage_events")
        .select("event_type,metadata,created_at")
        .eq("tenant_id", tenant_id)
        .gte("created_at", week_ago)
        .execute()
        .data
    )

    if not events:
        return None

    tenant = db.table("tenants").select("name,tier").eq("id", tenant_id).single().execute().data

    event_summary = _summarize_events(events)
    safe_summary = DataSanitizationShield.clean(event_summary)

    prompt = f"""Tenant: {tenant['name']} (tier: {tenant['tier']})
Week of: {week_ago[:10]} to {now.date()}
Usage data: {json.dumps(safe_summary)}

Write a weekly value digest email. Subject line on first line, blank line, then body."""

    result = analyze(_SYSTEM_PROMPT, prompt, max_tokens=512)
    lines = result.strip().split("\n", 2)
    subject = lines[0].strip()
    body = lines[2].strip() if len(lines) > 2 else lines[-1].strip()

    return {"subject": subject, "body": body, "value_metrics": event_summary}


def _summarize_events(events: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    return {"total_events": len(events), "by_type": counts}
