"""
Pattern database manager for core/email_finder.py — reads and writes
mse_email_patterns (supabase/migrations/20260814000024_lead_finder.sql).
Tracks success_count/attempt_count per (domain, pattern) so the most
successful pattern for a domain floats to the top of success_rate
automatically as verified sends accumulate over time, without ever
paying a per-lookup email-finding API for something already known.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from core.supabase_client import get_supabase

# Order tried when a domain has no learned pattern history yet — the most
# statistically common professional email patterns, per the task spec.
DEFAULT_PATTERN_ORDER = ["firstname", "firstname.lastname", "flastname", "firstnamelastname"]


def get_known_patterns(domain: str, supabase_client: Optional[Any] = None) -> list[dict]:
    """This domain's known patterns, best-performing (success_rate desc)
    first — empty list for a domain never seen before, never raises."""
    db = supabase_client or get_supabase()
    result = (
        db.table("mse_email_patterns")
        .select("*")
        .eq("domain", domain)
        .order("success_rate", desc=True)
        .execute()
    )
    return result.data or []


def record_attempt(domain: str, pattern: str, success: bool, supabase_client: Optional[Any] = None) -> None:
    """Increments attempt_count (+success_count when verified),
    recomputes success_rate, and upserts the (domain, pattern) row —
    called by core/email_finder.py after every SMTP verification attempt,
    successful or not, so the pattern database only ever reflects real
    outcomes."""
    db = supabase_client or get_supabase()
    existing = (
        db.table("mse_email_patterns")
        .select("*")
        .eq("domain", domain)
        .eq("pattern", pattern)
        .maybe_single()
        .execute()
    )
    existing_row = existing.data if existing is not None else None

    attempt_count = (existing_row.get("attempt_count", 0) if existing_row else 0) + 1
    success_count = (existing_row.get("success_count", 0) if existing_row else 0) + (1 if success else 0)
    success_rate = success_count / attempt_count if attempt_count else 0.0

    last_verified_at = (
        datetime.now(timezone.utc).isoformat() if success
        else (existing_row.get("last_verified_at") if existing_row else None)
    )

    db.table("mse_email_patterns").upsert({
        "domain": domain,
        "pattern": pattern,
        "attempt_count": attempt_count,
        "success_count": success_count,
        "success_rate": success_rate,
        "last_verified_at": last_verified_at,
    }, on_conflict="domain,pattern").execute()
