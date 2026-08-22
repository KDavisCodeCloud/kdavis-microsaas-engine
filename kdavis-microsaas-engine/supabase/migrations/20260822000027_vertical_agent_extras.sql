-- Migration 027: opportunity_pipeline.vertical_agent_extras
--
-- The four new industry vertical agents (Trades/Care/Service/Field,
-- agents/{trades,care,service,field}_intel/agent.py) return extra fields
-- beyond the fixed Opportunity Card schema -- facebook_groups_identified,
-- parts_integration_verdict, free_tier_differentiation,
-- youtube_content_angle, state_license_db_sources, raw_review_samples,
-- tam_warning (agents/orchestrator/agent.py's _tam_sanity_check), etc.
--
-- Without a real column, this data only ever existed in
-- node_write_pipeline's in-memory `source` dict for the lifetime of one
-- graph run. Brief generation (agents/factory/brief_generator.py) fires
-- later, via its own separate HTTP trigger
-- (POST /factory/generate-brief/{opportunity_id}), so by the time a human
-- clicks "generate brief" that run is long finished and this data would
-- otherwise be gone. Stored as freeform JSONB, not a fixed set of typed
-- columns -- each vertical agent's extra fields differ, and this is
-- consumed downstream by agents/factory/brief_generator.py's
-- generate_research_report_from_verdict() as an additive block, not
-- queried/filtered on directly.

ALTER TABLE opportunity_pipeline
  ADD COLUMN IF NOT EXISTS vertical_agent_extras JSONB NOT NULL DEFAULT '{}'::jsonb;
