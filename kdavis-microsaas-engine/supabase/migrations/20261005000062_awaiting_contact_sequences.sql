-- Contact-first gate for MKT-O2 (Kelvin's decision 2, 2026-10-05).
--
-- THE PROBLEM. MKT-O2 drafted a sequence for any lead it was handed and never
-- asked whether the contact was the BUYER. Of the 11 sequences sitting in
-- pending_hitl on 2026-10-05, six were drafted to someone contact_fit
-- rejects:
--
--   Ogury        Vice President of Marketing          (wrong function)
--   Alongside    director of Software Development     (not a buyer title)
--   MeridianLink Director, IT Applications Architecture
--   Clutch       COO                                  (twice -- duplicate drafts)
--   SingleStore  Director                             (bare, unqualified)
--
-- Each had contact_status='found', because 'found' only ever meant "a name was
-- discovered", never "the right person was found". So a human reviewing the
-- approval queue was being asked to approve copy addressed to the wrong
-- person, and the fit rules written on 2026-10-01 were being applied at
-- discovery time but not at DRAFT time.
--
-- 'awaiting_contact' is the state those drafts belong in. NOT 'rejected_hitl':
-- rejection is a judgement about the COPY, it is terminal, and it would bury a
-- company that is still perfectly qualified -- Stage 1 passed on the company,
-- only the contact was wrong. awaiting_contact says "this draft is parked
-- until a real buyer is named", which is the truth, and lets the row be
-- regenerated in place rather than duplicated.

ALTER TABLE mse_dm_sequences
    DROP CONSTRAINT IF EXISTS mse_dm_sequences_status_check;

ALTER TABLE mse_dm_sequences
    ADD CONSTRAINT mse_dm_sequences_status_check
    CHECK (status = ANY (ARRAY[
        'awaiting_contact'::text,   -- parked: no fit-accepted buyer yet
        'pending_hitl'::text,
        'approved_hitl'::text,
        'approved_manual'::text,
        'rejected_hitl'::text,
        'touch_1_sending'::text,
        'touch_1_sent'::text,
        'touch_2_sending'::text,
        'sequence_complete'::text,
        'suppressed'::text,
        'approval_expired'::text
    ]));

COMMENT ON COLUMN mse_dm_sequences.status IS
    'Lifecycle. awaiting_contact = drafted but parked because the lead has no '
    'fit-accepted buyer; it regenerates in place once one is named, and is '
    'deliberately distinct from rejected_hitl, which is a terminal judgement '
    'about the COPY. Only approved_hitl is ever polled for sending.';
