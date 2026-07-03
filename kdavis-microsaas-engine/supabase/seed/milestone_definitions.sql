-- Seed: baseline milestone definitions per product
-- Adapt milestone_key and threshold per product vertical at launch.

-- Generic milestone set — override in product-specific seed files
INSERT INTO milestones (tenant_id, milestone_key, threshold)
SELECT
  t.id,
  m.milestone_key,
  m.threshold
FROM tenants t
CROSS JOIN (
  VALUES
    ('first_event',       1),
    ('ten_events',        10),
    ('fifty_events',      50),
    ('hundred_events',    100),
    ('five_hundred_events', 500)
) AS m(milestone_key, threshold)
ON CONFLICT (tenant_id, milestone_key) DO NOTHING;
