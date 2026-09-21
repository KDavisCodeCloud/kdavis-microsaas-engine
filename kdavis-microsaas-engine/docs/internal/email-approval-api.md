# MSE email approval API (for the CEO Decoded Email Campaign HITL Queue)

Base path: `/marketing/internal/*` on the `mse-api` Railway service.
Auth: Supabase session JWT, `app_metadata.role == "admin"` (same
`tenant_context_middleware` + per-route role check pattern as
`api/routers/product_marketing.py` — **not** the `MARKETING_API_KEY`
shared-secret pattern `api/routers/marketing.py`'s automation routes use).
Router source: `api/routers/marketing_internal.py`.

## Why this contract looks the way it does

The 2026-09-21 build session this was written for assumed a clean,
product-registry-scoped `mse_email_templates` schema (per-step rows,
subscriber/enrollment tracking, click tracking). That schema **does not
exist** and was **not built** this session. Real recon found:

- The live lifecycle-email table is `mse_email_sequences` (migration
  `20260713000007`) — one row per whole sequence, `emails` is a JSONB
  array of steps, not per-step rows.
- `mse_email_sequences.product_id` predates `mse_products` (the DIST
  positioning-gate product registry, migration `20260830000029`) and does
  **not** resolve to it — 0 of the 2 existing rows match any real
  `mse_products.id`.
- The live campaign trigger
  (`agents/marketing/mkt_orch_campaign_orchestrator.py`, fired from
  `POST /products/{id}/run-campaign`) keys `product_id` off
  `opportunity_pipeline.id` — a **third**, unrelated ID space.
- No send/click/subscriber/enrollment tracking exists for lifecycle email
  at all. The only channel that has ever sent real email is MKT-O5 (cold
  outreach, `mse_dm_sequences`), not MKT-O3 (this table).
- MKT-O3 sequences load into Brevo **unactivated**, pending HITL —
  `BREVO_API_KEY` has never been set, so nothing here has ever actually
  sent.

Rather than build a second schema on top of that mismatch, this API
wraps `mse_email_sequences` as it actually exists. It gives the CEO
dashboard something real and stable to consume today; the deeper
product-identity reconciliation (see GAPS entry, session 2026-09-21) is
a separate, larger piece of work.

## Endpoints

### `GET /marketing/internal/email-templates`

Query params: `status` (one of `pending_hitl | loaded_unactivated |
activated | failed | retired`), `product_id`.

```json
{
  "templates": [
    {
      "template_key": "<mse_email_sequences.id>",
      "sequence_key": "<same value — one row is one whole sequence>",
      "step_count": 5,
      "subject": "<first step's subject, or null>",
      "preheader": "<first step's preheader, or null>",
      "status": "pending_hitl",
      "origin": "generated",
      "source_script": null,
      "grounding_sources": [],
      "product": {
        "id": "<mse_email_sequences.product_id, verbatim>",
        "slug": "tradesdesk",
        "name": "TradesDesk",
        "resolved": true
      },
      "campaign_build_id": "<campaign_builds.id>",
      "hitl_approved_by": null,
      "hitl_approved_at": null,
      "created_at": "2026-09-21T00:00:00Z"
    }
  ]
}
```

`product.resolved: false` means `product_id` did not match any
`mse_products` row — the dashboard should render this clearly (e.g. a
warning badge) rather than silently show a blank product name, since it
signals the ID-space mismatch above.

### `GET /marketing/internal/email-templates/{template_key}`

Same shape as one list-row, plus `"steps": [...]` — the full `emails`
array as stored (each step's own shape is whatever MKT-O3 wrote; not
normalized here).

### `POST /marketing/internal/email-templates/{template_key}/approve`

Moves `pending_hitl` or `loaded_unactivated` → `activated`, stamps
`hitl_approved_by`/`hitl_approved_at`. `409` if the row is already
`activated`/`failed`/`retired`. **Approving here does not cause a send**
— there is no scheduler reading `activated` rows into Brevo/Resend yet
(see gap above). This only advances the approval record.

### `POST /marketing/internal/email-templates/{template_key}/retire`

Sets `status = "retired"` (new terminal status, migration
`20260921000051`). `404` if not found.

### `GET /marketing/internal/campaign-status`

One row per real `mse_products` entry with its current positioning
state, plus honestly-labeled totals for email sequences:

```json
{
  "products": [
    {
      "product_id": "...", "slug": "tradesdesk", "name": "TradesDesk",
      "product_status": "active",
      "positioning_status": "approved",
      "positioning_version": 4
    }
  ],
  "email_sequences_total": 2,
  "email_sequences_unattributable": 2,
  "note": "email_sequences_unattributable counts sequences whose product_id does not match any mse_products row..."
}
```

As of 2026-09-21, exactly **1 product** (`small-portfolio-hub`) has an
approved positioning brief; the other 9 registered products are
`pending_review` or have no positioning row at all. Both existing
`mse_email_sequences` rows are unattributable (pre-date the registry).

### `GET /marketing/internal/email-metrics`

Query param: `product_id` (optional).

```json
{
  "sequences_by_status": {"pending_hitl": 2},
  "sends": null,
  "clicks": null,
  "unsubscribes": null,
  "conversions": null,
  "revenue": null,
  "note": "sends/clicks/... are null, not zero -- no tracking table exists yet."
}
```

`sends`/`clicks`/`unsubscribes`/`conversions`/`revenue` are always
`null` today, deliberately distinct from `0` — there is no
`mse_email_sends`/`mse_email_clicks` table. Do not render these as "0"
in the dashboard; render "not tracked yet."

## Update, 2026-09-21 (same day, W2.5 follow-up) — product-ID reconciliation shipped

The three-way ID mismatch above is now resolved. **Locked decision:
`mse_products.id` is canonical product identity for all campaign/email
tables and the positioning gate; `opportunity_pipeline` rows map TO
products, they never own campaign identity.**

- `mse_products` gained a nullable `opportunity_id` column (migration
  `20260921000052`), backfilled for the 2 confirmed matches
  (`tradesdesk` → `b1ebd730...`, `small-portfolio-hub` → `9d26abed...`).
  All other products stay `NULL` — confirmed no_match (hand-conceived
  Decoded Empire / internal / consumer products, or TradesDesk
  sub-verticals with no opportunity of their own).
- A real launched product, **Showing Signal**, was found with no
  `mse_products` row at all (it predates the registry) and has been
  registered (`slug: showing-signal`, `opportunity_id: 797adb70...`).
- Both orphan `mse_email_sequences` rows from the section above are now
  repointed to real `mse_products.id` values, and `product_id` now
  carries a **validated foreign key** to `mse_products(id)`. `product`
  in the API response above will now resolve for every row, going
  forward — `resolved: false` should not occur again barring new bugs
  upstream.
- **The positioning gate is now wired** (migration `20260921000053`): a
  real Postgres trigger on `mse_email_sequences` INSERT rejects any row
  whose product has no `mse_positioning` row with `status='approved'`.
  This is DB-level enforcement (fires even for the backend's own
  service_role connection — triggers are not bypassed by RLS bypass).
  As of today, this means MKT-O3 email-sequence generation will fail for
  every product except `small-portfolio-hub` until more positioning
  briefs are approved — **working as intended**, not a bug.
  `agents/marketing/mkt_orch_campaign_orchestrator.py`'s
  `_DOWNSTREAM_AGENTS` was reordered so mkt-o3 (email) fires **last**,
  specifically so this new, now-common rejection can never collaterally
  prevent lead-finding/DM/SEO/social from running for the same campaign
  build. See `tests/test_mkt_orch_campaign_orchestrator.py` and
  `tests/sql/test_positioning_gate_live.sql`.

## What this API still does NOT cover
- No click tracking, no send tracking, no subscriber/enrollment engine,
  no 15-minute scheduler loop. Nothing currently reads `activated` rows
  and turns them into real Brevo/Resend sends.
- No RFC 8058 headers or CAN-SPAM footer are applied to MKT-O3 content
  specifically (they don't need to be yet — nothing sends). MKT-O5 (cold
  outreach, the one live sender) got RFC 8058 `List-Unsubscribe` /
  `List-Unsubscribe-Post` headers added this session
  (`core/email_compliance.py`'s `build_list_unsubscribe_headers`) plus a
  matching `POST /marketing/unsubscribe` one-click target.
