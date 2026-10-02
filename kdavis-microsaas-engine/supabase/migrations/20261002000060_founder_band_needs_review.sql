-- Founder-title employee-band verification (Kelvin's decision, 2026-10-02).
--
-- Founder/CEO/co-founder titles are valid buyers only at <=50 people. That
-- was previously gated on an ATS open-role proxy; it is now gated on the
-- employee band stated on the company's LinkedIn page (one Brave query,
-- agents/marketing/founder_band.py).
--
-- A lookup has THREE outcomes, not two: the band is small (accept), the band
-- is large (reject), or nothing parseable was found. That third case is the
-- reason for this migration. Forcing it into 'none_found' would say "this
-- company has no buyer", which is false and would hide the lead; forcing it
-- into 'found' would let an unverified founder title send. 'needs_review'
-- says what is actually true -- a human has to look -- and keeps the row in
-- the buyer-research lane instead of silently resolving it either way.
--
-- headcount_band already exists on mse_leads and was always NULL; it now
-- carries the band the lookup read, so a later run never re-spends the query
-- and a reviewer can see the evidence the decision was made on.

ALTER TABLE mse_leads
    DROP CONSTRAINT IF EXISTS mse_leads_contact_status_check;

ALTER TABLE mse_leads
    ADD CONSTRAINT mse_leads_contact_status_check
    CHECK (
        contact_status IS NULL
        OR contact_status = ANY (ARRAY[
            'pending'::text,
            'found'::text,
            'none_found'::text,
            'needs_review'::text
        ])
    );

-- Why the band was accepted, rejected, or left for review. Free text rather
-- than an enum: the useful content is evidence ("LinkedIn states 201-500
-- employees"), not a category, and it is read by a human.
ALTER TABLE mse_leads
    ADD COLUMN IF NOT EXISTS headcount_band_source text;

COMMENT ON COLUMN mse_leads.headcount_band IS
    'Employee band stated on the company''s LinkedIn page (e.g. "11-50"), '
    'read by agents/marketing/founder_band.py. NULL means never looked up or '
    'nothing parseable was found -- see headcount_band_source.';

COMMENT ON COLUMN mse_leads.headcount_band_source IS
    'Evidence for the founder-band decision, e.g. "LinkedIn states 201-500 '
    'employees (> 50)". Set only for founder-shaped titles; other titles are '
    'valid at any company size and never trigger the lookup.';
