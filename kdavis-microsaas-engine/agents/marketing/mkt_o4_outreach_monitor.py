"""
MKT-O4 Outreach Monitor.

Scans mse_apollo_leads for a product's cold-outreach leads and buckets
them into no_reply / replied / bounced. Read-only — never sends
anything, never writes back to a lead. A separate, not-yet-built sender
is what actually moves mse_dm_sequences rows out of pending_hitl.

Schema notes (checked before writing —
supabase/migrations/20260709000006_outreach_engine.sql):
- mse_apollo_leads.status only supports
  ('pending', 'dm_sent', 'replied', 'converted') — there is no 'bounced'
  value and no dm_sent_at/updated_at column. "No reply after 3 days" is
  approximated from created_at on rows still in 'dm_sent' — a dedicated
  dm_sent_at timestamp would be more precise but doesn't exist yet.
  "Bounced" is approximated as dm_sent leads with no email on file
  (never actually deliverable), since no real bounce-tracking signal
  exists anywhere in this schema. Both are flagged here, not silently
  invented as if they were real tracked states.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

AGENT_ID = "mkt-o4"
NO_REPLY_THRESHOLD_DAYS = 3


def _emit_event(db, event_type: str, metadata: dict) -> None:
    db.table("usage_events").insert({
        "tenant_id": None,
        "event_type": event_type,
        "metadata": metadata,
    }).execute()


def _write_audit(db, outcome: str, product_id: str, metadata: dict) -> None:
    db.table("audit_log").insert({
        "agent_id": AGENT_ID,
        "action": "outreach_monitor_scan",
        "outcome": outcome,
        "product_id": product_id,
        "metadata": metadata,
    }).execute()


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_o4_outreach_monitor(
    product_id: str,
    days_back: int = 7,
    supabase_client: Optional[Any] = None,
) -> dict:
    """
    Reads leads created in the last `days_back` days and buckets them.
    Raises on any failure — never fails silently. Returns
    {"no_reply": list, "replied": list, "bounced": list}.
    """
    db = supabase_client if supabase_client is not None else get_supabase()
    window_start = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=NO_REPLY_THRESHOLD_DAYS)

    try:
        result = (
            db.table("mse_apollo_leads")
            .select("*")
            .eq("product_id", product_id)
            .gte("created_at", window_start)
            .execute()
        )
        leads = result.data or []

        no_reply: list[dict] = []
        replied: list[dict] = []
        bounced: list[dict] = []

        for lead in leads:
            status = lead.get("status")

            if status in ("replied", "converted"):
                replied.append(lead)
                continue

            if not lead.get("email"):
                bounced.append(lead)
                continue

            if status == "dm_sent":
                sent_at = _parse_ts(lead.get("created_at"))
                if sent_at is not None and sent_at < stale_cutoff:
                    no_reply.append(lead)

        counts = {"no_reply": len(no_reply), "replied": len(replied), "bounced": len(bounced)}
        _write_audit(db, "win", product_id, {"days_back": days_back, **counts})
        _emit_event(db, "outreach_monitor_scan_completed", {"product_id": product_id, **counts})

        return {"no_reply": no_reply, "replied": replied, "bounced": bounced}

    except Exception as exc:
        _write_audit(db, "lose", product_id, {"days_back": days_back, "error": str(exc)})
        raise RuntimeError(f"MKT-O4 outreach monitor failed for product {product_id}: {exc}") from exc
