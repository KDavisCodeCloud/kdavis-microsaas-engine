"""
One company, one product (Kelvin, 2026-11-09).

The same company can qualify for Cloud Decoded and for consulting off the same
job posting. Two approved sequences would put two different pitches from the
same sender in one buyer's inbox, so:

  * every outreach card lists the company's cards under OTHER products, so the
    reviewer sees the overlap;
  * once one of them is approved (anything past pending_hitl that was not
    rejected or archived), approving another is refused -- on the card and,
    because a greyed-out button is only a convenience, in the approve
    endpoint itself.

Companies are matched on registrable domain ("www.acme.com", "acme.com:" and
"https://acme.com/careers" are one company), falling back to the normalised
name when no domain was resolved. The referral-partner lane is a different
relationship and is never compared.
"""

import re
from typing import Optional

from agents.marketing.buyer_gate import registrable_domain

# Statuses that mean "this card is no longer in play".
_DEAD_STATUSES = {"rejected_hitl"}


def company_key(company: Optional[str], domain: Optional[str]) -> Optional[str]:
    """One stable key per company: its registrable domain, else its name."""
    reg = registrable_domain(domain)
    if reg and "." in reg:
        return reg
    name = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    return f"name:{name}" if name else None


def is_dead(status: Optional[str]) -> bool:
    s = status or ""
    return s in _DEAD_STATUSES or s.startswith("archived")


def is_committed(status: Optional[str]) -> bool:
    """Approved, sending, sent, pasted or replied: the company has been claimed."""
    s = status or ""
    return bool(s) and s != "pending_hitl" and not is_dead(s)


def _pattern(value: str) -> str:
    """ilike pattern from a domain or name, with the wildcard and PostgREST
    separator characters removed so the value cannot widen the match."""
    return "%" + re.sub(r"[%_,()*\\]", "", value) + "%"


def company_conflicts(db, lead: dict, product_id: Optional[str], products: dict) -> list[dict]:
    """Cards for the same company under other products, newest state first.

    `lead` needs company and domain; `products` maps product id -> {name}.
    Returns [] for the partner lane and for a lead with no usable identity.
    """
    from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID

    if not lead or product_id == PARTNERS_PRODUCT_ID:
        return []
    key = company_key(lead.get("company"), lead.get("domain"))
    if not key:
        return []

    q = db.table("mse_leads").select("id,product_id,company,domain,status,contact_status")
    if key.startswith("name:"):
        q = q.ilike("company", _pattern((lead.get("company") or "").strip()))
    else:
        q = q.ilike("domain", _pattern(key))
    candidates = q.limit(50).execute().data or []
    others = [
        r for r in candidates
        if r.get("id") != lead.get("id")
        and r.get("product_id") not in (product_id, PARTNERS_PRODUCT_ID)
        and r.get("status") != "rejected_wrong_icp"
        and company_key(r.get("company"), r.get("domain")) == key
    ]
    if not others:
        return []

    seqs = (db.table("mse_dm_sequences")
            .select("id,product_id,status,lead_finder_lead_id")
            .in_("lead_finder_lead_id", [o["id"] for o in others])
            .execute().data or [])
    by_lead: dict[str, list[dict]] = {}
    for s in seqs:
        by_lead.setdefault(s.get("lead_finder_lead_id"), []).append(s)

    out: list[dict] = []
    for o in others:
        name = (products.get(o.get("product_id")) or {}).get("name")
        live = [s for s in by_lead.get(o["id"], []) if not is_dead(s.get("status"))]
        if not live:
            if o.get("contact_status") == "pending":
                out.append({"product_id": o.get("product_id"), "product": name, "lead_id": o["id"],
                            "sequence_id": None, "status": "awaiting_buyer", "committed": False})
            continue
        for s in live:
            out.append({"product_id": s.get("product_id") or o.get("product_id"), "product": name,
                        "lead_id": o["id"], "sequence_id": s.get("id"), "status": s.get("status"),
                        "committed": is_committed(s.get("status"))})
    out.sort(key=lambda c: not c["committed"])
    return out


def conflict_block_reason(conflicts: list[dict]) -> Optional[str]:
    """Why approval is refused, or None while no other product has claimed the company."""
    claimed = next((c for c in conflicts if c.get("committed")), None)
    if not claimed:
        return None
    status = (claimed.get("status") or "").replace("_", " ")
    return (f"Already approved under {claimed.get('product') or 'another product'} ({status}). "
            "One product per company: reject this draft.")
