-- SQLite core realization; immutable once applied. UTC times are epoch microseconds.
CREATE TABLE principals (
 principal_id TEXT COLLATE BINARY PRIMARY KEY, issuer TEXT COLLATE BINARY NOT NULL, subject TEXT COLLATE BINARY NOT NULL,
 kind TEXT COLLATE BINARY NOT NULL, status TEXT COLLATE BINARY NOT NULL CHECK (status IN ('ACTIVE','REVOKED')),
 created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), UNIQUE(issuer, subject)
) STRICT;
CREATE TABLE jobs (
 job_id TEXT COLLATE BINARY PRIMARY KEY, parent_issue_ref TEXT COLLATE BINARY NOT NULL UNIQUE, owner_head_id TEXT COLLATE BINARY NOT NULL,
 state TEXT COLLATE BINARY NOT NULL CHECK (state IN ('APPROVED','READY','CLAIMED','YIELDED','SUBMITTED','DONE','BLOCKED','CANCELLED')),
 approved_scope_hash TEXT COLLATE BINARY CHECK(approved_scope_hash IS NULL OR (length(approved_scope_hash)=64 AND approved_scope_hash NOT GLOB '*[^0-9a-f]*')) NOT NULL, authority_revision TEXT COLLATE BINARY NOT NULL,
 source_repo TEXT COLLATE BINARY NOT NULL, source_base_sha TEXT COLLATE BINARY CHECK(source_base_sha IS NULL OR (length(source_base_sha)=40 AND source_base_sha NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 resource_key TEXT COLLATE BINARY NOT NULL, created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
 assignment_epoch INTEGER NOT NULL DEFAULT 1 CHECK(assignment_epoch > 0),
 cutover_import_ref TEXT COLLATE BINARY UNIQUE
) STRICT;
CREATE TABLE job_dependencies (
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 prerequisite_job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 PRIMARY KEY(job_id, prerequisite_job_id), CHECK(job_id <> prerequisite_job_id)
) STRICT;
CREATE TABLE decisions (
 decision_id TEXT COLLATE BINARY PRIMARY KEY, authority_principal_id TEXT COLLATE BINARY NOT NULL REFERENCES principals(principal_id),
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 approved_scope_hash TEXT COLLATE BINARY CHECK(approved_scope_hash IS NULL OR (length(approved_scope_hash)=64 AND approved_scope_hash NOT GLOB '*[^0-9a-f]*')) NOT NULL, action TEXT COLLATE BINARY NOT NULL,
 proof_ref TEXT COLLATE BINARY NOT NULL, authority_revision TEXT COLLATE BINARY NOT NULL,
 created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), expires_at INTEGER,
 revoked_at INTEGER
) STRICT;
CREATE TABLE grants (
 grant_id TEXT COLLATE BINARY PRIMARY KEY, principal_id TEXT COLLATE BINARY NOT NULL REFERENCES principals(principal_id),
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 worker_id TEXT COLLATE BINARY NOT NULL, repo_id TEXT COLLATE BINARY NOT NULL, path_scope TEXT NOT NULL CHECK(json_valid(path_scope) AND json_type(path_scope)='array'),
 operation TEXT COLLATE BINARY NOT NULL, policy_version TEXT COLLATE BINARY NOT NULL,
 authority_revision TEXT COLLATE BINARY NOT NULL, approved_scope_hash TEXT COLLATE BINARY CHECK(approved_scope_hash IS NULL OR (length(approved_scope_hash)=64 AND approved_scope_hash NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 source_base_sha TEXT COLLATE BINARY CHECK(source_base_sha IS NULL OR (length(source_base_sha)=40 AND source_base_sha NOT GLOB '*[^0-9a-f]*')) NOT NULL, assignment_epoch INTEGER NOT NULL CHECK(assignment_epoch > 0),
 expires_at INTEGER NOT NULL, issuer_approval_ref TEXT COLLATE BINARY NOT NULL REFERENCES decisions(decision_id),
 revoked_at INTEGER
) STRICT;
CREATE TABLE worker_assignments (
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id), worker_id TEXT COLLATE BINARY NOT NULL,
 assignment_epoch INTEGER NOT NULL CHECK(assignment_epoch > 0), status TEXT COLLATE BINARY NOT NULL CHECK(status IN ('ACTIVE','REVOKED','CLOSED')),
 valid_from INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), revoked_at INTEGER,
 PRIMARY KEY(job_id,worker_id,assignment_epoch)
) STRICT;
CREATE UNIQUE INDEX one_active_assignment_per_job ON worker_assignments(job_id) WHERE status='ACTIVE';
CREATE TABLE attempts (
 attempt_id TEXT COLLATE BINARY PRIMARY KEY, job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 worker_id TEXT COLLATE BINARY NOT NULL, principal_id TEXT COLLATE BINARY NOT NULL REFERENCES principals(principal_id), assignment_epoch INTEGER NOT NULL,
 started_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), ended_at INTEGER,
 status TEXT COLLATE BINARY NOT NULL CHECK(status IN ('STARTED','HEARTBEAT','CHECKPOINTED','YIELDED','SUBMITTED','STOPPED')),
 checkpoint_sha TEXT COLLATE BINARY CHECK(checkpoint_sha IS NULL OR (length(checkpoint_sha)=64 AND checkpoint_sha NOT GLOB '*[^0-9a-f]*')), artifact_ref TEXT COLLATE BINARY, elapsed_observed_seconds INTEGER NOT NULL DEFAULT 0,
 resume_required INTEGER NOT NULL DEFAULT 0 CHECK(resume_required IN (0,1)), source_base_sha TEXT COLLATE BINARY CHECK(source_base_sha IS NULL OR (length(source_base_sha)=40 AND source_base_sha NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 lease_generation INTEGER, UNIQUE(job_id,attempt_id),
 FOREIGN KEY(job_id,worker_id,assignment_epoch) REFERENCES worker_assignments(job_id,worker_id,assignment_epoch)
) STRICT;
CREATE TABLE resource_leases (
 repo_id TEXT COLLATE BINARY NOT NULL, resource_key TEXT COLLATE BINARY NOT NULL,
 lease_generation INTEGER NOT NULL CHECK(lease_generation > 0), holder_attempt_id TEXT COLLATE BINARY REFERENCES attempts(attempt_id),
 expires_at INTEGER NOT NULL, baseline_sha TEXT COLLATE BINARY CHECK(baseline_sha IS NULL OR (length(baseline_sha)=40 AND baseline_sha NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 PRIMARY KEY(repo_id,resource_key)
) STRICT;
CREATE TABLE event_journal (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT COLLATE BINARY NOT NULL UNIQUE, producer TEXT COLLATE BINARY NOT NULL, transport_delivery_id TEXT COLLATE BINARY NOT NULL,
 partition_key TEXT COLLATE BINARY NOT NULL, payload_sha256 TEXT COLLATE BINARY CHECK(payload_sha256 IS NULL OR (length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 canonical_event_json TEXT CHECK(json_valid(canonical_event_json)) NOT NULL, received_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000),
 immutable_evidence_ref TEXT COLLATE BINARY, legacy_event_id TEXT COLLATE BINARY,
 UNIQUE(producer,transport_delivery_id)
) STRICT;
CREATE TABLE delivery_effects (
 event_id TEXT COLLATE BINARY NOT NULL REFERENCES event_journal(event_id),
 consumer_id TEXT COLLATE BINARY NOT NULL, assignment_epoch INTEGER NOT NULL, effect_name TEXT COLLATE BINARY NOT NULL,
 recorded_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000),
 PRIMARY KEY(event_id,consumer_id,assignment_epoch,effect_name)
) STRICT;
CREATE TABLE inbox_cursors (
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id), worker_id TEXT COLLATE BINARY NOT NULL,
 assignment_epoch INTEGER NOT NULL, last_event_id TEXT COLLATE BINARY NOT NULL,
 updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000),
 PRIMARY KEY(job_id,worker_id,assignment_epoch)
) STRICT;
CREATE TABLE outbox (
 outbox_id TEXT COLLATE BINARY PRIMARY KEY, event_id TEXT COLLATE BINARY NOT NULL REFERENCES event_journal(event_id),
 kind TEXT COLLATE BINARY NOT NULL CHECK(kind IN ('NOTICE','REPORT','GITHUB_EFFECT','DRIVE_EFFECT')),
 destination TEXT COLLATE BINARY NOT NULL, status TEXT COLLATE BINARY NOT NULL CHECK(status IN ('PREPARED','IN_FLIGHT','RECONCILE_REQUIRED','DELIVERED','RETRY','DEAD_LETTER','BLOCKED')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 next_retry_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000),
 provider_ref TEXT COLLATE BINARY, payload_sha256 TEXT COLLATE BINARY CHECK(payload_sha256 IS NULL OR (length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL, provider_effect_sha256 TEXT COLLATE BINARY CHECK(provider_effect_sha256 IS NULL OR (length(provider_effect_sha256)=64 AND provider_effect_sha256 NOT GLOB '*[^0-9a-f]*')),
 dead_letter_reason TEXT COLLATE BINARY, claim_token TEXT COLLATE BINARY, claim_expires_at INTEGER,
 created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), UNIQUE(event_id,destination,kind)
) STRICT;
CREATE INDEX due_outbox_idx ON outbox(next_retry_at,created_at) WHERE status IN ('PREPARED','RETRY');
-- Global publication identities/idempotency across source and Drive provider tables.
CREATE TABLE publication_keys (
 publication_id TEXT COLLATE BINARY PRIMARY KEY, idempotency_key TEXT COLLATE BINARY CHECK(idempotency_key IS NULL OR (length(idempotency_key)=64 AND idempotency_key NOT GLOB '*[^0-9a-f]*')) NOT NULL UNIQUE,
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id), artifact_sha256 TEXT COLLATE BINARY CHECK(artifact_sha256 IS NULL OR (length(artifact_sha256)=64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 UNIQUE(job_id,artifact_sha256)
) STRICT;
CREATE TABLE source_publications (
 publication_id TEXT COLLATE BINARY PRIMARY KEY, job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 attempt_id TEXT COLLATE BINARY NOT NULL REFERENCES attempts(attempt_id),
 repo_id TEXT COLLATE BINARY NOT NULL, work_branch TEXT COLLATE BINARY NOT NULL, baseline_sha TEXT COLLATE BINARY CHECK(baseline_sha IS NULL OR (length(baseline_sha)=40 AND baseline_sha NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 proposed_tree_hash TEXT COLLATE BINARY CHECK(proposed_tree_hash IS NULL OR (length(proposed_tree_hash)=64 AND proposed_tree_hash NOT GLOB '*[^0-9a-f]*')) NOT NULL, idempotency_key TEXT COLLATE BINARY CHECK(idempotency_key IS NULL OR (length(idempotency_key)=64 AND idempotency_key NOT GLOB '*[^0-9a-f]*')) NOT NULL UNIQUE,
 provider_request_id TEXT COLLATE BINARY, result_commit_sha TEXT COLLATE BINARY CHECK(result_commit_sha IS NULL OR (length(result_commit_sha)=40 AND result_commit_sha NOT GLOB '*[^0-9a-f]*')), observed_sha256 TEXT COLLATE BINARY CHECK(observed_sha256 IS NULL OR (length(observed_sha256)=64 AND observed_sha256 NOT GLOB '*[^0-9a-f]*')),
 status TEXT COLLATE BINARY NOT NULL CHECK(status IN ('PREPARED','EXTERNAL_OBSERVED','VERIFIED','CONFLICT','BLOCKED')),
 verified_at INTEGER, UNIQUE(job_id,proposed_tree_hash)
) STRICT;
CREATE TABLE document_publications (
 publication_id TEXT COLLATE BINARY PRIMARY KEY, job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 attempt_id TEXT COLLATE BINARY NOT NULL REFERENCES attempts(attempt_id),
 drive_file_id TEXT COLLATE BINARY NOT NULL, expected_revision TEXT COLLATE BINARY, exported_sha256 TEXT COLLATE BINARY CHECK(exported_sha256 IS NULL OR (length(exported_sha256)=64 AND exported_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 idempotency_key TEXT COLLATE BINARY CHECK(idempotency_key IS NULL OR (length(idempotency_key)=64 AND idempotency_key NOT GLOB '*[^0-9a-f]*')) NOT NULL UNIQUE, resulting_revision TEXT COLLATE BINARY, observed_sha256 TEXT COLLATE BINARY CHECK(observed_sha256 IS NULL OR (length(observed_sha256)=64 AND observed_sha256 NOT GLOB '*[^0-9a-f]*')),
 provider_request_id TEXT COLLATE BINARY, status TEXT COLLATE BINARY NOT NULL CHECK(status IN ('PREPARED','EXTERNAL_OBSERVED','VERIFIED','CONFLICT','BLOCKED')),
 verified_at INTEGER, UNIQUE(job_id,exported_sha256)
) STRICT;
CREATE TABLE reviews (
 review_id TEXT COLLATE BINARY PRIMARY KEY, subject_publication_id TEXT COLLATE BINARY NOT NULL,
 reviewer_principal_id TEXT COLLATE BINARY NOT NULL REFERENCES principals(principal_id),
 subject_sha TEXT COLLATE BINARY CHECK(subject_sha IS NULL OR (length(subject_sha)=64 AND subject_sha NOT GLOB '*[^0-9a-f]*')) NOT NULL, verdict TEXT COLLATE BINARY NOT NULL CHECK(verdict IN ('PASS','REWORK')),
 evidence_ref TEXT COLLATE BINARY NOT NULL, created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000)
) STRICT;
CREATE TABLE audit_anchors (
 anchor_id TEXT COLLATE BINARY PRIMARY KEY, partition_id TEXT COLLATE BINARY NOT NULL, sequence_from INTEGER NOT NULL,
 sequence_to INTEGER NOT NULL, batch_hash TEXT COLLATE BINARY CHECK(batch_hash IS NULL OR (length(batch_hash)=64 AND batch_hash NOT GLOB '*[^0-9a-f]*')) NOT NULL, external_ref TEXT COLLATE BINARY NOT NULL,
 created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), CHECK(sequence_to >= sequence_from), UNIQUE(partition_id,sequence_from,sequence_to)
) STRICT;
CREATE TABLE budget_reservations (
 reservation_id TEXT COLLATE BINARY PRIMARY KEY, job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 amount INTEGER NOT NULL DEFAULT 0 CHECK(amount >= 0 AND amount <= 999999999999999999),
 currency TEXT COLLATE BINARY NOT NULL DEFAULT 'USD', status TEXT COLLATE BINARY NOT NULL CHECK(status IN ('APPROVED','REVOKED','SPENT')),
 approved_decision_id TEXT COLLATE BINARY REFERENCES decisions(decision_id)
) STRICT;
CREATE TABLE recovery_checkpoints (
 checkpoint_id TEXT COLLATE BINARY PRIMARY KEY, job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 attempt_id TEXT COLLATE BINARY NOT NULL REFERENCES attempts(attempt_id),
 source_ref TEXT COLLATE BINARY NOT NULL, snapshot_hash TEXT COLLATE BINARY CHECK(snapshot_hash IS NULL OR (length(snapshot_hash)=64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*')) NOT NULL, created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000),
 UNIQUE(attempt_id,snapshot_hash)
) STRICT;
CREATE TABLE command_results (
 commit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 command_id TEXT COLLATE BINARY NOT NULL UNIQUE, payload_sha256 TEXT COLLATE BINARY CHECK(payload_sha256 IS NULL OR (length(payload_sha256)=64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL, result_json TEXT CHECK(json_valid(result_json)) NOT NULL,
 created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000)
) STRICT;
CREATE TABLE resume_capsules (
 job_id TEXT COLLATE BINARY NOT NULL REFERENCES jobs(job_id),
 attempt_id TEXT COLLATE BINARY NOT NULL REFERENCES attempts(attempt_id),
 checkpoint_sha256 TEXT COLLATE BINARY CHECK(checkpoint_sha256 IS NULL OR (length(checkpoint_sha256)=64 AND checkpoint_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL, capsule_json TEXT CHECK(json_valid(capsule_json)) NOT NULL,
 captured_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000),
 PRIMARY KEY(job_id,attempt_id,checkpoint_sha256)
) STRICT;
CREATE TABLE shadow_imports (
 import_id TEXT COLLATE BINARY PRIMARY KEY, source_kind TEXT COLLATE BINARY NOT NULL CHECK(source_kind IN ('GITHUB','DRIVE','LEDGER')),
 source_ref TEXT COLLATE BINARY NOT NULL, source_revision TEXT COLLATE BINARY NOT NULL, content_sha256 TEXT COLLATE BINARY CHECK(content_sha256 IS NULL OR (length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*')) NOT NULL,
 source_json TEXT CHECK(json_valid(source_json)) NOT NULL, reconciliation_status TEXT COLLATE BINARY NOT NULL DEFAULT 'UNVERIFIED',
 imported_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)*1000000), UNIQUE(source_kind,source_ref,source_revision)
) STRICT;
CREATE TABLE authority_mode (
 singleton INTEGER PRIMARY KEY DEFAULT 1 CHECK(singleton=1),
 mode TEXT COLLATE BINARY NOT NULL DEFAULT 'SHADOW' CHECK(mode IN ('SHADOW','ACTIVE')),
 cutover_watermark TEXT COLLATE BINARY, owner_proof_ref TEXT COLLATE BINARY, signed_revision TEXT COLLATE BINARY,
 CHECK((mode='SHADOW') OR (cutover_watermark IS NOT NULL AND owner_proof_ref IS NOT NULL AND signed_revision IS NOT NULL))
) STRICT;
INSERT INTO authority_mode(singleton,mode) VALUES(1,'SHADOW');


CREATE INDEX journal_partition_sequence_idx ON event_journal(partition_key,sequence);
CREATE TRIGGER immutable_event_journal_update BEFORE UPDATE ON event_journal BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_event_journal_delete BEFORE DELETE ON event_journal BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_delivery_effects_update BEFORE UPDATE ON delivery_effects BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_delivery_effects_delete BEFORE DELETE ON delivery_effects BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_reviews_update BEFORE UPDATE ON reviews BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_reviews_delete BEFORE DELETE ON reviews BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_audit_anchors_update BEFORE UPDATE ON audit_anchors BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_audit_anchors_delete BEFORE DELETE ON audit_anchors BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_recovery_checkpoints_update BEFORE UPDATE ON recovery_checkpoints BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_recovery_checkpoints_delete BEFORE DELETE ON recovery_checkpoints BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_resume_capsules_update BEFORE UPDATE ON resume_capsules BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_resume_capsules_delete BEFORE DELETE ON resume_capsules BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_schema_migrations_update BEFORE UPDATE ON schema_migrations BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_schema_migrations_delete BEFORE DELETE ON schema_migrations BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_publication_keys_update BEFORE UPDATE ON publication_keys BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_publication_keys_delete BEFORE DELETE ON publication_keys BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_command_result_update BEFORE UPDATE ON command_results
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_COMMAND_RESULT'); END;
CREATE TRIGGER immutable_command_result_delete BEFORE DELETE ON command_results
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_COMMAND_RESULT'); END;
CREATE TRIGGER retain_authority_mode BEFORE DELETE ON authority_mode
BEGIN SELECT RAISE(ABORT,'AUTHORITY_MODE_REQUIRED'); END;
CREATE TRIGGER lease_generation_monotonic BEFORE UPDATE ON resource_leases
WHEN NEW.lease_generation < OLD.lease_generation
BEGIN SELECT RAISE(ABORT,'STALE_LEASE'); END;
CREATE TRIGGER job_epoch_monotonic BEFORE UPDATE ON jobs
WHEN NEW.assignment_epoch < OLD.assignment_epoch OR NEW.version < OLD.version
BEGIN SELECT RAISE(ABORT,'STALE_ASSIGNMENT'); END;
