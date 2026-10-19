# REFACTOR CANDIDATE -- read model + five write paths + the snapshot job.
"""
Loop Map -- the CEO Decoded "Loop Map" tab's backend.

GET  /marketing/loop-map                          the whole map, live statuses
POST /marketing/loop-map/{pid}/steps/{n}/complete  check a desk (current lap)
POST /marketing/loop-map/{pid}/steps/{n}/undo      uncheck it (current lap only)
POST /marketing/loop-map/{pid}/laps/complete       check everything, record the lap
POST /marketing/loop-map/{pid}/laps/reset          clear this lap's checks
POST /marketing/loop-map/{pid}/notes               log a hole or a note
POST /marketing/loop-map/notes/{id}/resolve        resolve / reopen a hole (never delete)
POST /marketing/loop-map/products/{pid}/playbook   give a new product its stations
POST /marketing/loop-map/snapshot                  hourly status history

Same internal-secret auth as every other /marketing route (MARKETING_API_KEY).
The CEO Decoded proxy enforces the admin role before forwarding, and passes the
signed-in admin's email as X-Loop-Actor for completed_by / created_by.

Desk color = system status (core/loop_map_rules.py, never stored except as
hourly history). Check mark = mse_loop_progress. Separate tables, so neither
can overwrite the other.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from api.middleware.auth import require_marketing_api_key
from core.loop_map_rules import SEVERITY, evaluate
from core.loop_map_sources import load_sources
from agents.marketing.partner_sourcing import PARTNERS_PRODUCT_ID
from core.outreach_counters import count_outreach
from core.supabase_client import get_supabase, get_supabase_for_job

log = logging.getLogger(__name__)

router = APIRouter(prefix="/marketing/loop-map", tags=["marketing", "loop-map"])

HISTORY_DAYS = 28
FUNNEL_WEEKS = 8
SNAPSHOT_MIN_GAP = timedelta(minutes=50)
SNAPSHOT_INTERVAL_SECONDS = 3600
AGENT_ID = "loop-map"

QUALIFIED = ("company_qualified", "pending_dm", "pending_email", "contacted", "converted")
APPROVED = ("approved_hitl", "approved_manual", "touch_1_sending", "touch_1_sent",
            "touch_2_sending", "sequence_complete", "replied")


# ------------------------------------------------------------------ reads
def _all(db, table: str, cols: str = "*", **eq) -> list[dict]:
    q = db.table(table).select(cols)
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.limit(10000).execute().data or []


def _current_lap(db, product_id: str) -> int:
    laps = _all(db, "mse_loop_laps", "lap", product_id=product_id)
    return max((r["lap"] for r in laps), default=0) + 1


def _stations(db, product_id: str) -> list[dict]:
    rows = _all(db, "mse_loop_stations", "step_order,name", product_id=product_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No loop stations for product {product_id}")
    return sorted(rows, key=lambda r: r["step_order"])


def _open_holes(db, product_id: str) -> int:
    notes = _all(db, "mse_loop_notes", "kind,resolved_at", product_id=product_id)
    return sum(1 for n in notes if n["kind"] == "hole" and not n.get("resolved_at"))


def _history_strip(rows: list[dict], today: datetime) -> list[dict]:
    """One cell per day for HISTORY_DAYS, colored by that day's worst status."""
    worst: dict[str, str] = {}
    for r in rows:
        day = str(r["captured_at"])[:10]
        if day not in worst or SEVERITY[r["status"]] > SEVERITY[worst[day]]:
            worst[day] = r["status"]
    days = [(today - timedelta(days=i)).date().isoformat() for i in range(HISTORY_DAYS - 1, -1, -1)]
    return [{"day": d, "status": worst.get(d)} for d in days]


def _status_since(rows: list[dict], status: str) -> Optional[str]:
    """Start of the unbroken run of `status` at the end of the history."""
    since = None
    for r in sorted(rows, key=lambda r: str(r["captured_at"]), reverse=True):
        if r["status"] != status:
            break
        since = str(r["captured_at"])
    return since


