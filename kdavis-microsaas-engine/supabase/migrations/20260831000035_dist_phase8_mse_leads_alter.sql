-- STAGED, NOT APPLIED. Per the explicit hard gate on this task: do not
-- run this against microsaas-prod in an unattended session. mse_leads is
-- written to by the Sunday lead-finder run (a real, currently-scheduled
-- production job) -- this file is ready for a human to review and apply,
-- either directly or via a Supabase branch, whichever Kelvin prefers.
--
-- Supabase branching could not be confirmed available on this project
-- (the /branches API returned an empty list, which is ambiguous between
-- "no branches exist yet" and "branching isn't enabled on this plan" --
-- creating one to find out risks incurring real, undisclosed cost on a
-- paid feature, so this was not attempted). Local Postgres/Docker was not
-- available in this environment either (dockerd not running, no
-- permission to start it) -- so this migration's raw DDL has NOT been
-- validated against a live copy of the schema, only reviewed by hand.
-- The service-layer code that will consume these new columns (CRM lead
-- pipeline in Decoded Empire OS) IS tested, against the existing
-- Fake-Supabase mock pattern already used throughout this repo's test
-- suite -- that tests the application logic correctly, but not this raw
-- SQL against a real engine. Disclosed, not glossed over.

alter table mse_leads
  add column owner            text,
  add column stage            text not null default 'new'
    check (stage in ('new','contacted','replied','qualified','demo','won','lost')),
  add column next_action      text,
  add column next_action_due  date,
  add column last_activity_at timestamptz;

create index if not exists idx_mse_leads_owner_stage
  on mse_leads (owner, stage, next_action_due);
