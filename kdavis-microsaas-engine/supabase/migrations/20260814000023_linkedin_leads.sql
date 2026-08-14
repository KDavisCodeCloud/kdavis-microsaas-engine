-- Migration 023: LinkedIn manual-outreach lead intake.
--
-- Apollo.io is suspended (Free plan 403s on every call, upgrade deferred
-- indefinitely) -- MKT-O1 is effectively dead for now, not removed, just
-- routed around. The active first-customer strategy is Kelvin's own daily
-- LinkedIn session: manually collected leads (CSV export from a LinkedIn
-- search) or engager leads (people who liked/commented on one of his
-- posts), generate DM copy for, route through HITL, Kelvin pastes and
-- sends himself. Nothing here ever sends to LinkedIn automatically.
--
-- Why a new table instead of reusing mse_apollo_leads (20260709000006):
-- that table's campaign_build_id is NOT NULL + FK'd to campaign_builds --
-- every row there is scoped to one product's automated MKT-ORCH campaign
-- run. LinkedIn leads Kelvin collects by hand aren't tied to a campaign
-- build at all (no MKT-O1 run produced them), so they can't satisfy that
-- FK. A new table is the cleaner fit, same "tenant_id nullable, admin-only
-- RLS" precedent as mse_apollo_leads/mse_dm_sequences.

CREATE TABLE IF NOT EXISTS mse_linkedin_leads (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id         UUID,  -- which product's pitch/research this batch is for (MKT-O2 pulls MKT-R1 research by product_id); nullable since not every intake batch is tied to one
  first_name         TEXT,
  last_name          TEXT,
  title              TEXT,
  company            TEXT,
  linkedin_url       TEXT NOT NULL,
  location           TEXT,
  source             TEXT NOT NULL CHECK (source IN ('linkedin_manual', 'linkedin_engager')),
  source_post_url    TEXT,  -- which post they engaged with (linkedin_engager only)
  interaction_type   TEXT CHECK (interaction_type IN ('like', 'comment') OR interaction_type IS NULL),
  interaction_note   TEXT,  -- what they commented, or the post's topic -- feeds MKT-O2's "Saw your [like/comment] on my post about [topic]" opener
  status             TEXT NOT NULL DEFAULT 'pending_dm' CHECK (status IN ('pending_dm', 'contacted')),
  contacted_at       TIMESTAMPTZ,
  tenant_id          UUID,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);

-- App-level dedup (agents/marketing/mkt_li_intake.py checks before insert)
-- is backed by a real constraint too, same belt-and-suspenders pattern as
-- tenants.referral_code / integration_api_keys.key_hash elsewhere in this
-- ecosystem (see showing-signal/supabase/migrations for the same idea).
CREATE UNIQUE INDEX IF NOT EXISTS idx_mse_linkedin_leads_linkedin_url ON mse_linkedin_leads(linkedin_url);
CREATE INDEX IF NOT EXISTS idx_mse_linkedin_leads_status_source ON mse_linkedin_leads(status, source, created_at);
CREATE INDEX IF NOT EXISTS idx_mse_linkedin_leads_product ON mse_linkedin_leads(product_id);

ALTER TABLE mse_linkedin_leads ENABLE ROW LEVEL SECURITY;

CREATE POLICY mse_linkedin_leads_admin_access ON mse_linkedin_leads
  USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');

-- mse_dm_sequences (20260709000006) was built apollo-only: lead_id NOT
-- NULL FK'd to mse_apollo_leads, campaign_build_id NOT NULL FK'd to
-- campaign_builds. LinkedIn-sourced sequences have neither -- generalize
-- the table to carry either lead type rather than duplicating the whole
-- sequences table per source.
ALTER TABLE mse_dm_sequences
  ALTER COLUMN lead_id DROP NOT NULL,
  ALTER COLUMN campaign_build_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS lead_source TEXT NOT NULL DEFAULT 'apollo'
    CHECK (lead_source IN ('apollo', 'linkedin_manual', 'linkedin_engager')),
  ADD COLUMN IF NOT EXISTS linkedin_lead_id UUID REFERENCES mse_linkedin_leads(id);

-- Exactly one of the two lead references must be set -- never both, never
-- neither. Existing apollo rows (lead_id set, linkedin_lead_id null)
-- satisfy this without a backfill.
ALTER TABLE mse_dm_sequences
  ADD CONSTRAINT mse_dm_sequences_one_lead_ref CHECK (
    (lead_id IS NOT NULL)::int + (linkedin_lead_id IS NOT NULL)::int = 1
  );

-- mse_dm_sequences.status had no CHECK constraint at all in the original
-- migration (006) -- adding one now, not modifying an existing one.
-- 'approved_manual' is a new terminal HITL-approval state distinct from
-- 'approved_hitl'. This distinction is load-bearing, not cosmetic:
-- mkt_o5_sequence_sender.py (untouched by this migration, per this
-- session's explicit constraint not to change MKT-O5 or the email send
-- path) polls mse_dm_sequences for status='approved_hitl' and emails
-- whatever it finds via Resend using the linked lead's email address.
-- LinkedIn leads have no email on file at all (CSV intake columns are
-- first_name/last_name/title/company/linkedin_url/location) -- if an
-- approved LinkedIn sequence ever reached 'approved_hitl', MKT-O5 would
-- either crash-and-retry-forever on that row or, worse, silently pick up
-- some other lead's email by accident. 'approved_manual' means "ready for
-- Kelvin to paste into LinkedIn himself" and MKT-O5's own query never
-- matches it, so it's structurally impossible for a LinkedIn DM to be
-- auto-emailed. See api/routers/outreach.py's approve endpoint for where
-- this status is chosen.
ALTER TABLE mse_dm_sequences ADD CONSTRAINT mse_dm_sequences_status_check
  CHECK (status IN (
    'pending_hitl', 'approved_hitl', 'approved_manual', 'rejected_hitl',
    'touch_1_sent', 'sequence_complete', 'suppressed'
  ));

CREATE INDEX IF NOT EXISTS idx_mse_dm_sequences_linkedin_lead ON mse_dm_sequences(linkedin_lead_id);
