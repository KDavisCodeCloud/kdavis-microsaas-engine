-- Restrict the weekly Lead Finder sweep to products actually being sold
-- (Kelvin's decision 1b, 2026-10-02).
--
-- THE PROBLEM THIS SOLVES. The Sunday workflow read every row of
-- mse_icp_configs and ran a full find against each -- 9 products, including
-- 'building' and 'warming' ones nobody is selling yet. The 2026-10-02 manual
-- run proved the cost: it picked 797adb70, a shelved REAL-ESTATE product, and
-- spent 2h20m reaching lead 18 of 50 before being cancelled. At ~30 Brave
-- queries per product a full sweep is ~270 of the 900/month budget, every
-- week.
--
-- WHY A FLAG AND NOT selling_stage. selling_stage already exists and
-- 'active' currently happens to select the right two products, but the two
-- questions are different: selling_stage describes where a product is in its
-- lifecycle, while this asks "should the robot spend money finding strangers
-- for it this Sunday?". Those come apart immediately -- a warming product can
-- deserve outbound, and an active one can need its outbound paused without
-- being un-launched. Overloading selling_stage would make pausing outbound
-- mean lying about the product's stage.
--
-- Default FALSE: a newly configured ICP must be switched on deliberately.
-- Opt-in is the safe default for something that spends a metered budget and
-- emails strangers.

ALTER TABLE mse_icp_configs
    ADD COLUMN IF NOT EXISTS outbound_enabled boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN mse_icp_configs.outbound_enabled IS
    'Whether the weekly Lead Finder sweep may run for this product. Opt-in: '
    'defaults false so a new ICP config never silently starts spending Brave '
    'quota. Distinct from selling_stage, which describes lifecycle, not '
    'whether outbound prospecting is authorised right now.';

-- The two products being sold as of 2026-10-02.
--   9b6c8f36-985f-4d7a-a416-c40da89e23af  THD Agentic Systems Consulting
--   777a1852-f84c-49d8-890e-cd14670b7f6f  Cloud Decoded
-- Addressed by id rather than by name or stage so this is reproducible and
-- cannot quietly widen if another row later becomes 'active'.
UPDATE mse_icp_configs
   SET outbound_enabled = true
 WHERE product_id IN (
        '9b6c8f36-985f-4d7a-a416-c40da89e23af',
        '777a1852-f84c-49d8-890e-cd14670b7f6f'
 );

-- Note for whoever reads this next: cb6ea950-5038-47f4-9d51-f29d3f3044b5 has
-- vertical='consulting' and no product name, and is NOT the consulting
-- product being sold (that is 9b6c8f36, "THD Agentic Systems Consulting").
-- It stays disabled deliberately; do not "fix" it by enabling it.
