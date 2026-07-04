-- Migration 001: Core retention schema
-- Every micro SaaS product built in this engine starts here.

CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'starter' CHECK (tier IN ('starter', 'growth', 'scale')),
  stripe_customer_id TEXT UNIQUE,
  stripe_subscription_id TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'churned')),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE usage_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_usage_events_tenant_created ON usage_events(tenant_id, created_at DESC);

CREATE TABLE milestones (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  milestone_key TEXT NOT NULL,
  threshold INTEGER NOT NULL,
  achieved_at TIMESTAMPTZ,
  notified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(tenant_id, milestone_key)
);

CREATE TABLE retention_sequences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  sequence_type TEXT NOT NULL CHECK (sequence_type IN ('reengagement_7d', 'reengagement_21d', 'prebilling')),
  current_step INTEGER DEFAULT 0,
  last_triggered_at TIMESTAMPTZ,
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'suppressed')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE weekly_digest_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  sent_at TIMESTAMPTZ DEFAULT NOW(),
  open_at TIMESTAMPTZ,
  click_at TIMESTAMPTZ,
  value_metrics JSONB NOT NULL DEFAULT '{}',
  skipped_reason TEXT
);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE milestones ENABLE ROW LEVEL SECURITY;
ALTER TABLE retention_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE weekly_digest_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON usage_events
  USING (tenant_id = current_setting('app.tenant_id')::UUID);

CREATE POLICY tenant_isolation ON milestones
  USING (tenant_id = current_setting('app.tenant_id')::UUID);

CREATE POLICY tenant_isolation ON retention_sequences
  USING (tenant_id = current_setting('app.tenant_id')::UUID);

CREATE POLICY tenant_isolation ON weekly_digest_log
  USING (tenant_id = current_setting('app.tenant_id')::UUID);
