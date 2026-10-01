-- Scraper v2 qualification layer (2026-10-01).
--
-- Two parts, both purely additive:
--   1. mse_ats_board_tokens -- the board-token cache that makes
--      company-first ATS sourcing cheap. One Brave query discovers a
--      token; every later poll of that company's whole open-role list
--      costs zero Brave quota (see scrapers/ats_boards.py).
--   2. mse_leads qualification columns -- seniority, technographics,
--      firmographics, email confidence grade, fit/intent scores and the
--      routing decision, with score_reasons so a rejected lead can
--      always be explained rather than silently dropped.
--
-- No column is dropped, no CHECK is narrowed, no existing row is
-- rewritten. Every new mse_leads column is NULLable precisely so the
-- ~66 pre-v2 rows stay readable as "never scored by v2" instead of
-- being back-filled with invented values.

-- =====================================================
-- TABLE: mse_ats_board_tokens -- company-first ATS cache
-- =====================================================
create table if not exists mse_ats_board_tokens (
  id             uuid primary key default gen_random_uuid(),
  product_id     uuid references mse_products(id),
  provider       text not null
                   check (provider in ('greenhouse','lever','ashby','workable')),
  board_token    text not null,
  company        text,
  company_domain text,
  -- Observability for the cache itself: a board that has gone 404 or
  -- empty for weeks should stop being polled, and that is only visible
  -- if both outcomes are recorded.
  last_fetched_at   timestamptz,
  last_status       text check (last_status in ('ok','empty','failed')),
  open_role_count   int,
  consecutive_failures int not null default 0,
  discovered_from   text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (provider, board_token)
);

-- The hot read: "which boards are worth polling for this product".
create index if not exists idx_mse_ats_board_tokens_pollable
  on mse_ats_board_tokens (product_id, last_fetched_at)
  where consecutive_failures < 3;

create index if not exists idx_mse_ats_board_tokens_domain
  on mse_ats_board_tokens (company_domain)
  where company_domain is not null;

alter table mse_ats_board_tokens enable row level security;

drop policy if exists mse_ats_board_tokens_service_role on mse_ats_board_tokens;
create policy mse_ats_board_tokens_service_role on mse_ats_board_tokens
  for all to service_role using (true) with check (true);

-- Canonical role value is 'admin' (not 'owner'/'superadmin') -- same
-- model as mse_hitl_items in 20260831000034.
drop policy if exists mse_ats_board_tokens_read on mse_ats_board_tokens;
create policy mse_ats_board_tokens_read on mse_ats_board_tokens
  for select using (
    (auth.jwt() -> 'app_metadata' ->> 'role') in ('admin', 'hitl')
  );

-- =====================================================
-- mse_leads: qualification columns
-- =====================================================

-- Seniority tier of the CONTACT. 'unknown' is a real, expected value --
-- an ATS posting page names no hiring manager -- so it is in the CHECK
-- rather than being represented as NULL.
alter table mse_leads add column if not exists seniority text;
alter table mse_leads drop constraint if exists mse_leads_seniority_check;
alter table mse_leads add constraint mse_leads_seniority_check
  check (seniority is null or seniority in
    ('c_level','vp','head','director','manager','ic','unknown'));

-- Technographics: canonical tool names extracted from JD text.
alter table mse_leads add column if not exists stack_tags text[];

-- Firmographics. headcount_band is a band, never a fabricated exact
-- number; NULL means "not stated anywhere we could see", which is
-- explicitly NOT a disqualifier (see lead_qualification.headcount_in_band).
alter table mse_leads add column if not exists headcount_band text;
alter table mse_leads drop constraint if exists mse_leads_headcount_band_check;
alter table mse_leads add constraint mse_leads_headcount_band_check
  check (headcount_band is null or headcount_band in
    ('1-19','20-49','50-99','100-300','301-1000','1000+'));

alter table mse_leads add column if not exists funding_months int
  check (funding_months is null or funding_months >= 0);

-- Email confidence grade. Only 'valid' may enter MKT-O5 (Kelvin,
-- 2026-10-01); 'risky' covers catch-all domains and role addresses,
-- where a 250 on RCPT TO proves the domain answers, not that the
-- mailbox exists.
alter table mse_leads add column if not exists email_grade text;
alter table mse_leads drop constraint if exists mse_leads_email_grade_check;
alter table mse_leads add constraint mse_leads_email_grade_check
  check (email_grade is null or email_grade in ('valid','risky','invalid','unknown'));

alter table mse_leads add column if not exists fit_score numeric(4,3)
  check (fit_score is null or (fit_score >= 0 and fit_score <= 1));
alter table mse_leads add column if not exists intent_score numeric(4,3)
  check (intent_score is null or (intent_score >= 0 and intent_score <= 1));

-- Why those scores came out that way. Stored because "no silent
-- failures" applies to scoring too: a lead rejected at 0.42 fit must be
-- explainable months later without re-running the scraper.
alter table mse_leads add column if not exists score_reasons text[];

-- Routing decision. Domain-less leads go to the manual LinkedIn track
-- and are never emailed (Kelvin, 2026-10-01).
alter table mse_leads add column if not exists lead_route text;
alter table mse_leads drop constraint if exists mse_leads_lead_route_check;
alter table mse_leads add constraint mse_leads_lead_route_check
  check (lead_route is null or lead_route in
    ('outbound_email','manual_linkedin','reject'));

-- How many relevant roles that company has open -- the intent signal
-- company-first ATS sourcing exists to produce.
alter table mse_leads add column if not exists open_role_count int
  check (open_role_count is null or open_role_count >= 0);

-- The send gate reads (lead_route, email_grade) together on every run.
create index if not exists idx_mse_leads_route_grade
  on mse_leads (product_id, lead_route, email_grade)
  where lead_route is not null;

-- Ranked review of what v2 actually produced.
create index if not exists idx_mse_leads_scores
  on mse_leads (product_id, fit_score desc, intent_score desc)
  where fit_score is not null;
