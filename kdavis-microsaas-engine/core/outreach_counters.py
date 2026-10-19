"""
The outreach counters -- ONE implementation, three readers.

GET /marketing/outreach/summary (the CEO Decoded Outreach header), the 8am
digest (which calls that endpoint) and GET /marketing/loop-map (the Loop Map's
desk statuses) all count from here. The point is that they cannot disagree:
two count queries drift the moment a status value is added, and the digest
saying "4 awaiting approval" while a desk says 5 is worse than either being
slightly stale.

Pure functions over row lists, so the Loop Map can count per product from the
rows it already loaded instead of issuing a second round of queries.
"""

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

AWAITING_BUYER_CONTACT_STATUSES = ("pending", "needs_review")


def _for_product(rows: Iterable[dict], product_id: Optional[str],
                 exclude: Iterable[str] = ()) -> list[dict]:
    rows = list(rows or [])
    exclude = set(exclude or ())
    if exclude:
        rows = [r for r in rows if r.get("product_id") not in exclude]
    if product_id is None:
        return rows
    return [r for r in rows if r.get("product_id") == product_id]


def count_outreach(leads: Iterable[dict], seqs: Iterable[dict], convs: Iterable[dict],
                   *, product_id: Optional[str] = None,
                   exclude_product_ids: Iterable[str] = (),
                   now: Optional[datetime] = None) -> dict:
    """The five header counters, optionally scoped to one product.

    `leads` need product_id, contact_status; `seqs` need product_id, status,
    touch_1_sent_at; `convs` need product_id, replied_at.
    """
    now = now or datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    today = now.date().isoformat()

    leads = _for_product(leads, product_id, exclude_product_ids)
    seqs = _for_product(seqs, product_id, exclude_product_ids)
    convs = _for_product(convs, product_id, exclude_product_ids)

    return {
        "awaiting_buyer": sum(1 for l in leads
                              if l.get("contact_status") in AWAITING_BUYER_CONTACT_STATUSES),
        "awaiting_approval": sum(1 for s in seqs if s.get("status") == "pending_hitl"),
        # approved_manual is the LinkedIn track: approved copy a human pastes.
        "ready_to_paste": sum(1 for s in seqs if s.get("status") == "approved_manual"),
        "sends_today": sum(1 for s in seqs if (s.get("touch_1_sent_at") or "").startswith(today)),
        # A REAL count as of decision 3 (2026-10-06): replies are marked by hand
        # in the Conversations lane, so 0 means zero replies, not "unmeasured".
        "replies_this_week": sum(1 for c in convs
                                 if c.get("replied_at") and str(c["replied_at"]) >= week_ago),
        "parked_awaiting_contact": sum(1 for s in seqs if s.get("status") == "awaiting_contact"),
        "window_start": week_ago,
    }
