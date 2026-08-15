# Brevo Setup — MKT-O3 Trial Nurture Sequences

Brevo (formerly Sendinblue) replaced systeme.io as MKT-O3's email sequence
provider on 2026-08-14. systeme.io's public API has no campaigns/sequences
endpoint at all — confirmed 404 on every plausible path (`/campaigns`,
`/email_campaigns`, `/sequences`, `/automations`, `/newsletters`, ...) against
a real, verified account key. Brevo is free (300 emails/day, 100K contacts)
and has a real contacts + automation API.

**All the code for this is done.** What's left is manual, one-time setup in
the Brevo UI — this doc is that checklist. Nothing here can be done by
Claude Code: it needs a real Brevo account, a real API key, and clicking
through Brevo's own automation builder.

## How this actually works

This repo never sends the nurture emails itself, and never creates a Brevo
automation workflow via API. Each MSE product gets:

1. Its own Brevo **contact list** (e.g. "Showing Signal Trial Nurture")
2. Its own Brevo **automation workflow**, built once by hand in the Brevo
   UI, triggered by "contact added to list [that product's list]"

When a real trial signup happens, this repo (`POST /marketing/brevo/enroll`
→ `agents/marketing/mkt_o3_email_sequence_loader.py`'s
`enroll_trial_in_sequence`) does exactly two things: creates/updates the
contact in Brevo with their trial metadata, and adds them to the right
list. That addition is what fires the automation — Brevo takes it from
there on its own schedule. MKT-O3 still drafts the actual 5-email copy with
Claude, unchanged from before; you copy that drafted copy into the
automation's email steps by hand (step 5 below) — there's no API for
Brevo to pull it in automatically.

## One-time setup

### 1. Create a Brevo account

Go to [brevo.com](https://www.brevo.com) and sign up for the free plan
(300 emails/day, 100K contacts — plenty for MSE's current scale).

### 2. Generate an API key

Settings → SMTP & API → API Keys → **Generate a new API key**. Copy it —
Brevo only shows it once.

### 3. Add `BREVO_API_KEY` to Railway

In the `kdavis-microsaas-engine` Railway project's environment variables,
add:

```
BREVO_API_KEY=<the key from step 2>
```

Until this is set, every Brevo call fails gracefully with a clear
`ConfigurationError` (logged, not a crash) — the rest of the API keeps
running normally.

### 4. For each MSE product: create a contact list

Contacts → Lists → **Create a new list**. Name it `[Product Name] Trial
Nurture` (e.g. "Showing Signal Trial Nurture"). Note the list ID — it's
visible in the URL when you open the list (`.../lists/<id>`) or in the
list's own settings panel.

### 5. Register that list ID in this repo

```bash
curl -X POST https://<your-railway-api-url>/marketing/brevo/lists \
  -H "Authorization: Bearer $MARKETING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"product_id": "<the product's UUID>", "brevo_list_id": <the list ID from step 4>, "list_name": "Showing Signal Trial Nurture"}'
```

Verify it saved:

```bash
curl https://<your-railway-api-url>/marketing/brevo/lists/<product_id> \
  -H "Authorization: Bearer $MARKETING_API_KEY"
```

### 6. Get the drafted sequence copy

MKT-O3 already drafted the 5-email sequence during that product's campaign
build (`mse_email_sequences` table, most recent row for the product). Pull
it via Supabase directly, or ask Claude Code to print it — you'll need the
`subject`/`body`/`day` for each of the 5 emails in the next step.

### 7. Build the automation workflow in Brevo

Automation → **Create a workflow** → start from a blank workflow (or the
"Send a series of emails" template, then adjust):

1. **Trigger**: "A contact is added to a list" → select the product's list
   from step 4
2. Add 5 **Send an email** steps, one per drafted email. For each step:
   - Use the `subject` and `body` from the corresponding drafted email
     (step 6) — copy them in as-is, this repo doesn't invent content
     beyond what MKT-O3 already drafted from real research
   - Set the delay before that step fires to match the email's `day`
     value: Day 0 (immediately), Day 2, Day 4, Day 7, Day 14
3. **Activate** the workflow (top-right toggle) — nothing sends until you
   do this

### 8. Confirm custom attributes exist (only if MKT-O3 ever adds new ones)

`enroll_trial_in_sequence` sends `FIRSTNAME`/`LASTNAME` (Brevo's built-in
reserved attributes — always work) plus `PRODUCT_ID`/`PLAN_TIER`/
`TRIAL_START` as custom attributes. Brevo requires custom attributes to
already exist in your account before a contact can be created with them,
or the API call fails with a real, surfaced "Attribute not found" error
(never silently dropped — you'll see it in `audit_log`/the enroll
response). Create them once: Contacts → Settings → Contact attributes →
add `PRODUCT_ID`, `PLAN_TIER`, `TRIAL_START` as Text attributes.

### 9. Wire the real trigger (Stripe webhook or Supabase function)

`n8n/trial_enrollment_workflow.json` is a webhook-triggered n8n workflow —
import it, then point your actual trial-signup source (a Stripe webhook on
`customer.subscription.created` with `status=trialing`, or a Supabase
database function/trigger) at that n8n webhook's URL with a body of
`{product_id, email, first_name, last_name, plan_tier, trial_start}`.
Exported with `"active": false` — activate it in n8n once you've verified
one real enrollment end-to-end (step 10).

### 10. Verify end-to-end

Trigger one real enrollment (either via the n8n webhook, or directly):

```bash
curl -X POST https://<your-railway-api-url>/marketing/brevo/enroll \
  -H "Authorization: Bearer $MARKETING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"product_id": "<product_id>", "email": "your-own-test-email@example.com", "first_name": "Test", "last_name": "Signup", "plan_tier": "solo", "trial_start": "2026-08-14"}'
```

Check: the contact appears in the product's Brevo list, the automation
shows as triggered (Automation → workflow → Statistics), and the first
email (Day 0) arrives shortly after. From that point forward, every real
trial signup is fully automatic — no manual action needed per contact.

## What's already done (code side, no action needed)

- `core/brevo_client.py` — wrapper around the real `brevo-python` SDK
  (v5.0.2, verified against the actual installed package, not assumed
  from docs), added to `requirements.txt`
- `supabase/migrations/20260814000025_brevo_lists.sql` — `mse_brevo_lists`
  table (not yet applied to production as of this writing — ask Claude
  Code to apply it, same as every other migration this repo ships)
- `POST /marketing/brevo/lists`, `GET /marketing/brevo/lists/{product_id}`,
  `POST /marketing/brevo/enroll` — `api/routers/brevo.py`
- `agents/marketing/mkt_o3_email_sequence_loader.py` — drafting logic
  unchanged; `enroll_trial_in_sequence()` is the new per-signup entry
  point; the old systeme.io push is removed from the draft-time flow
  entirely (Brevo needs no API call at draft time — see the module
  docstring's PROVIDER SWAP note) but `_SystemeIOClient` code itself is
  kept, flagged `DEPRECATED = True`, for reference
- `n8n/trial_enrollment_workflow.json` — webhook → enroll → audit log,
  imported but inactive until step 9 above
