-- 069: buyer-only ICP for Cloud Decoded and consulting (Kelvin, 2026-11-07).
--
-- Scoped to exactly two product ids; no other product's row is touched.
-- job_titles are the budget owners only. Cloud Decoded's row previously
-- listed "SRE", "DevOps engineer", "platform engineer", "cloud engineer" and
-- "infrastructure engineer" -- the individual contributors who do the work,
-- not the people who buy. The enforced rule lives in code
-- (agents/marketing/buyer_gate.py); these rows feed search queries, size
-- bands and exclusions.

UPDATE mse_icp_configs SET
  job_titles = '["CTO","VP Engineering","VP Infrastructure","VP Platform","Head of Engineering","Head of Platform","Head of Infrastructure","Head of DevOps","Director of Engineering","Director of Platform Engineering","Director of Infrastructure","Founder","Co-founder","CEO"]'::jsonb,
  min_company_size = 20,
  max_company_size = 500,
  industries = '["software","SaaS","technology"]'::jsonb,
  exclude_domains = '["linkedin.com","indeed.com","ziprecruiter.com","glassdoor.com","monster.com","simplyhired.com","dice.com","index.dev","builtin.com","wellfound.com","otta.com","greenhouse.io","lever.co","ashbyhq.com","corvel.com","honeywell.com","boeing.com"]'::jsonb,
  exclude_criteria = '["job_board_or_aggregator","ats_host_as_company","staffing_or_recruiting_agency","it_staffing","msp","devops_consultancy","excluded_employer:corvel","excluded_employer:honeywell","excluded_employer:boeing","individual_contributor_contact","unverified_email_to_send_queue","no_aws_or_azure_signal"]'::jsonb,
  updated_at = now()
WHERE product_id = '777a1852-f84c-49d8-890e-cd14670b7f6f';

UPDATE mse_icp_configs SET
  job_titles = '["CTO","VP Engineering","VP Infrastructure","VP Platform","Head of Engineering","Head of Platform","Head of Infrastructure","Head of DevOps","Director of Engineering","Director of Platform Engineering","Director of Infrastructure","Founder","Co-founder","CEO"]'::jsonb,
  min_company_size = 20,
  max_company_size = 200,
  exclude_domains = '["linkedin.com","indeed.com","ziprecruiter.com","glassdoor.com","monster.com","simplyhired.com","dice.com","index.dev","builtin.com","wellfound.com","otta.com","greenhouse.io","lever.co","ashbyhq.com","corvel.com","honeywell.com","boeing.com"]'::jsonb,
  exclude_criteria = '["over_200_employees","no_tech_presence","palantir_or_databricks_at_scale","job_board_or_aggregator","ats_host_as_company","staffing_or_recruiting_agency","it_staffing","msp","devops_consultancy","excluded_employer:corvel","excluded_employer:honeywell","excluded_employer:boeing","individual_contributor_contact","unverified_email_to_send_queue"]'::jsonb,
  updated_at = now()
WHERE product_id = '9b6c8f36-985f-4d7a-a416-c40da89e23af';