def _week_start(ts: str) -> str:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _funnel_trend(S: dict, product_id: str, now: datetime) -> list[dict]:
    """qualified -> approved -> sent -> replies -> calls -> won, per ISO week.
    The first three are automatic (written by the pipeline); replies, calls
    and won come from Kelvin's marks in the Conversations lane."""
    weeks = [_week_start((now - timedelta(weeks=i)).isoformat()) for i in range(FUNNEL_WEEKS - 1, -1, -1)]
    out = {w: dict.fromkeys(("qualified", "approved", "sent", "replies", "calls", "won"), 0) for w in weeks}

    def bump(ts, key):
        if ts:
            w = _week_start(str(ts))
            if w in out:
                out[w][key] += 1

    # An unreadable source is stored as {"_error": ...}; iterating that dict
    # yielded its key strings and 500'd the whole map (prod, 2026-10-19).
    # The desks already show the source as unknown; the trend just skips it.
    rows = lambda name: [r for r in (S.get(name) if isinstance(S.get(name), list) else [])  # noqa: E731
                         if r.get("product_id") == product_id]
    for l in rows("leads"):
        if l.get("status") in QUALIFIED:
            bump(l.get("created_at"), "qualified")
    for s in rows("seqs"):
        if s.get("status") in APPROVED:
            bump(s.get("created_at"), "approved")
        bump(s.get("touch_1_sent_at"), "sent")
    for c in rows("convs"):
        bump(c.get("replied_at"), "replies")
        bump(c.get("call_booked_at"), "calls")
        bump(c.get("won_at"), "won")
    return [{"week": w, **out[w]} for w in weeks]


def _chips(also: list, S: dict, product_id: str, now: datetime) -> list[dict]:
    chips = []
    for item in also or []:
        tone, text = item[0], item[1]
        if len(item) > 2:
            st = evaluate(item[2], S, product_id, now)
            chips.append({"text": text, "status": st.status, "evidence": st.evidence, "live": True})
        else:
            chips.append({"text": text, "tone": tone, "live": False})
    return chips


