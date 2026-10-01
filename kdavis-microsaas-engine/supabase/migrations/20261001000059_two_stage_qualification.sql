-- Two-stage qualification for scraper v2 (Kelvin's decisions 1-5,
-- 2026-10-01). Purely additive: no column dropped, no CHECK narrowed, no
-- existing row rewritten.
--
-- THE PROBLEM THIS SOLVES. v2's first live runs dropped 23 of the 32
-- companies that had a matching open role, as "low fit". The cause was
-- structural, not a tuning accident: fit_score weights CONTACT seniority
-- at 0.45, but company-first sourcing finds the COMPANY first and the
-- contact lookup is budgeted and optional. A company with no contact found
-- could not reach the 0.50 threshold even with a perfect stack match, so
-- every contact-less company was discarded -- including real prospects
-- with six open platform roles.
--
-- The fix is to stop conflating two different questions:
--
--   Stage 1  Is this company worth pursuing?   No contact required.
--            matching role + resolved domain + exclusions pass
--            + size proxy in band.
--   Stage 2  Who do we talk to?                Only for Stage 1 passers.
--
-- A Stage 1 company with no contact yet is NOT dropped. It is written with
-- status='company_qualified' and contact_status='pending', and the next run
-- retries the contact lookup from the board-token cache at zero Brave cost.

-- =====================================================
-- mse_leads: two-stage state
-- =====================================================

-- 'company_qualified' is the new entry state for a company that cleared
-- Stage 1 but has no contact yet. It deliberately does NOT enter
-- 'pending_dm', because MKT-O2 would draft outreach to a company with no
-- named recipient.
alter table mse_leads drop constraint if exists mse_leads_status_check;
alter table mse_leads add constraint mse_leads_status_check
  check (status in (
    'company_qualified',  -- Stage 1 passed, awaiting a contact
    'pending_dm',         -- has a contact, ready for MKT-O2
    'pending_email',
    'contacted',
    'converted',
    'unsubscribed',
    'bounced',
    'disqualified'
  ));

-- The retry queue. 'pending' means Stage 2 has not succeeded YET and should
-- be retried next run; 'none_found' means it was tried enough times to stop
-- (tracked by contact_attempts) and is a manual-research candidate.
alter table mse_leads add column if not exists contact_status text;
alter table mse_leads drop constraint if exists mse_leads_contact_status_check;
alter table mse_leads add constraint mse_leads_contact_status_check
  check (contact_status is null or contact_status in ('pending','found','none_found'));

alter table mse_leads add column if not exists contact_attempts int not null default 0
  check (contact_attempts >= 0);

-- Stage 1 audit trail. Stored for the same reason score_reasons is: a
-- company that was excluded must be explainable months later without
-- re-running the scraper, and "it didn't score well" is not an explanation.
alter table mse_leads add column if not exists company_tags text[];
alter table mse_leads add column if not exists stage1_reasons text[];

-- How the domain was established -- jd_text / posting_url /
-- constructed_verified / brave / already_known. A wrong domain is traceable
-- to the step that produced it instead of being an unexplained value.
alter table mse_leads add column if not exists domain_source text;
alter table mse_leads drop constraint if exists mse_leads_domain_source_check;
alter table mse_leads add constraint mse_leads_domain_source_check
  check (domain_source is null or domain_source in
    ('jd_text','posting_url','constructed_verified','brave','already_known'));

-- The size proxy actually used. headcount_band stays in the schema but was
-- NULL on every single lead from the first live runs -- ATS job
-- descriptions essentially never state company headcount, so the open-role
-- count from the board cache is the proxy instead (in-ICP = 3-40).
alter table mse_leads add column if not exists size_proxy_open_roles int
  check (size_proxy_open_roles is null or size_proxy_open_roles >= 0);

-- The contact-retry queue read, run once per scout run.
create index if not exists idx_mse_leads_contact_pending
  on mse_leads (product_id, contact_status, contact_attempts)
  where contact_status = 'pending';

-- =====================================================
-- mse_icp_configs: role taxonomy (decision 2)
-- =====================================================
--
-- Shape: {"include": {...}, "negative": {...}, "intent_boost": {...}}
-- Each section is either {name: regex} or a bare list of literal terms --
-- agents/marketing/role_taxonomy.RoleTaxonomy.from_config accepts both, so
-- a config author who does not write regex can supply ["DevOps","SRE"].
-- A NULL or absent section falls back to that module's defaults rather
-- than matching nothing.
alter table mse_icp_configs add column if not exists role_taxonomy jsonb;

comment on column mse_icp_configs.role_taxonomy is
  'Role matching for scraper v2: {"include":{name:regex},"negative":{...},"intent_boost":{...}}. Sections may also be plain lists of literal terms. NULL/absent sections fall back to agents/marketing/role_taxonomy.py DEFAULT_*. Widened 2026-10-01 after the first live runs matched only 1.0-1.7% of real ATS titles.';

comment on column mse_leads.status is
  'company_qualified = Stage 1 passed (matching role + domain + exclusions + size proxy), no contact yet; pending_dm = has a contact and is ready for MKT-O2. See migration 20261001000059.';

comment on column mse_leads.size_proxy_open_roles is
  'Total open roles on the company ATS board, used as the company-size proxy because headcount is not stated in ATS text (in-ICP = 3-40). Costs no Brave quota -- read from mse_ats_board_tokens.';
