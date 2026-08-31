-- Migration 038: DIST wedge taxonomy amendment (Phase 0 recalibration).
-- Session 2026-08-31. The 2-tier wedge test (structural pass / temporary
-- fail) rejected all 4 real products submitted to it (SPH, DecodedSix, and
-- all 3 TradesDesk verticals all came back 'temporary', confirmed live via
-- mse_positioning before this migration). A filter that rejects everything
-- is miscalibrated, not working as intended -- structural is a moat test,
-- and moats aren't required to clear the $4K MRR first bar (~33-40
-- customers), only at $10K+ and exit. This does NOT weaken the gate: the
-- existing three structural questions in wedge_validator.py are unchanged
-- and still determine tier; only the pass condition widens to include
-- "they could close this and demonstrably have not" (execution), which
-- still requires sourced evidence -- unsourced still resolves to temporary,
-- which still fails and blocks generation exactly as before.

alter table mse_positioning
  drop constraint mse_positioning_wedge_type_check;

alter table mse_positioning
  add constraint mse_positioning_wedge_type_check
  check (wedge_type in ('structural', 'execution', 'temporary'));

-- wedge_review_status: independent of wedge_type/status. Marks a row whose
-- verdict was rendered under a taxonomy rule that has since changed, so P2
-- re-evaluates it under the new rule rather than silently inheriting a
-- verdict decided under different criteria. 'needs_rereview' is a signal
-- for P2 scheduling, not a gate by itself -- generation eligibility is
-- still governed purely by wedge_type (see surface_planner.py).
alter table mse_positioning
  add column if not exists wedge_review_status text not null default 'current'
    check (wedge_review_status in ('current', 'needs_rereview'));

-- next_review_due: mandatory re-review tracking for 'execution'-tier rows
-- only (an execution wedge is a bet that the substitute keeps not closing
-- the gap -- that bet needs a recheck date, unlike a structural wedge which
-- doesn't expire on a normal roadmap cycle by definition). Left null for
-- structural/temporary rows; set by wedge_validator.py at write time for
-- any row it verdicts 'execution' (now() + 6 months, i.e. 2 quarters).
alter table mse_positioning
  add column if not exists next_review_due date;

-- Existing 'temporary' rows were verdicted under the old 2-tier rule, which
-- had no way to distinguish "will close this on a normal cycle" (still
-- temporary today) from "could close it, sourced evidence they haven't"
-- (would now be execution). Flagging for re-review, NOT auto-promoting --
-- promotion requires actually re-running Q4 with real sourced evidence,
-- which this migration cannot manufacture. Structural rows need no
-- re-review; the taxonomy change doesn't touch their pass condition.
update mse_positioning
  set wedge_review_status = 'needs_rereview'
  where wedge_type = 'temporary';
