-- Additive V1.6 publication contract repair. No legacy publication is promoted.
-- Frozen WG-A schemas and 0001 migration are unchanged.
ALTER TABLE nomad_v16.source_publications
  ADD COLUMN patch_sha256 char(64);
CREATE TABLE nomad_v16.publication_intents (
 publication_id text PRIMARY KEY REFERENCES nomad_v16.publication_keys(publication_id),
 idempotency_key char(64) NOT NULL UNIQUE,
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 grant_id text NOT NULL REFERENCES nomad_v16.grants(grant_id),
 principal_id text NOT NULL REFERENCES nomad_v16.principals(principal_id),
 state_revision text NOT NULL,
 authority_revision text NOT NULL,
 policy_revision text NOT NULL,
 assignment_epoch bigint NOT NULL CHECK (assignment_epoch > 0),
 lease_generation bigint NOT NULL CHECK (lease_generation > 0),
 owner_decision_ref text NOT NULL REFERENCES nomad_v16.decisions(decision_id),
 qa_review_id text NOT NULL REFERENCES nomad_v16.reviews(review_id),
 evidence_sha256 char(64) NOT NULL,
 intent_sha256 char(64) NOT NULL,
 intent_json jsonb NOT NULL,
 provider text NOT NULL CHECK(provider IN ('GITHUB','GOOGLE_DRIVE')),
 patch_sha256 char(64),
 proposed_tree_sha256 char(64),
 expected_revision text,
 outbox_id text NOT NULL UNIQUE REFERENCES nomad_v16.outbox(outbox_id),
 status text NOT NULL DEFAULT 'PREPARED' CHECK(status IN ('PREPARED','VERIFIED','CONFLICT','BLOCKED')),
 verified_receipt_json jsonb,
 conflict_receipt_json jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 verified_at timestamptz,
 CHECK ((provider='GITHUB' AND patch_sha256 IS NOT NULL AND proposed_tree_sha256 IS NOT NULL AND expected_revision IS NULL)
     OR (provider='GOOGLE_DRIVE' AND patch_sha256 IS NULL AND proposed_tree_sha256 IS NULL AND expected_revision IS NOT NULL))
);
-- Old records lack the reviewed full intent and stay outside the trusted read-side.
CREATE INDEX publication_intents_job_idx ON nomad_v16.publication_intents(job_id, status);

-- Immutable reservation identity; reconciliation may only change status/receipt.
CREATE FUNCTION nomad_v16.protect_publication_intent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'publication intent cannot be deleted'; END IF;
 IF (OLD.publication_id,OLD.idempotency_key,OLD.job_id,OLD.grant_id,OLD.principal_id,
     OLD.state_revision,OLD.authority_revision,OLD.policy_revision,OLD.assignment_epoch,
     OLD.lease_generation,OLD.owner_decision_ref,OLD.qa_review_id,OLD.evidence_sha256,
     OLD.intent_sha256,OLD.intent_json,OLD.provider,OLD.patch_sha256,
     OLD.proposed_tree_sha256,OLD.expected_revision,OLD.outbox_id)
    IS DISTINCT FROM
    (NEW.publication_id,NEW.idempotency_key,NEW.job_id,NEW.grant_id,NEW.principal_id,
     NEW.state_revision,NEW.authority_revision,NEW.policy_revision,NEW.assignment_epoch,
     NEW.lease_generation,NEW.owner_decision_ref,NEW.qa_review_id,NEW.evidence_sha256,
     NEW.intent_sha256,NEW.intent_json,NEW.provider,NEW.patch_sha256,
     NEW.proposed_tree_sha256,NEW.expected_revision,NEW.outbox_id)
 THEN RAISE EXCEPTION 'publication intent is immutable'; END IF;
 IF OLD.status IN ('VERIFIED','CONFLICT','BLOCKED') AND
    (NEW.status!=OLD.status OR
     OLD.verified_receipt_json IS DISTINCT FROM NEW.verified_receipt_json OR
     OLD.conflict_receipt_json IS DISTINCT FROM NEW.conflict_receipt_json)
 THEN RAISE EXCEPTION 'terminal publication cannot be changed'; END IF;
 IF NEW.status='VERIFIED' AND (NEW.verified_receipt_json IS NULL OR NEW.verified_at IS NULL)
 THEN RAISE EXCEPTION 'verified publication requires evidence'; END IF;
 IF NEW.status='CONFLICT' AND NEW.conflict_receipt_json IS NULL
 THEN RAISE EXCEPTION 'conflict requires evidence'; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER guard_publication_intent BEFORE UPDATE OR DELETE ON nomad_v16.publication_intents
 FOR EACH ROW EXECUTE FUNCTION nomad_v16.protect_publication_intent();
