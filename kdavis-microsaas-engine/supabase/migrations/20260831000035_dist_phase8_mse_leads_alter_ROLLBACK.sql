-- Rollback for 20260831000035_dist_phase8_mse_leads_alter.sql.
-- Run this only if the forward migration was applied and needs reverting.

drop index if exists idx_mse_leads_owner_stage;

alter table mse_leads
  drop column if exists owner,
  drop column if exists stage,
  drop column if exists next_action,
  drop column if exists next_action_due,
  drop column if exists last_activity_at;
