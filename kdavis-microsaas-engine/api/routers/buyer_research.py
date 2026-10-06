"""
Buyer-research queue API (Kelvin's decision 5b, 2026-10-01).

Scraper v2's two-stage model qualifies a COMPANY without needing a contact
(matching role + resolved domain + exclusions + size proxy), and parks it as
status='company_qualified' / contact_status='pending'. Free sources
(agents/marketing/contact_discovery.py) and one budgeted Brave query are
tried automatically, but the 2026-10-01 runs show plenty of companies where
neither finds a named buyer.

Rather than leave those sitting, this exposes them as a human task lane:
"Find the buyer at {company}", with the domain, the role signal that
qualified them, and the titles worth looking for. Kelvin pastes the LinkedIn
URL, name and title; saving creates the contact and kicks off email pattern
+ SMTP + catch-all grading, so the lead moves into the normal MKT-O2 queue.

Same internal-secret auth as every other marketing router here
(MARKETING_API_KEY) -- ceo-dashboard proxies to it with that key.
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, field_validator

from agents.marketing.mkt_lead_finder import CLOUD_DECODED_PRODUCT_ID
from api.middleware.auth import require_marketing_api_key
from core.linkedin_urls import PROFILE_URL_HELP, is_profile_url
from core.supabase_client import get_supabase

log = logging.getLogger(__name__)

router = APIRouter(prefix="/marketing/buyer-research", tags=["marketing", "buyer-research"])

# Titles worth looking for, surfaced to the human so the lane is actionable
# without them having to remember the ICP. Mirrors
# company_first_sourcing.DECISION_MAKER_TITLES.
TARGET_TITLES_BY_PRODUCT: dict[str, list[str]] = {
    CLOUD_DECODED_PRODUCT_ID: [
        "VP Engineering", "Head of Platform", "Director of Platform Engineering",
        "Head of Infrastructure", "VP Infrastructure", "CTO",
    ],
}
DEFAULT_TARGET_TITLES = [
    "CTO", "VP Engineering", "Head of Platform", "Director of Engineering",
    "Founder", "Co-Founder",
]

# Moved to core/linkedin_urls.py 2026-10-06 so the Approve-Drafts card uses
# the same check. Two copies would disagree the first time one learned about a
# new URL shape.


class SaveContactRequest(BaseModel):
    """What the human pastes. All three are required: a LinkedIn URL with no
    name and title would leave the lead unaddressable, and a name with no
    title would let MKT-O2 assert a role nobody verified."""

    linkedin_url: str
    name: str
    title: str

    @field_validator("linkedin_url")
    @classmethod
    def _must_be_a_profile_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not is_profile_url(v):
            # A company URL (/company/...) or a search URL is a common
            # paste mistake and would be stored as a person's profile.
            raise ValueError(PROFILE_URL_HELP)
        return v

    @field_validator("name", "title")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must not be empty")
        return v


@router.get("")
async def list_buyer_research_tasks(
    product_id: Optional[str] = None,
    limit: int = 50,
    authorization: Optional[str] = Header(default=None),
):
    """Companies that cleared Stage 1 but still need a human to name the
    buyer. Ordered by fit then intent, so the strongest company is at the
    top of the lane rather than the oldest."""
    require_marketing_api_key(authorization)
    db = get_supabase()

    query = (db.table("mse_leads")
             .select("id,product_id,company,domain,domain_source,job_posting_title,"
                     "job_posting_url,open_role_count,size_proxy_open_roles,stack_tags,"
                     "company_tags,fit_score,intent_score,score_reasons,contact_attempts,"
                     "location,status,contact_status")
             .eq("contact_status", "pending"))
    if product_id:
        query = query.eq("product_id", product_id)
    rows = (query.order("fit_score", desc=True)
            .order("intent_score", desc=True)
            .limit(limit).execute().data or [])

    tasks = []
    for r in rows:
        tasks.append({
            "lead_id": r["id"],
            "product_id": r.get("product_id"),
            "headline": f"Find the buyer at {r.get('company')}",
            "company": r.get("company"),
            "domain": r.get("domain"),
            "domain_source": r.get("domain_source"),
            # The signal that qualified them -- this is what makes the task
            # answerable without opening another tab.
            "role_signal": {
                "title": r.get("job_posting_title"),
                "posting_url": r.get("job_posting_url"),
                "matching_roles": r.get("open_role_count"),
                "open_roles_on_board": r.get("size_proxy_open_roles"),
                "stack": r.get("stack_tags") or [],
            },
            "target_titles": TARGET_TITLES_BY_PRODUCT.get(
                r.get("product_id") or "", DEFAULT_TARGET_TITLES),
            "scores": {"fit": r.get("fit_score"), "intent": r.get("intent_score")},
            "why_qualified": r.get("score_reasons") or [],
            "company_tags": r.get("company_tags") or [],
            "location": r.get("location"),
            "automated_attempts": r.get("contact_attempts") or 0,
        })
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/{lead_id}/contact")
async def save_contact(
    lead_id: str,
    body: SaveContactRequest,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
):
    """
    Save a human-found buyer, then grade their email in the background.

    The write and the grading are deliberately split: SMTP verification
    involves a real connection to a stranger's mail server and
    core/email_finder paces itself, so making the human wait on it would
    make the lane feel broken. The contact is saved synchronously (so the
    UI can confirm immediately) and grading runs after.
    """
    require_marketing_api_key(authorization)
    db = get_supabase()

    existing = (db.table("mse_leads")
                .select("id,company,domain,status,contact_status")
                .eq("id", lead_id).maybe_single().execute())
    lead = existing.data if existing is not None else None
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # linkedin_url carries a UNIQUE partial index. A duplicate paste would
    # 409 at the database with an opaque message, so it is caught here with
    # one that says which company already has that profile.
    clash = (db.table("mse_leads").select("id,company")
             .eq("linkedin_url", body.linkedin_url).execute().data or [])
    other = [c for c in clash if c["id"] != lead_id]
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"That LinkedIn profile is already on the lead for {other[0].get('company')!r}",
        )

    first_name, _, last_name = body.name.strip().partition(" ")
    update = {
        "first_name": first_name or None,
        "last_name": last_name.strip() or None,
        "title": body.title.strip(),
        "linkedin_url": body.linkedin_url,
        "contact_status": "found",
        # A named contact makes the lead draftable, so it joins MKT-O2's
        # queue. Sending still requires HITL approval and a 'valid' email
        # grade -- this only moves it into the queue.
        "status": "pending_dm",
    }
    result = db.table("mse_leads").update(update).eq("id", lead_id).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save the contact")

    domain = lead.get("domain")
    if domain and first_name and last_name.strip():
        background_tasks.add_task(_grade_email_for_lead, lead_id, first_name, last_name.strip(), domain)
        grading = "queued"
    else:
        # Honest about why nothing was queued rather than silently skipping.
        grading = ("skipped: no domain on this lead" if not domain
                   else "skipped: need both a first and last name to build an email pattern")

    return {
        "lead_id": lead_id,
        "company": lead.get("company"),
        "contact_status": "found",
        "status": "pending_dm",
        "email_grading": grading,
    }


def _grade_email_for_lead(lead_id: str, first_name: str, last_name: str, domain: str) -> None:
    """Pattern + SMTP + catch-all grading for a newly-saved contact.

    Writes whatever it establishes, including a failure: email_grade
    'unknown' with no address is a real, actionable result (it routes the
    lead to the manual track) and must not be left NULL as though grading
    never ran.
    """
    from agents.marketing.lead_qualification import grade_email, is_role_address
    from core.email_finder import find_email, verify_email

    db = get_supabase()
    try:
        result = find_email(first_name, last_name, domain, supabase_client=db, max_candidates=2)
        domain_is_catch_all = None
        if result.verification_status == "verified" and result.email:
            probe = verify_email(
                f"no-such-mailbox-{abs(hash(domain)) % 10**8}@{domain}", domain=domain)
            domain_is_catch_all = probe.status in ("verified", "catch_all")
        grade = grade_email(
            result.verification_status,
            domain_is_catch_all=domain_is_catch_all,
            role_address=is_role_address(result.email),
        )
        payload = {"email_grade": grade}
        if result.email:
            payload["email"] = result.email
            payload["email_status"] = result.verification_status
        db.table("mse_leads").update(payload).eq("id", lead_id).execute()
        log.info("[BuyerResearch] graded %s@%s for lead %s -> %s",
                 first_name, domain, lead_id, grade)
    except Exception as exc:
        log.error("[BuyerResearch] email grading failed for lead %s: %s", lead_id, exc)
        try:
            db.table("mse_leads").update({"email_grade": "unknown"}).eq("id", lead_id).execute()
        except Exception:
            log.error("[BuyerResearch] could not even record the grading failure for %s", lead_id)


@router.post("/{lead_id}/skip")
async def skip_task(
    lead_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """Mark a company as not worth researching by hand. It leaves the lane
    without being deleted -- 'none_found' is the same terminal state the
    automated retry reaches, and keeps it visible for review."""
    require_marketing_api_key(authorization)
    db = get_supabase()
    result = (db.table("mse_leads")
              .update({"contact_status": "none_found"})
              .eq("id", lead_id).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead_id": lead_id, "contact_status": "none_found"}
