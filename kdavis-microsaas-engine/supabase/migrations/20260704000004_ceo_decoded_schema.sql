-- Migration 004: CEO Decoded dashboard schema
-- Tables: team_members, agent_events, hitl_queue, operating_stack,
--         build_queue, session_log, gap_tracker, legal_documents,
--         advisory_threads, hitl_routing_rules

-- Team members (Kelvin / Wife / Son)
CREATE TABLE IF NOT EXISTS team_members (
  id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name               TEXT        NOT NULL,
  email              TEXT        UNIQUE NOT NULL,
  role               TEXT        NOT NULL,
  department_access  TEXT[]      NOT NULL DEFAULT '{}',
  permission_level   TEXT        NOT NULL DEFAULT 'read'
                                 CHECK (permission_level IN ('admin', 'write', 'read')),
  last_active_at     TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Agent events (real-time activity feed)
CREATE TABLE IF NOT EXISTS agent_events (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_name  TEXT        NOT NULL,
  department  TEXT        NOT NULL,
  action      TEXT        NOT NULL,
  verdict     TEXT        NOT NULL DEFAULT 'pending'
              CHECK (verdict IN ('pass', 'flagged', 'pending')),
  metadata    JSONB       NOT NULL DEFAULT '{}',
  product     TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HITL approval queue
CREATE TABLE IF NOT EXISTS hitl_queue (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_name       TEXT        NOT NULL,
  proposed_action  TEXT        NOT NULL,
  blast_radius     TEXT        NOT NULL DEFAULT 'low'
                   CHECK (blast_radius IN ('low', 'medium', 'high')),
  confidence_pct   INTEGER     NOT NULL DEFAULT 0
                   CHECK (confidence_pct BETWEEN 0 AND 100),
  status           TEXT        NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected')),
  routed_to        UUID        REFERENCES team_members(id),
  resolved_by      UUID        REFERENCES team_members(id),
  resolved_at      TIMESTAMPTZ,
  metadata         JSONB       NOT NULL DEFAULT '{}',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Operating stack cost tracker
CREATE TABLE IF NOT EXISTS operating_stack (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  service_name     TEXT        NOT NULL UNIQUE,
  category         TEXT        NOT NULL,
  monthly_cost_usd NUMERIC(10,2) NOT NULL DEFAULT 0,
  status           TEXT        NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'paused')),
  notes            TEXT,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Build queue
CREATE TABLE IF NOT EXISTS build_queue (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  priority   TEXT        NOT NULL DEFAULT 'P2'
             CHECK (priority IN ('P1', 'P2', 'P3')),
  item       TEXT        NOT NULL,
  repo       TEXT,
  owner      UUID        REFERENCES team_members(id),
  status     TEXT        NOT NULL DEFAULT 'queued'
             CHECK (status IN ('queued', 'in_progress', 'done')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Session log (append-only — no delete)
CREATE TABLE IF NOT EXISTS session_log (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  session_date DATE        NOT NULL DEFAULT CURRENT_DATE,
  product      TEXT,
  summary      TEXT        NOT NULL,
  operator_id  UUID        REFERENCES team_members(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- GAP tracker
CREATE TABLE IF NOT EXISTS gap_tracker (
  id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  gap_name   TEXT        NOT NULL,
  product    TEXT,
  status     TEXT        NOT NULL DEFAULT 'open'
             CHECK (status IN ('open', 'closed')),
  notes      TEXT,
  closed_at  TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Legal document vault
CREATE TABLE IF NOT EXISTS legal_documents (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_name        TEXT        NOT NULL,
  product         TEXT,
  version         TEXT        NOT NULL DEFAULT '1.0',
  storage_path    TEXT,
  last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Advisory conversation threads
CREATE TABLE IF NOT EXISTS advisory_threads (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  advisor_role   TEXT        NOT NULL CHECK (advisor_role IN ('CFO', 'CMO', 'CTO')),
  advisor_name   TEXT        NOT NULL,
  message        TEXT        NOT NULL,
  role           TEXT        NOT NULL DEFAULT 'user'
                 CHECK (role IN ('user', 'advisor')),
  memory_summary TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HITL routing rules
CREATE TABLE IF NOT EXISTS hitl_routing_rules (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  action_type TEXT        NOT NULL UNIQUE,
  routes_to   UUID        REFERENCES team_members(id),
  description TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Enable Realtime on the two tables the dashboard subscribes to
ALTER PUBLICATION supabase_realtime ADD TABLE agent_events;
ALTER PUBLICATION supabase_realtime ADD TABLE hitl_queue;

-- Seed: operating stack services
INSERT INTO operating_stack (service_name, category, monthly_cost_usd, status) VALUES
  ('Supabase',       'Database / Auth',   25.00,  'active'),
  ('Vercel',         'Hosting',           20.00,  'active'),
  ('Anthropic API',  'AI / LLM',          50.00,  'active'),
  ('n8n (self-host)','Automation',         5.00,  'active'),
  ('Resend',         'Email',              9.00,  'active'),
  ('GitHub',         'Version Control',    4.00,  'active'),
  ('Videomule',      'Video Processing',  50.00,  'paused'),
  ('ElevenLabs',     'Voice AI',          22.00,  'paused'),
  ('HeyGen',         'Video AI',          29.00,  'paused')
ON CONFLICT (service_name) DO NOTHING;

-- Seed: team members
INSERT INTO team_members (name, email, role, department_access, permission_level) VALUES
  ('Kelvin Davis', 'kdav2k5@gmail.com', 'CEO', ARRAY['overview','finance','marketing','rnd','hr','tech','legal','ops','advisory','video'], 'admin'),
  ('Wife',         'wife@decodedempire.com',  'COO', ARRAY['overview','marketing','hr','ops','video'], 'write'),
  ('Son',          'son@decodedempire.com',   'CTO', ARRAY['overview','rnd','tech'], 'read')
ON CONFLICT (email) DO NOTHING;

-- Seed: gap tracker (MSE build order)
INSERT INTO gap_tracker (gap_name, product, status) VALUES
  ('GAP 1 — API foundation',              'MSE', 'closed'),
  ('GAP 2 — Core schema',                 'MSE', 'closed'),
  ('GAP 3 — Config setup',                'MSE', 'closed'),
  ('GAP 4 — Stripe webhook',              'MSE', 'closed'),
  ('GAP 5 — n8n setup',                   'MSE', 'closed'),
  ('GAP 6 — Weekly digest workflow',      'MSE', 'closed'),
  ('GAP 7 — Retention sequences workflow','MSE', 'closed'),
  ('GAP 8 — Resend integration',          'MSE', 'closed'),
  ('GAP 9 — Opportunity pipeline schema', 'MSE', 'closed'),
  ('GAP 10 — RLS auth.uid() migration',   'MSE', 'closed'),
  ('GAP 11 — Supabase client refactor',   'MSE', 'closed'),
  ('GAP 12 — Legal documents',            'MSE', 'closed'),
  ('GAP 13 — Agent cadence (orchestrator + aggregator)', 'MSE', 'closed'),
  ('CEO Decoded dashboard',               'CEO Decoded', 'open'),
  ('MSE dashboard',                       'MSE', 'open'),
  ('Week 2 — Market sizing agent',        'MSE', 'open')
ON CONFLICT DO NOTHING;