def build_map(db, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    S = load_sources(db, now)
    products = sorted(_all(db, "mse_loop_products"), key=lambda p: p["tab_order"])
    stations = _all(db, "mse_loop_stations")
    progress = _all(db, "mse_loop_progress")
    laps = _all(db, "mse_loop_laps")
    notes = sorted(_all(db, "mse_loop_notes"), key=lambda n: str(n["created_at"]), reverse=True)
    since = (now - timedelta(days=HISTORY_DAYS)).isoformat()
    history = (db.table("mse_loop_status_history").select("product_id,step_order,status,captured_at")
               .gte("captured_at", since).limit(50000).execute().data or [])
    hist = defaultdict(list)
    for h in history:
        hist[(h["product_id"], h["step_order"])].append(h)

    out = []
    for p in products:
        pid = p["product_id"]
        p_laps = sorted((l for l in laps if l["product_id"] == pid), key=lambda l: l["lap"])
        lap = (p_laps[-1]["lap"] if p_laps else 0) + 1
        done = {r["step_order"]: r for r in progress if r["product_id"] == pid and r["lap"] == lap}
        desks = []
        for s in sorted((s for s in stations if s["product_id"] == pid), key=lambda s: s["step_order"]):
            st = evaluate(s["status_rule_key"], S, pid, now)
            h = hist[(pid, s["step_order"])]
            desks.append({
                "step_order": s["step_order"], "name": s["name"], "owner": s["owner"],
                "description": s["description"], "next_action": s["next_action"],
                "lane_link": s["lane_link"], "target_hint": s["target_hint"],
                "rule_key": s["status_rule_key"], **st.as_dict(),
                "status_since": _status_since(h, st.status),
                "history": _history_strip(h, now),
                "done": s["step_order"] in done,
                "done_at": (done.get(s["step_order"]) or {}).get("completed_at"),
            })
        out.append({
            "product_id": pid, "slug": p["slug"], "name": p["display_name"], "playbook": p["playbook"],
            "playbook_line": p["playbook_line"],
            "verdict": {"tone": p["verdict_tone"], "title": p["verdict_title"], "body": p["verdict_body"]},
            "math": p["math"], "chips": _chips(p["also"], S, pid, now),
            "lap": lap, "laps_finished": len(p_laps),
            "last_lap_at": p_laps[-1]["completed_at"] if p_laps else None,
            "stations": desks,
            "notes": [n for n in notes if n["product_id"] == pid],
            "funnel": _funnel_trend(S, pid, now) if p["playbook"] in ("signal_outbound", "partner") else None,
        })

    counters = count_outreach(S.get("leads") if isinstance(S.get("leads"), list) else [],
                              S.get("seqs") if isinstance(S.get("seqs"), list) else [],
                              S.get("convs") if isinstance(S.get("convs"), list) else [], now=now,
                              # Same prospect-only scope as the digest header.
                              exclude_product_ids=(PARTNERS_PRODUCT_ID,))
    return {"generated_at": now.isoformat(), "products": out,
            "counters": {k: counters[k] for k in ("awaiting_buyer", "awaiting_approval", "ready_to_paste")}}


@router.get("")
async def get_loop_map(authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    # Reconnecting client: a source read that hits a connection the server
    # already dropped is replayed instead of rendering that source unknown.
    return await asyncio.to_thread(build_map, get_supabase_for_job())


# ----------------------------------------------------------------- writes
def _actor(x_loop_actor: Optional[str]) -> Optional[str]:
    return (x_loop_actor or "").strip()[:200] or None


def _finish_lap(db, product_id: str, lap: int) -> dict:
    holes = _open_holes(db, product_id)
    db.table("mse_loop_laps").insert({"product_id": product_id, "lap": lap,
                                      "open_holes_at_completion": holes}).execute()
    return {"lap_completed": lap, "open_holes_at_completion": holes}


@router.post("/{product_id}/steps/{step_order}/complete")
async def complete_step(product_id: str, step_order: int,
                        authorization: Optional[str] = Header(default=None),
                        x_loop_actor: Optional[str] = Header(default=None)):
    """Check one desk. When it is the last unchecked desk the lap is recorded
    and the next lap starts with no checks -- server-side, so a lap can never
    be half-recorded by a closed browser tab."""
    require_marketing_api_key(authorization)
    db = get_supabase()
    stations = _stations(db, product_id)
    if step_order not in {s["step_order"] for s in stations}:
        raise HTTPException(status_code=404, detail=f"Product has no step {step_order}")
    lap = _current_lap(db, product_id)
    db.table("mse_loop_progress").upsert({"product_id": product_id, "lap": lap, "step_order": step_order,
                                          "completed_by": _actor(x_loop_actor)},
                                         on_conflict="product_id,lap,step_order").execute()
    done = {r["step_order"] for r in _all(db, "mse_loop_progress", "step_order", product_id=product_id, lap=lap)}
    result = {"product_id": product_id, "lap": lap, "step_order": step_order, "done": True}
    if done >= {s["step_order"] for s in stations}:
        result.update(_finish_lap(db, product_id, lap))
    return result


@router.post("/{product_id}/steps/{step_order}/undo")
async def undo_step(product_id: str, step_order: int, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()
    lap = _current_lap(db, product_id)
    db.table("mse_loop_progress").delete().eq("product_id", product_id).eq("lap", lap) \
        .eq("step_order", step_order).execute()
    return {"product_id": product_id, "lap": lap, "step_order": step_order, "done": False}


@router.post("/{product_id}/laps/complete")
async def complete_lap(product_id: str, authorization: Optional[str] = Header(default=None),
                       x_loop_actor: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()
    stations = _stations(db, product_id)
    lap = _current_lap(db, product_id)
    db.table("mse_loop_progress").upsert(
        [{"product_id": product_id, "lap": lap, "step_order": s["step_order"], "completed_by": _actor(x_loop_actor)}
         for s in stations], on_conflict="product_id,lap,step_order", ignore_duplicates=True).execute()
    return {"product_id": product_id, "lap": lap, **_finish_lap(db, product_id, lap)}


@router.post("/{product_id}/laps/reset")
async def reset_lap(product_id: str, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    db = get_supabase()
    lap = _current_lap(db, product_id)
    db.table("mse_loop_progress").delete().eq("product_id", product_id).eq("lap", lap).execute()
    return {"product_id": product_id, "lap": lap, "cleared": True}


class NoteIn(BaseModel):
    step_order: int = Field(ge=1)
    kind: str = Field(pattern="^(hole|note)$")
    text: str = Field(min_length=1, max_length=2000)


@router.post("/{product_id}/notes")
async def add_note(product_id: str, body: NoteIn, authorization: Optional[str] = Header(default=None),
                   x_loop_actor: Optional[str] = Header(default=None)):
    """Logging a note never blocks or changes completion."""
    require_marketing_api_key(authorization)
    db = get_supabase()
    if body.step_order not in {s["step_order"] for s in _stations(db, product_id)}:
        raise HTTPException(status_code=404, detail=f"Product has no step {body.step_order}")
    row = db.table("mse_loop_notes").insert({
        "product_id": product_id, "step_order": body.step_order, "lap": _current_lap(db, product_id),
        "kind": body.kind, "text": body.text.strip(), "created_by": _actor(x_loop_actor),
    }).execute().data
    return {"note": (row or [None])[0]}


class ResolveIn(BaseModel):
    resolved: bool


@router.post("/notes/{note_id}/resolve")
async def resolve_note(note_id: str, body: ResolveIn, authorization: Optional[str] = Header(default=None)):
    """Resolve or reopen. There is deliberately no delete route."""
    require_marketing_api_key(authorization)
    db = get_supabase()
    resolved_at = datetime.now(timezone.utc).isoformat() if body.resolved else None
    row = db.table("mse_loop_notes").update({"resolved_at": resolved_at}).eq("id", note_id).execute().data
    if not row:
        raise HTTPException(status_code=404, detail=f"No note {note_id}")
    return {"note": row[0]}


class PlaybookIn(BaseModel):
    playbook: str = Field(pattern="^(signal_outbound|community_search|content|distributor|partner)$")


@router.post("/products/{product_id}/playbook")
async def apply_playbook(product_id: str, body: PlaybookIn, authorization: Optional[str] = Header(default=None)):
    """A new product gets its desks by picking a playbook -- no code change.
    It also needs an mse_loop_products row to appear as a tab."""
    require_marketing_api_key(authorization)
    db = get_supabase()
    try:
        n = db.rpc("mse_loop_apply_playbook", {"p_product_id": product_id, "p_playbook": body.playbook}).execute().data
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"product_id": product_id, "playbook": body.playbook, "stations_created": n}


# --------------------------------------------------------------- history
def snapshot(db, *, force: bool = False, now: Optional[datetime] = None) -> dict:
    """Write every station's current status to mse_loop_status_history.
    Skips (and says so) if a snapshot landed in the last SNAPSHOT_MIN_GAP, so
    a restart, a second replica or a manual call cannot double-count an hour."""
    now = now or datetime.now(timezone.utc)
    last = (db.table("mse_loop_status_history").select("captured_at").order("captured_at", desc=True)
            .limit(1).execute().data or [])
    if last and not force and str(last[0]["captured_at"]) > (now - SNAPSHOT_MIN_GAP).isoformat():
        return {"written": 0, "skipped": True, "last_captured_at": last[0]["captured_at"]}
    data = build_map(db, now)
    rows = [{"product_id": p["product_id"], "step_order": s["step_order"], "status": s["status"],
             "count": s["count"], "evidence": (s["evidence"] or "")[:1000], "captured_at": now.isoformat()}
            for p in data["products"] for s in p["stations"]]
    if rows:
        db.table("mse_loop_status_history").insert(rows).execute()
    by_status: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r["status"]] += 1
    return {"written": len(rows), "skipped": False, "captured_at": now.isoformat(), "by_status": dict(by_status)}


def _audit(db, outcome: str, metadata: dict) -> None:
    try:
        db.table("audit_log").insert({"agent_id": AGENT_ID, "action": "loop_map_snapshot",
                                      "outcome": outcome, "metadata": metadata}).execute()
    except Exception as exc:
        log.error("[LoopMap] audit_log write failed: %s", exc)


def run_snapshot(force: bool = False) -> dict:
    db = get_supabase_for_job()
    try:
        result = snapshot(db, force=force)
    except Exception as exc:
        log.error("[LoopMap] snapshot failed: %s", exc)
        _audit(db, "lose", {"error": f"{type(exc).__name__}: {exc}"[:500]})
        raise
    if not result.get("skipped"):
        _audit(db, "win", result)
    return result


@router.post("/snapshot")
async def post_snapshot(force: bool = False, authorization: Optional[str] = Header(default=None)):
    require_marketing_api_key(authorization)
    try:
        return await asyncio.to_thread(run_snapshot, force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Snapshot failed: {exc}") from exc


async def snapshot_loop() -> None:
    """Hourly history, run by mse-api itself (started in api/main.py's
    lifespan). n8n would be the usual home for a cron, but this service cannot
    read or verify n8n executions (no N8N_API_KEY), and a job nobody can prove
    ran is not a job. The DB-side gap check makes restarts and replicas safe."""
    await asyncio.sleep(60)
    while True:
        try:
            result = await asyncio.to_thread(run_snapshot)
            # WARNING, not INFO: production ships WARNING+ only, and a job whose
            # runs never appear in the logs is the gap the 8am drafter has.
            log.warning("[LoopMap] hourly snapshot: %s", {k: result.get(k) for k in ("written", "skipped")})
        except Exception as exc:  # logged + audited in run_snapshot; keep the loop alive
            log.error("[LoopMap] hourly snapshot raised: %s", exc)
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
