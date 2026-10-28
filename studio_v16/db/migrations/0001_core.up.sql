-- PostgreSQL 15+; migration role must own the schema. Runtime role must not own tables.
CREATE SCHEMA IF NOT EXISTS nomad_v16;
CREATE TABLE nomad_v16.schema_migrations (
 version text PRIMARY KEY, checksum char(64) NOT NULL, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE nomad_v16.principals (
 principal_id text PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL,
 kind text NOT NULL, status text NOT NULL CHECK (status IN ('ACTIVE','REVOKED')),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(issuer, subject)
);
CREATE TABLE nomad_v16.jobs (
 job_id text PRIMARY KEY, parent_issue_ref text NOT NULL UNIQUE, owner_head_id text NOT NULL,
 state text NOT NULL CHECK (state IN ('APPROVED','READY','CLAIMED','YIELDED','SUBMITTED','DONE','BLOCKED','CANCELLED')),
 approved_scope_hash char(64) NOT NULL, authority_revision text NOT NULL,
 source_repo text NOT NULL, source_base_sha char(40) NOT NULL,
 resource_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
 assignment_epoch bigint NOT NULL DEFAULT 1 CHECK(assignment_epoch > 0),
 cutover_import_ref text UNIQUE
);
CREATE TABLE nomad_v16.job_dependencies (
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 prerequisite_job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 PRIMARY KEY(job_id, prerequisite_job_id), CHECK(job_id <> prerequisite_job_id)
);
CREATE TABLE nomad_v16.decisions (
 decision_id text PRIMARY KEY, authority_principal_id text NOT NULL REFERENCES nomad_v16.principals(principal_id),
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 approved_scope_hash char(64) NOT NULL, action text NOT NULL,
 proof_ref text NOT NULL, authority_revision text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
 revoked_at timestamptz
);
CREATE TABLE nomad_v16.grants (
 grant_id text PRIMARY KEY, principal_id text NOT NULL REFERENCES nomad_v16.principals(principal_id),
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 worker_id text NOT NULL, repo_id text NOT NULL, path_scope text[] NOT NULL,
 operation text NOT NULL, policy_version text NOT NULL,
 authority_revision text NOT NULL, approved_scope_hash char(64) NOT NULL,
 source_base_sha char(40) NOT NULL, assignment_epoch bigint NOT NULL CHECK(assignment_epoch > 0),
 expires_at timestamptz NOT NULL, issuer_approval_ref text NOT NULL REFERENCES nomad_v16.decisions(decision_id),
 revoked_at timestamptz
);
CREATE TABLE nomad_v16.worker_assignments (
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id), worker_id text NOT NULL,
 assignment_epoch bigint NOT NULL CHECK(assignment_epoch > 0), status text NOT NULL CHECK(status IN ('ACTIVE','REVOKED','CLOSED')),
 valid_from timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
 PRIMARY KEY(job_id,worker_id,assignment_epoch)
);
CREATE UNIQUE INDEX one_active_assignment_per_job ON nomad_v16.worker_assignments(job_id) WHERE status='ACTIVE';
CREATE TABLE nomad_v16.attempts (
 attempt_id text PRIMARY KEY, job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 worker_id text NOT NULL, principal_id text NOT NULL REFERENCES nomad_v16.principals(principal_id), assignment_epoch bigint NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz,
 status text NOT NULL CHECK(status IN ('STARTED','HEARTBEAT','CHECKPOINTED','YIELDED','SUBMITTED','STOPPED')),
 checkpoint_sha char(64), artifact_ref text, elapsed_observed_seconds bigint NOT NULL DEFAULT 0,
 resume_required boolean NOT NULL DEFAULT false, source_base_sha char(40) NOT NULL,
 lease_generation bigint, UNIQUE(job_id,attempt_id),
 FOREIGN KEY(job_id,worker_id,assignment_epoch) REFERENCES nomad_v16.worker_assignments(job_id,worker_id,assignment_epoch)
);
CREATE TABLE nomad_v16.resource_leases (
 repo_id text NOT NULL, resource_key text NOT NULL,
 lease_generation bigint NOT NULL CHECK(lease_generation > 0), holder_attempt_id text REFERENCES nomad_v16.attempts(attempt_id),
 expires_at timestamptz NOT NULL, baseline_sha char(40) NOT NULL,
 PRIMARY KEY(repo_id,resource_key)
);
CREATE TABLE nomad_v16.event_journal (
 event_id text PRIMARY KEY, producer text NOT NULL, transport_delivery_id text NOT NULL,
 partition_key text NOT NULL, payload_sha256 char(64) NOT NULL,
 canonical_event_json jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
 immutable_evidence_ref text, legacy_event_id text,
 UNIQUE(producer,transport_delivery_id)
);
CREATE TABLE nomad_v16.delivery_effects (
 event_id text NOT NULL REFERENCES nomad_v16.event_journal(event_id),
 consumer_id text NOT NULL, assignment_epoch bigint NOT NULL, effect_name text NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(event_id,consumer_id,assignment_epoch,effect_name)
);
CREATE TABLE nomad_v16.inbox_cursors (
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id), worker_id text NOT NULL,
 assignment_epoch bigint NOT NULL, last_event_id text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(job_id,worker_id,assignment_epoch)
);
CREATE TABLE nomad_v16.outbox (
 outbox_id text PRIMARY KEY, event_id text NOT NULL REFERENCES nomad_v16.event_journal(event_id),
 kind text NOT NULL CHECK(kind IN ('NOTICE','REPORT','GITHUB_EFFECT','DRIVE_EFFECT')),
 destination text NOT NULL, status text NOT NULL CHECK(status IN ('PREPARED','IN_FLIGHT','RECONCILE_REQUIRED','DELIVERED','RETRY','DEAD_LETTER','BLOCKED')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 next_retry_at timestamptz NOT NULL DEFAULT now(),
 provider_ref text, payload_sha256 char(64) NOT NULL, provider_effect_sha256 char(64),
 dead_letter_reason text, claim_token text, claim_expires_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(event_id,destination,kind)
);
CREATE INDEX due_outbox_idx ON nomad_v16.outbox(next_retry_at,created_at) WHERE status IN ('PREPARED','RETRY');
-- Global publication identities/idempotency across source and Drive provider tables.
CREATE TABLE nomad_v16.publication_keys (
 publication_id text PRIMARY KEY, idempotency_key char(64) NOT NULL UNIQUE,
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id), artifact_sha256 char(64) NOT NULL,
 UNIQUE(job_id,artifact_sha256)
);
CREATE TABLE nomad_v16.source_publications (
 publication_id text PRIMARY KEY, job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 attempt_id text NOT NULL REFERENCES nomad_v16.attempts(attempt_id),
 repo_id text NOT NULL, work_branch text NOT NULL, baseline_sha char(40) NOT NULL,
 proposed_tree_hash char(64) NOT NULL, idempotency_key char(64) NOT NULL UNIQUE,
 provider_request_id text, result_commit_sha char(40), observed_sha256 char(64),
 status text NOT NULL CHECK(status IN ('PREPARED','EXTERNAL_OBSERVED','VERIFIED','CONFLICT','BLOCKED')),
 verified_at timestamptz, UNIQUE(job_id,proposed_tree_hash)
);
CREATE TABLE nomad_v16.document_publications (
 publication_id text PRIMARY KEY, job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 attempt_id text NOT NULL REFERENCES nomad_v16.attempts(attempt_id),
 drive_file_id text NOT NULL, expected_revision text, exported_sha256 char(64) NOT NULL,
 idempotency_key char(64) NOT NULL UNIQUE, resulting_revision text, observed_sha256 char(64),
 provider_request_id text, status text NOT NULL CHECK(status IN ('PREPARED','EXTERNAL_OBSERVED','VERIFIED','CONFLICT','BLOCKED')),
 verified_at timestamptz, UNIQUE(job_id,exported_sha256)
);
CREATE TABLE nomad_v16.reviews (
 review_id text PRIMARY KEY, subject_publication_id text NOT NULL,
 reviewer_principal_id text NOT NULL REFERENCES nomad_v16.principals(principal_id),
 subject_sha char(64) NOT NULL, verdict text NOT NULL CHECK(verdict IN ('PASS','REWORK')),
 evidence_ref text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE nomad_v16.audit_anchors (
 anchor_id text PRIMARY KEY, partition_id text NOT NULL, sequence_from bigint NOT NULL,
 sequence_to bigint NOT NULL, batch_hash char(64) NOT NULL, external_ref text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), CHECK(sequence_to >= sequence_from), UNIQUE(partition_id,sequence_from,sequence_to)
);
CREATE TABLE nomad_v16.budget_reservations (
 reservation_id text PRIMARY KEY, job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 amount numeric(18,4) NOT NULL DEFAULT 0 CHECK(amount >= 0),
 currency text NOT NULL DEFAULT 'USD', status text NOT NULL CHECK(status IN ('APPROVED','REVOKED','SPENT')),
 approved_decision_id text REFERENCES nomad_v16.decisions(decision_id)
);
CREATE TABLE nomad_v16.recovery_checkpoints (
 checkpoint_id text PRIMARY KEY, job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 attempt_id text NOT NULL REFERENCES nomad_v16.attempts(attempt_id),
 source_ref text NOT NULL, snapshot_hash char(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(attempt_id,snapshot_hash)
);
CREATE TABLE nomad_v16.command_results (
 commit_sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 command_id text NOT NULL UNIQUE, payload_sha256 char(64) NOT NULL, result_json jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE nomad_v16.resume_capsules (
 job_id text NOT NULL REFERENCES nomad_v16.jobs(job_id),
 attempt_id text NOT NULL REFERENCES nomad_v16.attempts(attempt_id),
 checkpoint_sha256 char(64) NOT NULL, capsule_json jsonb NOT NULL,
 captured_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(job_id,attempt_id,checkpoint_sha256)
);
CREATE TABLE nomad_v16.shadow_imports (
 import_id text PRIMARY KEY, source_kind text NOT NULL CHECK(source_kind IN ('GITHUB','DRIVE','LEDGER')),
 source_ref text NOT NULL, source_revision text NOT NULL, content_sha256 char(64) NOT NULL,
 source_json jsonb NOT NULL, reconciliation_status text NOT NULL DEFAULT 'UNVERIFIED',
 imported_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source_kind,source_ref,source_revision)
);
CREATE TABLE nomad_v16.authority_mode (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 mode text NOT NULL DEFAULT 'SHADOW' CHECK(mode IN ('SHADOW','ACTIVE')),
 cutover_watermark text, owner_proof_ref text, signed_revision text,
 CHECK((mode='SHADOW') OR (cutover_watermark IS NOT NULL AND owner_proof_ref IS NOT NULL AND signed_revision IS NOT NULL))
);
INSERT INTO nomad_v16.authority_mode(singleton,mode) VALUES(true,'SHADOW');

-- Immutable evidence is append-only even if application code is buggy.
CREATE FUNCTION nomad_v16.reject_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'immutable V1.6 evidence cannot be modified: %', TG_TABLE_NAME;
END;
$$;
CREATE TRIGGER immutable_events BEFORE UPDATE OR DELETE ON nomad_v16.event_journal
  FOR EACH ROW EXECUTE FUNCTION nomad_v16.reject_evidence_mutation();
CREATE TRIGGER immutable_delivery_effects BEFORE UPDATE OR DELETE ON nomad_v16.delivery_effects
  FOR EACH ROW EXECUTE FUNCTION nomad_v16.reject_evidence_mutation();
CREATE TRIGGER immutable_reviews BEFORE UPDATE OR DELETE ON nomad_v16.reviews
  FOR EACH ROW EXECUTE FUNCTION nomad_v16.reject_evidence_mutation();
CREATE TRIGGER immutable_audit_anchors BEFORE UPDATE OR DELETE ON nomad_v16.audit_anchors
  FOR EACH ROW EXECUTE FUNCTION nomad_v16.reject_evidence_mutation();
CREATE TRIGGER immutable_checkpoints BEFORE UPDATE OR DELETE ON nomad_v16.recovery_checkpoints
  FOR EACH ROW EXECUTE FUNCTION nomad_v16.reject_evidence_mutation();
