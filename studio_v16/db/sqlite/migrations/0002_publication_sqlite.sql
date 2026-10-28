-- Additive V1.6 publication contract repair. No legacy publication is promoted.
-- Frozen WG-A schemas and 0001 migration are unchanged.
ALTER TABLE source_publications
  ADD COLUMN patch_sha256 TEXT COLLATE BINARY CHECK(patch_sha256 IS NULL OR (length(patch_sha256)=64 AND patch_sha256 NOT GLOB '*[^0-9a-f]*'));
CREATE TABLE publication_intents (
 publication_id TEXT COLLATE BINARY PRIMARY KEY REFERENCES publication_keys(publication_id),
 idempotency_key TEXT COLLATE BINARY CHECK(idempotency_key IS NULL OR (length(idempotency_key)=64 AND idempotency_key NOT GLOB '*[^0-9a-f]*')) NOT NULL UNIQUE,
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 grant_id TEXT COLLATE BINARY NOT NULL REFERENCES grants(grant_id),
 principal_id TEXT COLLATE BINARY NOT NULL REFERENCES principals(principal_id),
 state_revision TEXT COLLATE BINARY NOT NULL,
 authority_revision TEXT COLLATE BINARY NOT NULL,
 policy_revision TEXT COLLATE BINARY NOT NULL,
 assignment_epoch INTEGER NOT NULL CHECK (assignment_epoch > 0),
 lease_generation INTEGER NOT NULL CHECK (lease_generation > 0),
 owner_decision_ref TEXT COLLATE BINARY NOT NULL REFERENCES decisions(decision_id),
 qa_review_id TEXT COLLATE BINARY NOT NULL REFERENCES reviews(review_id),
 evidence_sha256 TEXT COLLATE BINARY CHECK(evidence_sha256 IS NULL OR (length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 intent_sha256 TEXT COLLATE BINARY CHECK(intent_sha256 IS NULL OR (length(intent_sha256)=64 AND intent_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 intent_json TEXT CHECK(intent_json IS NULL OR json_valid(intent_json)) NOT NULL,
 provider TEXT COLLATE BINARY NOT NULL CHECK(provider IN ('GITHUB','GOOGLE_DRIVE')),
 patch_sha256 TEXT COLLATE BINARY CHECK(patch_sha256 IS NULL OR (length(patch_sha256)=64 AND patch_sha256 NOT GLOB '*[^0-9a-f]*')),
 proposed_tree_sha256 TEXT COLLATE BINARY CHECK(proposed_tree_sha256 IS NULL OR (length(proposed_tree_sha256)=64 AND proposed_tree_sha256 NOT GLOB '*[^0-9a-f]*')),
 expected_revision TEXT COLLATE BINARY,
 outbox_id TEXT COLLATE BINARY NOT NULL UNIQUE REFERENCES outbox(outbox_id),
 status TEXT COLLATE BINARY NOT NULL DEFAULT 'PREPARED' CHECK(status IN ('PREPARED','VERIFIED','CONFLICT','BLOCKED')),
 verified_receipt_json TEXT CHECK(verified_receipt_json IS NULL OR json_valid(verified_receipt_json)),
 conflict_receipt_json TEXT CHECK(conflict_receipt_json IS NULL OR json_valid(conflict_receipt_json)),
 created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000),
 verified_at INTEGER,
 CHECK ((provider='GITHUB' AND patch_sha256 IS NOT NULL AND proposed_tree_sha256 IS NOT NULL AND expected_revision IS NULL)
     OR (provider='GOOGLE_DRIVE' AND patch_sha256 IS NULL AND proposed_tree_sha256 IS NULL AND expected_revision IS NOT NULL))
) STRICT;
-- Old records lack the reviewed full intent and stay outside the trusted read-side.
CREATE INDEX publication_intents_job_idx ON publication_intents(job_id, status);


CREATE TRIGGER publication_identity_immutable BEFORE UPDATE ON publication_intents
WHEN NEW.publication_id IS NOT OLD.publication_id OR NEW.idempotency_key IS NOT OLD.idempotency_key OR NEW.job_id IS NOT OLD.job_id OR NEW.grant_id IS NOT OLD.grant_id OR NEW.principal_id IS NOT OLD.principal_id OR NEW.state_revision IS NOT OLD.state_revision OR NEW.authority_revision IS NOT OLD.authority_revision OR NEW.policy_revision IS NOT OLD.policy_revision OR NEW.assignment_epoch IS NOT OLD.assignment_epoch OR NEW.lease_generation IS NOT OLD.lease_generation OR NEW.owner_decision_ref IS NOT OLD.owner_decision_ref OR NEW.qa_review_id IS NOT OLD.qa_review_id OR NEW.evidence_sha256 IS NOT OLD.evidence_sha256 OR NEW.intent_sha256 IS NOT OLD.intent_sha256 OR NEW.intent_json IS NOT OLD.intent_json OR NEW.provider IS NOT OLD.provider OR NEW.patch_sha256 IS NOT OLD.patch_sha256 OR NEW.proposed_tree_sha256 IS NOT OLD.proposed_tree_sha256 OR NEW.expected_revision IS NOT OLD.expected_revision OR NEW.outbox_id IS NOT OLD.outbox_id OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_INTENT'); END;
CREATE TRIGGER publication_no_delete BEFORE DELETE ON publication_intents
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_INTENT'); END;
CREATE TRIGGER publication_terminal BEFORE UPDATE ON publication_intents
WHEN (OLD.status IN ('VERIFIED','CONFLICT','BLOCKED') AND
 (NEW.status IS NOT OLD.status OR NEW.verified_receipt_json IS NOT OLD.verified_receipt_json
 OR NEW.conflict_receipt_json IS NOT OLD.conflict_receipt_json OR NEW.verified_at IS NOT OLD.verified_at))
 OR (NEW.status='VERIFIED' AND (NEW.verified_receipt_json IS NULL OR NEW.verified_at IS NULL))
 OR (NEW.status='CONFLICT' AND NEW.conflict_receipt_json IS NULL)
BEGIN SELECT RAISE(ABORT,'INVALID_TERMINAL_RECEIPT'); END;
CREATE TABLE effect_dispatches (
 dispatch_id TEXT PRIMARY KEY,
 publication_id TEXT NOT NULL UNIQUE REFERENCES publication_intents(publication_id),
 intent_sha256 TEXT NOT NULL CHECK(length(intent_sha256)=64 AND intent_sha256 NOT GLOB '*[^0-9a-f]*'),
 principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 assignment_epoch INTEGER NOT NULL CHECK(assignment_epoch>0),
 lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
 policy_revision TEXT NOT NULL,
 provider_operation_id TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'DISPATCH_STARTED' CHECK(status='DISPATCH_STARTED'),
 dispatch_started_at INTEGER NOT NULL
) STRICT;
CREATE INDEX dispatch_publication_status_idx ON effect_dispatches(publication_id,status);
CREATE TRIGGER dispatch_no_update BEFORE UPDATE ON effect_dispatches
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_DISPATCH'); END;
CREATE TRIGGER dispatch_no_delete BEFORE DELETE ON effect_dispatches
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_DISPATCH'); END;
CREATE TRIGGER source_publications_terminal BEFORE UPDATE ON source_publications
WHEN OLD.status IN ('VERIFIED','CONFLICT','BLOCKED') AND (NEW.publication_id IS NOT OLD.publication_id OR NEW.job_id IS NOT OLD.job_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.repo_id IS NOT OLD.repo_id OR NEW.work_branch IS NOT OLD.work_branch OR NEW.baseline_sha IS NOT OLD.baseline_sha OR NEW.proposed_tree_hash IS NOT OLD.proposed_tree_hash OR NEW.idempotency_key IS NOT OLD.idempotency_key OR NEW.provider_request_id IS NOT OLD.provider_request_id OR NEW.result_commit_sha IS NOT OLD.result_commit_sha OR NEW.observed_sha256 IS NOT OLD.observed_sha256 OR NEW.status IS NOT OLD.status OR NEW.verified_at IS NOT OLD.verified_at OR NEW.patch_sha256 IS NOT OLD.patch_sha256)
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TERMINAL'); END;
CREATE TRIGGER source_publications_no_delete BEFORE DELETE ON source_publications BEGIN SELECT RAISE(ABORT,'IMMUTABLE_PUBLICATION'); END;
CREATE TRIGGER document_publications_terminal BEFORE UPDATE ON document_publications
WHEN OLD.status IN ('VERIFIED','CONFLICT','BLOCKED') AND (NEW.publication_id IS NOT OLD.publication_id OR NEW.job_id IS NOT OLD.job_id OR NEW.attempt_id IS NOT OLD.attempt_id OR NEW.drive_file_id IS NOT OLD.drive_file_id OR NEW.expected_revision IS NOT OLD.expected_revision OR NEW.exported_sha256 IS NOT OLD.exported_sha256 OR NEW.idempotency_key IS NOT OLD.idempotency_key OR NEW.resulting_revision IS NOT OLD.resulting_revision OR NEW.observed_sha256 IS NOT OLD.observed_sha256 OR NEW.provider_request_id IS NOT OLD.provider_request_id OR NEW.status IS NOT OLD.status OR NEW.verified_at IS NOT OLD.verified_at)
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TERMINAL'); END;
CREATE TRIGGER document_publications_no_delete BEFORE DELETE ON document_publications BEGIN SELECT RAISE(ABORT,'IMMUTABLE_PUBLICATION'); END;
CREATE TRIGGER outbox_terminal BEFORE UPDATE ON outbox
WHEN OLD.status IN ('DELIVERED','BLOCKED','DEAD_LETTER') AND (NEW.outbox_id IS NOT OLD.outbox_id OR NEW.event_id IS NOT OLD.event_id OR NEW.kind IS NOT OLD.kind OR NEW.destination IS NOT OLD.destination OR NEW.status IS NOT OLD.status OR NEW.attempts IS NOT OLD.attempts OR NEW.next_retry_at IS NOT OLD.next_retry_at OR NEW.provider_ref IS NOT OLD.provider_ref OR NEW.payload_sha256 IS NOT OLD.payload_sha256 OR NEW.provider_effect_sha256 IS NOT OLD.provider_effect_sha256 OR NEW.dead_letter_reason IS NOT OLD.dead_letter_reason OR NEW.claim_token IS NOT OLD.claim_token OR NEW.claim_expires_at IS NOT OLD.claim_expires_at OR NEW.created_at IS NOT OLD.created_at)
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TERMINAL'); END;
CREATE TRIGGER outbox_no_delete BEFORE DELETE ON outbox BEGIN SELECT RAISE(ABORT,'IMMUTABLE_PUBLICATION'); END;
