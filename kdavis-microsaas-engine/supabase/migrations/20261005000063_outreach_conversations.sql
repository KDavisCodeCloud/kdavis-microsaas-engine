-- Conversations lane for the CEO Decoded Outreach section
-- (Kelvin's decision 3d, 2026-10-05).
--
-- WHY A TABLE AND NOT MORE SEQUENCE STATUSES. mse_dm_sequences.status is the
-- lifecycle of a DRAFT: written, approved, sent, complete. A conversation is a
-- different thing with a different shape -- it moves forward through stages,
-- each stage happens at a time worth recording, and it can end in won or lost
-- long after the sequence itself is finished. Folding "proposal sent" into a
-- draft's status would make the draft's own history unreadable and would lose
-- the date each stage happened, which is the only way to measure the funnel
-- 5b asks for.
--
-- 'replied' IS added to the sequence status, because a reply is a fact about
-- the sequence: it must stop the remaining touches. That is the one overlap.

ALTER TABLE mse_dm_sequences
    DROP CONSTRAINT IF EXISTS mse_dm_sequences_status_check;

ALTER TABLE mse_dm_sequences
    ADD CONSTRAINT mse_dm_sequences_status_check
    CHECK (status = ANY (ARRAY[
        'awaiting_contact'::text,
        'pending_hitl'::text,
        'approved_hitl'::text,
        'approved_manual'::text,
        'rejected_hitl'::text,
        'touch_1_sending'::text,
        'touch_1_sent'::text,
        'touch_2_sending'::text,
        'sequence_complete'::text,
        -- A reply STOPS the sequence. Terminal, and deliberately distinct from
        -- sequence_complete (which means "we sent everything and heard
        -- nothing") -- conflating them would hide the only outcome that
        -- matters from every funnel count.
        'replied'::text,
        'suppressed'::text,
        'approval_expired'::text
    ]));

CREATE TABLE IF NOT EXISTS mse_outreach_conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- product_id on every new table (platform non-negotiable).
    product_id uuid NOT NULL,
    tenant_id uuid,
    lead_id uuid REFERENCES mse_leads(id) ON DELETE CASCADE,
    sequence_id uuid REFERENCES mse_dm_sequences(id) ON DELETE SET NULL,

    -- The furthest stage reached. Ordered, and only ever moved forward by the
    -- API so a mis-click cannot silently regress a won deal to "replied".
    stage text NOT NULL DEFAULT 'replied'
        CHECK (stage = ANY (ARRAY['replied'::text, 'call_booked'::text,
                                  'proposal_sent'::text, 'won'::text, 'lost'::text])),

    -- One timestamp per stage rather than a single updated_at: the funnel
    -- needs "how many replies became calls THIS WEEK", which a single
    -- current-stage column cannot answer after the fact.
    replied_at timestamptz,
    call_booked_at timestamptz,
    proposal_sent_at timestamptz,
    won_at timestamptz,
    lost_at timestamptz,
    lost_reason text,

    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- One conversation per lead. A second row for the same lead would double every
-- funnel count, which is the bug class this session keeps finding.
CREATE UNIQUE INDEX IF NOT EXISTS mse_outreach_conversations_lead_uniq
    ON mse_outreach_conversations (lead_id)
    WHERE lead_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS mse_outreach_conversations_stage_idx
    ON mse_outreach_conversations (product_id, stage);

ALTER TABLE mse_outreach_conversations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mse_outreach_conversations_service_role ON mse_outreach_conversations;
CREATE POLICY mse_outreach_conversations_service_role
    ON mse_outreach_conversations
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

COMMENT ON TABLE mse_outreach_conversations IS
    'A prospect who replied, and how far that went. Separate from '
    'mse_dm_sequences.status, which is the lifecycle of a DRAFT: a '
    'conversation outlives its sequence and needs a timestamp per stage so '
    'the weekly funnel can be computed after the fact.';
