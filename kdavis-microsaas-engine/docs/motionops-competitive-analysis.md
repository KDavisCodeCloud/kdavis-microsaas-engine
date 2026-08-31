# MotionOps Competitive Analysis — TradesDesk

Standalone deliverable, outstanding ahead of the September TradesDesk build brief. Run alongside the SPH gate-hardening pass for efficiency; not part of that work.

Date: 2026-08-31

## What MotionOps is

Field service management software for home-service and contracting businesses (electrical, plumbing, HVAC, pool, pest control, remodeling, cleaning, landscaping) — CRM, proposals, scheduling, invoicing, HR, payroll, and payment processing in one product.
Source: https://www.getapp.com/operations-management-software/a/motionops/ (verified 2026-08-31)

## Pricing (primary source, motionops.com/pricing, verified 2026-08-31)

| Tier | Users included | Monthly price | Additional user |
|---|---|---|---|
| Solo | 1 | $39/mo | — |
| Go | 1–5 | $119/mo | +$29/user |
| Scale | 5 | $199/mo | +$49/user |

- 14-day free trial, card required at signup, cancellable within the trial.
- Annual plans: 33% savings, full amount charged upfront, non-refundable if cancelled early.

**Real discrepancy found**: MotionOps' own marketing page (motionops.com/service-fusion-alternative) claims "without per-user pricing, add-ons, or limits as you grow." Its own pricing page contradicts this directly — Solo/Go/Scale is a per-user tier structure with explicit per-additional-user add-on fees. Treated the pricing page as authoritative for this analysis, not the marketing claim, per the same standard applied to competitor claims elsewhere in this repo (verify against the primary pricing/settings source, not secondhand copy).

## Payment processing / configurable cost-shift (the new B.1 research standard)

- Card processing: 2.9% + $0.30 (3.2% + $0.30 for Amex).
- ACH: 1% under $20,000, 0.5% over $20,000.
- **Configurable option confirmed**: contractors can pass the card-processing service fee to the customer. This is a real cost-shift setting, same category as the Innago/TurboTenant fee-absorption toggles found during the SPH audit.
- No evidence found of an equivalent ACH fee pass-through/absorption toggle — the configurable option is specific to card payments.

## Does MotionOps invalidate any current TradesDesk wedge?

Checked against all four live `mse_positioning` v2 briefs (`tradesdesk`, `tradesdesk-hvac`, `tradesdesk-plumbing`, `tradesdesk-electrical` — all `pending_review`, `wedge_type: temporary`, none approved). **No wedge is invalidated.**

- **Base `tradesdesk` wedge** ("every paid substitute scales price with headcount — one added helper jumps the bill 50–200%"): MotionOps is a textbook example of exactly this pattern (Go → Scale, +$29–49/user), not a counterexample. It **reinforces** the wedge rather than undermining it — it was a real gap that this specific competitor was missing from the substitute_set, now closed.
- **`tradesdesk-hvac`** (EPA 608 / AIM Act refrigerant leak-rate tracking tied to a persistent appliance record): no evidence MotionOps has any refrigerant-specific compliance feature. Not a substitute for this wedge.
- **`tradesdesk-electrical`** (permit/AHJ data model, multi-jurisdiction, re-inspection scheduling): no evidence found. Not a substitute for this wedge.
- **`tradesdesk-plumbing`** (jurisdiction-specific backflow report generation + direct municipal submission): no evidence found. Not a substitute for this wedge.

This is the same class of check Q0 is meant to formalize (does a substitute already offer the thing the wedge claims is missing) — the answer here is no for all four products, unlike the SPH case where it was yes.

## Recorded

- `docs/motionops-competitive-analysis.md` (this file).
- `mse_competitors` row inserted against `tradesdesk` (base product, id `ccc66fa2-ce86-4eb7-9b7c-6cbae9404c2e`) — name `MotionOps`, kind `paid_tool`, full pricing snapshot, positioning note on the marketing/pricing-page discrepancy, and the three findings above as `known_weaknesses`. Not inserted against the three vertical products — no vertical-specific feature found that would make it a relevant citation there.
- **No `mse_positioning` row modified. No approval performed. No TradesDesk candidate selected.** This is diagnostic input for a later owner decision on the four pending briefs, consistent with the standing rule that only the owner selects/approves.

## Recommendation for whoever runs the September brief

Add MotionOps as a cited substitute in the base `tradesdesk` `substitute_set` alongside Jobber and Housecall Pro when v3 of that brief is written — it strengthens the existing per-headcount-scaling argument with a third real example rather than requiring any wedge rewrite.
