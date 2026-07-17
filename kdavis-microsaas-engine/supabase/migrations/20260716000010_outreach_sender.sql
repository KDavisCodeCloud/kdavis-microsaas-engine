-- Migration 010: Outreach sender support
--
-- mkt_o5_sequence_sender needs to know when touch_1 was sent to schedule
-- touch_2 three days later, and mse_apollo_leads needs a way to track
-- manual LinkedIn outreach independently from the automated email channel
-- (a lead can have both an email sequence running AND be flagged for
-- manual LinkedIn contact in parallel — reusing the existing `status`
-- column for both would conflate the two channels' completion state).

ALTER TABLE mse_dm_sequences ADD COLUMN IF NOT EXISTS touch_1_sent_at TIMESTAMPTZ;
ALTER TABLE mse_dm_sequences ADD COLUMN IF NOT EXISTS touch_2_sent_at TIMESTAMPTZ;

ALTER TABLE mse_apollo_leads ADD COLUMN IF NOT EXISTS linkedin_contacted_at TIMESTAMPTZ;
