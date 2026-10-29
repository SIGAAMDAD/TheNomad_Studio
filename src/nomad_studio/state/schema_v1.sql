-- The Nomad Studio V1 -- FIRST INSTALL ONLY.
-- Single authoritative schema. No pre-V1 history or upgrades.

-- Studio V1 initial schema: state
-- Core WG-A state. Other modules supply their own deterministic migrations.
CREATE TABLE IF NOT EXISTS state_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_fingerprint TEXT NOT NULL,
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor_principal_id TEXT NOT NULL,
    issued_at_us INTEGER NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    job_id TEXT,
    shift_id TEXT,
    assignment_epoch INTEGER,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_evidence_ref TEXT
);
CREATE INDEX state_events_domain_seq ON state_events(domain, sequence);
CREATE INDEX state_events_job_seq ON state_events(job_id, sequence);
CREATE INDEX state_events_correlation_seq ON state_events(correlation_id, sequence);
CREATE TABLE IF NOT EXISTS state_idempotency (
    key TEXT PRIMARY KEY,
    fingerprint TEXT,
    result_json TEXT NOT NULL,
    created_at_us INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS state_receipts (
    receipt_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    request_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES state_events(event_id),
    result_json TEXT NOT NULL,
    created_at_us INTEGER NOT NULL,
    UNIQUE(namespace, request_id)
);
CREATE INDEX state_receipts_namespace ON state_receipts(namespace, created_at_us);
CREATE TABLE IF NOT EXISTS state_records (
    namespace TEXT NOT NULL,
    record_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    updated_at_us INTEGER NOT NULL,
    PRIMARY KEY(namespace, record_key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS state_actor_epochs (
    principal_id TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL CHECK(epoch >= 0),
    changed_at_us INTEGER NOT NULL
);

-- Studio V1 initial schema: archive
-- WG-L archive-only index. Applied by WG-A; never opened directly by WG-L.
CREATE TABLE IF NOT EXISTS archive_jobs (
    archive_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    approved_ref TEXT NOT NULL,
    created_by TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('OPEN','SNAPSHOTTED','COMPRESSED','SEALED_UNWITNESSED','WITNESSED')),
    snapshot_sha256 TEXT,
    snapshot_size INTEGER,
    manifest_sha256 TEXT,
    archive_sha256 TEXT,
    stored_sha256 TEXT,
    record_count INTEGER,
    encrypted INTEGER NOT NULL DEFAULT 0,
    witness_ref TEXT,
    witness_by TEXT,
    CHECK((state IN ('OPEN','SNAPSHOTTED','COMPRESSED')) OR (manifest_sha256 IS NOT NULL AND archive_sha256 IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS archive_idempotency (
    key TEXT PRIMARY KEY,
    command_sha256 TEXT NOT NULL,
    response_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS archive_jobs_state ON archive_jobs(state, archive_id);

-- Studio V1 initial schema: artifacts
CREATE TABLE IF NOT EXISTS artifacts_manifests (
    artifact_id TEXT PRIMARY KEY NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('REGISTERED','VERIFIED','VALIDATED')),
    producer_principal TEXT NOT NULL,
    byte_proof_sha256 TEXT,
    validator TEXT,
    evidence_json TEXT,
    created_us INTEGER NOT NULL,
    verified_us INTEGER,
    validated_us INTEGER
);
CREATE TABLE IF NOT EXISTS artifacts_requests (
    idempotency_key TEXT PRIMARY KEY NOT NULL,
    operation TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    outcome_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts_attachments (
    attachment_id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts_manifests(artifact_id),
    job_id TEXT NOT NULL,
    actor_principal TEXT NOT NULL,
    subject_sha256 TEXT NOT NULL,
    qa_intent_json TEXT NOT NULL,
    publication_intent_json TEXT NOT NULL,
    qa_receipt_id TEXT,
    publication_receipt_id TEXT,
    created_us INTEGER NOT NULL,
    UNIQUE(artifact_id, job_id)
);
CREATE INDEX IF NOT EXISTS artifacts_by_state ON artifacts_manifests(state);
CREATE INDEX IF NOT EXISTS artifacts_attachment_by_job ON artifacts_attachments(job_id);

-- Studio V1 initial schema: budget
-- MODULE budget / migration 001. Applied solely by the central WG-A migration loader.
CREATE TABLE IF NOT EXISTS budget_grants (
 grant_id TEXT PRIMARY KEY,
 revision INTEGER NOT NULL CHECK(revision >= 1),
 owner_principal TEXT NOT NULL,
 feature_id TEXT NOT NULL,
 department_id TEXT NOT NULL,
 worker_slot_id TEXT NOT NULL,
 model_effort TEXT NOT NULL,
 resource_class TEXT NOT NULL,
 provider TEXT NOT NULL,
 unit_limit INTEGER NOT NULL CHECK(unit_limit >= 0),
 cost_limit_minor INTEGER NOT NULL CHECK(cost_limit_minor >= 0),
 currency TEXT NOT NULL,
 reserved_units INTEGER NOT NULL DEFAULT 0 CHECK(reserved_units >= 0),
 used_units INTEGER NOT NULL DEFAULT 0 CHECK(used_units >= 0),
 reserved_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK(reserved_cost_minor >= 0),
 measured_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK(measured_cost_minor >= 0),
 valid_from_us INTEGER NOT NULL,
 expires_at_us INTEGER NOT NULL,
 revoked_at_us INTEGER,
 created_at_us INTEGER NOT NULL,
 updated_at_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budget_grants_scope ON budget_grants(feature_id,department_id,worker_slot_id,model_effort,resource_class,provider);
CREATE TABLE IF NOT EXISTS budget_reservations (
 reservation_id TEXT PRIMARY KEY,
 grant_id TEXT NOT NULL REFERENCES budget_grants(grant_id),
 request_key TEXT NOT NULL,
 job_id TEXT NOT NULL,
 units INTEGER NOT NULL CHECK(units > 0),
 cost_ceiling_minor INTEGER NOT NULL CHECK(cost_ceiling_minor >= 0),
 status TEXT NOT NULL CHECK(status IN ('RESERVED','PENDING_EVIDENCE','SETTLED','RELEASED')),
 used_units INTEGER NOT NULL DEFAULT 0 CHECK(used_units >= 0),
 created_at_us INTEGER NOT NULL,
 expires_at_us INTEGER NOT NULL,
 finalized_at_us INTEGER,
 provider TEXT NOT NULL,
 attempt INTEGER NOT NULL CHECK(attempt >= 0),
 UNIQUE(grant_id, request_key, attempt)
);
CREATE INDEX IF NOT EXISTS idx_budget_reservations_grant ON budget_reservations(grant_id,status);
CREATE TABLE IF NOT EXISTS budget_provider_quotas (
 provider TEXT PRIMARY KEY CHECK(provider IN ('github','drive')),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 window_us INTEGER NOT NULL CHECK(window_us > 0),
 max_requests INTEGER NOT NULL CHECK(max_requests >= 0),
 max_retries INTEGER NOT NULL CHECK(max_retries >= 0),
 cooldown_until_us INTEGER NOT NULL DEFAULT 0,
 updated_at_us INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_provider_attempts (
 provider TEXT NOT NULL,
 request_key TEXT NOT NULL,
 attempt INTEGER NOT NULL CHECK(attempt >= 0),
 reservation_id TEXT NOT NULL UNIQUE REFERENCES budget_reservations(reservation_id),
 created_at_us INTEGER NOT NULL,
 PRIMARY KEY(provider,request_key,attempt)
);
CREATE INDEX IF NOT EXISTS idx_budget_provider_attempts_time ON budget_provider_attempts(provider,created_at_us);
CREATE TABLE IF NOT EXISTS budget_evidence (
 evidence_id TEXT PRIMARY KEY,
 grant_id TEXT NOT NULL REFERENCES budget_grants(grant_id),
 reservation_id TEXT REFERENCES budget_reservations(reservation_id),
 kind TEXT NOT NULL CHECK(kind IN ('PROVIDER_CHARGE','PROVIDER_RATE','ESTIMATE','MANUAL_WEB')),
 status TEXT NOT NULL CHECK(status IN ('MEASURED','RATE_OBSERVED','ESTIMATED','NOT_MEASURED')),
 provider TEXT NOT NULL,
 observed_at_us INTEGER NOT NULL,
 recorded_at_us INTEGER NOT NULL,
 source_ref TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,
 actual_cost_minor INTEGER,
 estimate_cost_minor INTEGER,
 currency TEXT NOT NULL,
 requests_observed INTEGER,
 remaining_requests INTEGER,
 reset_at_us INTEGER,
 CHECK(actual_cost_minor IS NULL OR actual_cost_minor >= 0),
 CHECK(estimate_cost_minor IS NULL OR estimate_cost_minor >= 0)
);
CREATE INDEX IF NOT EXISTS idx_budget_evidence_grant ON budget_evidence(grant_id,recorded_at_us);
CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_unique_provider_receipt ON budget_evidence(kind,source_ref) WHERE kind IN ('PROVIDER_CHARGE','PROVIDER_RATE');
CREATE TABLE IF NOT EXISTS budget_exceptions (
 exception_id TEXT PRIMARY KEY,
 grant_id TEXT,
 code TEXT NOT NULL,
 details TEXT NOT NULL,
 created_at_us INTEGER NOT NULL,
 request_id TEXT NOT NULL,
 UNIQUE(request_id,code)
);
CREATE INDEX IF NOT EXISTS idx_budget_exceptions_time ON budget_exceptions(created_at_us);
CREATE TABLE IF NOT EXISTS budget_effects (
 idempotency_key TEXT PRIMARY KEY,
 principal_id TEXT NOT NULL,
 operation TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 state TEXT NOT NULL,
 code TEXT NOT NULL,
 receipt_id TEXT,
 response_json TEXT NOT NULL,
 created_at_us INTEGER NOT NULL
);

-- Studio V1 initial schema: console
-- WG-O's only durable data: owner control requests and their verified polling observation.
-- Shared StateStore remains the sole connection/transaction owner.
CREATE TABLE IF NOT EXISTS console_control_intents (
    intent_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch > 0),
    grant_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    assignment_epoch INTEGER NOT NULL CHECK (assignment_epoch > 0),
    action TEXT NOT NULL CHECK (action IN ('DRAIN','PAUSE','STOP')),
    reason TEXT NOT NULL,
    requested_at_us INTEGER NOT NULL,
    requested_event_id TEXT NOT NULL,
    observed_event_id TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','OBSERVED')),
    observed_at_us INTEGER,
    observing_principal_id TEXT,
    observing_shift_id TEXT,
    observed_receipt_id TEXT,
    CHECK ((status = 'PENDING' AND observed_event_id IS NULL AND observed_at_us IS NULL AND observing_principal_id IS NULL
            AND observing_shift_id IS NULL AND observed_receipt_id IS NULL)
        OR (status = 'OBSERVED' AND observed_event_id IS NOT NULL AND observed_at_us IS NOT NULL AND observing_principal_id IS NOT NULL
            AND observing_shift_id IS NOT NULL AND observed_receipt_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_console_controls_worker_pending
    ON console_control_intents (worker_id, assignment_epoch, status, requested_at_us, intent_id);
CREATE INDEX IF NOT EXISTS idx_console_controls_requested
    ON console_control_intents (requested_at_us DESC, intent_id DESC);
CREATE INDEX IF NOT EXISTS idx_console_controls_worker_history
    ON console_control_intents (worker_id, requested_at_us DESC, intent_id DESC);

-- Studio V1 initial schema: creative_authority/policy
-- Created exclusively under WG-AR; integrator registers with the original state owner.
CREATE TABLE IF NOT EXISTS creative_policy_head (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  revision INTEGER NOT NULL CHECK(revision >= 0),
  policy_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS creative_policy_nodes (
  node_id TEXT PRIMARY KEY,
  document_json TEXT NOT NULL,
  registered_by TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS creative_policy_rules (
  rule_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  document_json TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS creative_policy_history (
  revision INTEGER PRIMARY KEY,
  prior_digest TEXT NOT NULL,
  resulting_digest TEXT NOT NULL,
  operation TEXT NOT NULL,
  object_id TEXT NOT NULL,
  document_json TEXT NOT NULL,
  owner_decision_id TEXT NOT NULL UNIQUE,
  owner_principal_id TEXT NOT NULL,
  owner_issuer_ref TEXT NOT NULL,
  authority_proof_ref TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  recorded_at_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS creative_policy_rules_node ON creative_policy_rules(node_id);

-- Studio V1 initial schema: hr_bridge
-- Bridge contains operational digests/codes only; never private case plaintext.
CREATE TABLE IF NOT EXISTS hrb_observations (
  event_id TEXT PRIMARY KEY,
  subject_worker_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  assignment_epoch INTEGER NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  source_ref_sha256 TEXT NOT NULL,
  incident_category TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('PREPARED','PENDING_UNKNOWN','SENT')),
  receipt_sha256 TEXT,
  observed_at_us INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hrb_recommendations (
  recommendation_id TEXT PRIMARY KEY,
  subject_worker_id TEXT NOT NULL,
  assignment_epoch INTEGER NOT NULL,
  notice_id TEXT NOT NULL,
  action_category TEXT NOT NULL,
  reason_class TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  recommendation_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('OWNER_PENDING','PROPOSED'))
);
CREATE TABLE IF NOT EXISTS hrb_notices (
  notice_digest TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  notice_id TEXT NOT NULL UNIQUE,
  worker_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  assignment_epoch INTEGER NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('PREPARED','PENDING_UNKNOWN','APPLIED')),
  receipt_sha256 TEXT,
  approval_sha256 TEXT,
  issued_at_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS hrb_recs_owner ON hrb_recommendations(status,subject_worker_id);
CREATE INDEX IF NOT EXISTS hrb_notices_worker ON hrb_notices(worker_id,job_id,assignment_epoch);

-- Studio V1 initial schema: knowledge
-- WG-S owns only these namespace-prefixed tables. WG-A applies this migration.
CREATE TABLE IF NOT EXISTS knowledge_sources (
    record_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    revision TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('git', 'drive')),
    source_json TEXT NOT NULL,
    subject TEXT NOT NULL,
    domain TEXT NOT NULL,
    body TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    provenance_id TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('PUBLIC', 'INTERNAL')),
    folder_ref TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    departments_json TEXT NOT NULL,
    jobs_json TEXT NOT NULL,
    read_scopes_json TEXT NOT NULL,
    metadata_sha256 TEXT NOT NULL,
    UNIQUE (source_key, revision)
);
CREATE INDEX IF NOT EXISTS knowledge_sources_subject_idx ON knowledge_sources (subject, domain);
CREATE TABLE IF NOT EXISTS knowledge_heads (
    source_key TEXT PRIMARY KEY,
    current_revision TEXT NOT NULL,
    latest_record_id TEXT,
    FOREIGN KEY (latest_record_id) REFERENCES knowledge_sources(record_id)
);
CREATE TABLE IF NOT EXISTS knowledge_canon (
    subject TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES knowledge_sources(record_id),
    generation INTEGER NOT NULL CHECK (generation > 0)
);
CREATE TABLE IF NOT EXISTS knowledge_canon_history (
    subject TEXT NOT NULL,
    generation INTEGER NOT NULL,
    record_id TEXT NOT NULL REFERENCES knowledge_sources(record_id),
    owner_principal TEXT NOT NULL,
    accepted_at_us INTEGER NOT NULL,
    approval_reference TEXT NOT NULL,
    PRIMARY KEY (subject, generation)
);
CREATE TABLE IF NOT EXISTS knowledge_exports (
    digest TEXT PRIMARY KEY,
    source_keys_json TEXT NOT NULL,
    record_ids_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('CURRENT', 'STALE'))
);
CREATE TABLE IF NOT EXISTS knowledge_idempotency (
    key TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL,
    code TEXT NOT NULL,
    receipt_id TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_epochs (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    generation INTEGER NOT NULL CHECK (generation >= 0)
);
INSERT OR IGNORE INTO knowledge_epochs (id,generation) VALUES (1,0);

-- Studio V1 initial schema: operations
-- Operated by WG-A's module migration loader; do not run within WG-M.
CREATE TABLE IF NOT EXISTS operations_receipts (
    idempotency_key TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    state TEXT NOT NULL,
    code TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at_us INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS operations_reports (
    report_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('hourly', 'daily')),
    period_start_us INTEGER NOT NULL,
    period_end_us INTEGER NOT NULL,
    recorded_at_us INTEGER NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    audit_event_id TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS operations_reports_period ON operations_reports(kind, period_start_us);
CREATE TABLE IF NOT EXISTS operations_actions (
    case_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('alert', 'recovery', 'replacement')),
    action TEXT NOT NULL,
    state TEXT NOT NULL,
    expected_sha256 TEXT,
    recorded_sha256 TEXT,
    evidence_ref TEXT,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at_us INTEGER NOT NULL,
    updated_at_us INTEGER NOT NULL,
    audit_event_id TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS operations_actions_state ON operations_actions(kind, state, updated_at_us);
CREATE INDEX IF NOT EXISTS operations_actions_health ON operations_actions(action, updated_at_us);

-- Studio V1 initial schema: organization
-- WG-G schema. Applied solely by WG-A's ordered, checksummed migration loader.
CREATE TABLE org_features (
 feature_id TEXT PRIMARY KEY,
 issue_ref TEXT NOT NULL UNIQUE,
 issue_revision TEXT NOT NULL,
 title TEXT NOT NULL,
 scope_sha256 TEXT NOT NULL,
 scope_json TEXT NOT NULL,
 approval_state TEXT NOT NULL CHECK (approval_state IN ('REGISTERED','APPROVED','FENCED')),
 approval_receipt TEXT,
 created_at_us INTEGER NOT NULL,
 updated_at_us INTEGER NOT NULL
);
CREATE TABLE org_jobs (
 job_id TEXT PRIMARY KEY,
 feature_id TEXT NOT NULL REFERENCES org_features(feature_id),
 department_id TEXT NOT NULL,
 role_id TEXT NOT NULL,
 creator_id TEXT NOT NULL,
 implementer_id TEXT,
 reviewer_id TEXT,
 accountable_head_role TEXT NOT NULL,
 assignment_head_epoch INTEGER,
 assignment_head_id TEXT,
 scope_sha256 TEXT NOT NULL,
 scope_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK (state IN ('PLANNED','ASSIGNED')),
 created_at_us INTEGER NOT NULL
);
CREATE INDEX org_jobs_feature ON org_jobs(feature_id,job_id);
CREATE INDEX org_jobs_department ON org_jobs(department_id,state);
CREATE TABLE org_edges (
 predecessor_id TEXT NOT NULL REFERENCES org_jobs(job_id),
 successor_id TEXT NOT NULL REFERENCES org_jobs(job_id),
 PRIMARY KEY(predecessor_id,successor_id),
 CHECK (predecessor_id <> successor_id)
);
CREATE INDEX org_edges_successor ON org_edges(successor_id);
CREATE TABLE org_leaders (
 slot_id TEXT PRIMARY KEY,
 required_role_id TEXT NOT NULL,
 principal_id TEXT,
 deputy_principal_id TEXT,
 epoch INTEGER NOT NULL DEFAULT 0 CHECK (epoch >= 0),
 status TEXT NOT NULL DEFAULT 'VACANT' CHECK(status IN ('VACANT','PRESENT','MISSING','ACTING')),
 decision_receipt TEXT
);
INSERT INTO org_leaders(slot_id,required_role_id) VALUES
 ('director','studio-director'),
 ('production','rpg-lead-producer'),
 ('design','rpg-design-director'),
 ('narrative','rpg-narrative-director'),
 ('engineering','rpg-lead-engineer'),
 ('visual_art','rpg-art-director'),
 ('tech_art','rpg-technical-art-director'),
 ('animation','rpg-animation-director'),
 ('audio','rpg-audio-director'),
 ('ux','rpg-ux-director'),
 ('qa','rpg-qa-lead'),
 ('publishing','rpg-release-manager'),
 ('studio_ops','rpg-studio-operations-manager');
CREATE TABLE org_handoffs (
 handoff_id TEXT PRIMARY KEY,
 from_job_id TEXT NOT NULL REFERENCES org_jobs(job_id),
 to_job_id TEXT NOT NULL REFERENCES org_jobs(job_id),
 from_department_id TEXT NOT NULL,
 to_department_id TEXT NOT NULL,
 source_ack_epoch INTEGER,
 target_ack_epoch INTEGER,
 source_ack_id TEXT,
 target_ack_id TEXT,
 UNIQUE(from_job_id,to_job_id)
);
CREATE TABLE org_authority_grants (
 grant_id TEXT PRIMARY KEY,
 principal_id TEXT NOT NULL,
 department_id TEXT NOT NULL,
 operations_json TEXT NOT NULL,
 expires_at_us INTEGER NOT NULL,
 issued_by TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 issued_at_us INTEGER NOT NULL
);
CREATE INDEX org_grants_lookup ON org_authority_grants(principal_id,department_id,active);
CREATE TABLE org_escalations (
 escalation_id TEXT PRIMARY KEY,
 feature_id TEXT NOT NULL REFERENCES org_features(feature_id),
 category TEXT NOT NULL CHECK(category IN ('ARCHITECTURE','CANON')),
 reason TEXT NOT NULL,
 raised_by TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','RESOLVED')),
 resolution_ref TEXT,
 resolved_by TEXT,
 issued_at_us INTEGER NOT NULL
);
CREATE INDEX org_escalations_feature ON org_escalations(feature_id,status);
CREATE TABLE org_notices (
 notice_ref TEXT PRIMARY KEY,
 decision_code TEXT NOT NULL,
 feature_id TEXT REFERENCES org_features(feature_id),
 evidence_sha256 TEXT NOT NULL,
 issued_at_us INTEGER NOT NULL
);
CREATE TABLE org_requests (
 idempotency_key TEXT PRIMARY KEY,
 request_sha256 TEXT NOT NULL,
 receipt_id TEXT NOT NULL,
 state TEXT NOT NULL,
 code TEXT NOT NULL,
 result_json TEXT NOT NULL,
 created_at_us INTEGER NOT NULL
);

-- Studio V1 initial schema: oversight
-- WG-N; metadata only. No witness names, narratives, ciphertext or signatures.
CREATE TABLE IF NOT EXISTS oversight_notices (
  notice_digest TEXT PRIMARY KEY,
  issuer_id TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  assignment_epoch INTEGER NOT NULL CHECK (assignment_epoch >= 0),
  action TEXT NOT NULL CHECK (action IN ('HOLD_JOB','QUARANTINE_WORKER','REVOKE_WORKER_CAPABILITY')),
  expires_at_us INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('ISSUED','PREPARED','APPLIED','PENDING_UNKNOWN','REVOKED')),
  receipt_digest TEXT,
  updated_at_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS oversight_notices_target ON oversight_notices(worker_id, job_id, assignment_epoch);
CREATE TABLE IF NOT EXISTS oversight_requests (
  idempotency_key TEXT PRIMARY KEY,
  operation TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  outcome_state TEXT,
  outcome_code TEXT,
  notice_digest TEXT,
  receipt_id TEXT
);

-- Studio V1 initial schema: publication
-- Registered by the sole WG-A SQLite migration owner; no independent connection.
CREATE TABLE IF NOT EXISTS publication_intents (
  intent_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  effect_key TEXT NOT NULL UNIQUE,
  fingerprint TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('github','drive')),
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PREPARED','DISPATCHING','PENDING_UNKNOWN','EXTERNALLY_OBSERVED','VERIFIED','CONFLICT','BLOCKED')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count>=0),
  next_attempt_us INTEGER NOT NULL DEFAULT 0,
  claim_token TEXT,
  observed_revision TEXT,
  last_code TEXT,
  created_us INTEGER NOT NULL,
  updated_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS publication_intents_state_due ON publication_intents(state,next_attempt_us,updated_us);
CREATE INDEX IF NOT EXISTS publication_intents_principal ON publication_intents(principal_id,created_us);
CREATE TABLE IF NOT EXISTS publication_quota (
  provider TEXT PRIMARY KEY,
  window_start_us INTEGER NOT NULL,
  used INTEGER NOT NULL CHECK(used>=0)
);
CREATE TABLE IF NOT EXISTS publication_dead_letters (
  intent_id TEXT PRIMARY KEY REFERENCES publication_intents(intent_id),
  reason TEXT NOT NULL,
  created_us INTEGER NOT NULL
);

-- Studio V1 initial schema: relay
-- WG-C owns only relay_* tables; apply via the shared SQLite migration owner.
CREATE TABLE relay_workers (
    worker_id TEXT PRIMARY KEY,
    assignment_epoch INTEGER NOT NULL CHECK (assignment_epoch >= 1),
    active INTEGER NOT NULL CHECK (active IN (0,1)),
    updated_at_us INTEGER NOT NULL
);
CREATE TABLE relay_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    recipient_worker_id TEXT NOT NULL,
    assignment_epoch INTEGER NOT NULL CHECK (assignment_epoch >= 1),
    kind TEXT NOT NULL CHECK (kind IN ('PROMPT_BATCH','CONTROL')),
    body_json TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    causation_id TEXT,
    created_at_us INTEGER NOT NULL,
    available_at_us INTEGER NOT NULL,
    expires_at_us INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ADDRESSED','DELIVERED','ACKED','EXPIRED','DEAD_LETTER')),
    delivery_count INTEGER NOT NULL DEFAULT 0,
    last_delivered_at_us INTEGER,
    acked_at_us INTEGER,
    processing_receipt_id TEXT,
    dead_letter_reason TEXT,
    external_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (external_status IN ('PENDING','IN_FLIGHT','UNKNOWN','BACKOFF','VERIFIED','CONFLICT')),
    external_anchor TEXT NOT NULL UNIQUE,
    external_ref TEXT,
    external_revision TEXT,
    dispatch_attempts INTEGER NOT NULL DEFAULT 0,
    publish_attempts INTEGER NOT NULL DEFAULT 0,
    dispatch_token TEXT,
    dispatch_until_us INTEGER,
    external_retry_at_us INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (recipient_worker_id) REFERENCES relay_workers(worker_id)
);
CREATE INDEX relay_scan ON relay_messages(recipient_worker_id,assignment_epoch,status,available_at_us,sequence);
CREATE INDEX relay_expiration ON relay_messages(status,expires_at_us);
CREATE INDEX relay_dispatch ON relay_messages(external_status,external_retry_at_us);
CREATE TABLE relay_cursors (
    worker_id TEXT NOT NULL,
    assignment_epoch INTEGER NOT NULL,
    last_sequence INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(worker_id,assignment_epoch),
    FOREIGN KEY(worker_id) REFERENCES relay_workers(worker_id)
);
CREATE TABLE relay_external_events (
    provider TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    repository TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    revision TEXT NOT NULL,
    event_id TEXT NOT NULL,
    received_at_us INTEGER NOT NULL,
    PRIMARY KEY(provider,delivery_id)
);
CREATE INDEX relay_external_ref ON relay_external_events(repository,external_ref);
CREATE TABLE relay_request_keys (
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_sha256 TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY(operation,idempotency_key)
);

-- Studio V1 initial schema: review
-- WG-I owns these tables. The sole StateStore owner applies this migration.
CREATE TABLE IF NOT EXISTS review_jobs (
 job_id TEXT PRIMARY KEY, department TEXT NOT NULL, repo TEXT NOT NULL,
 branch TEXT NOT NULL, source_sha TEXT NOT NULL, artifact_hash TEXT,
 artifact_revision TEXT, submitter TEXT NOT NULL, authors_json TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK (revision >= 1),
 status TEXT NOT NULL CHECK(status IN ('PENDING','QA_PENDING','PASS','REWORK','NOT_RUN','INVALIDATED')),
 created_us INTEGER NOT NULL, updated_us INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS review_revisions (
 job_id TEXT NOT NULL REFERENCES review_jobs(job_id), revision INTEGER NOT NULL,
 source_sha TEXT NOT NULL, artifact_hash TEXT, artifact_revision TEXT,
 authors_json TEXT NOT NULL, status TEXT NOT NULL, changed_by TEXT NOT NULL,
 created_us INTEGER NOT NULL, PRIMARY KEY(job_id, revision)
);
CREATE TABLE IF NOT EXISTS review_verdicts (
 job_id TEXT NOT NULL REFERENCES review_jobs(job_id), revision INTEGER NOT NULL,
 reviewer TEXT NOT NULL, result TEXT NOT NULL CHECK(result IN ('PASS','REWORK','NOT_RUN')),
 findings_json TEXT NOT NULL, ci_digest TEXT NOT NULL, ci_runs_json TEXT NOT NULL,
 decided_us INTEGER NOT NULL, PRIMARY KEY(job_id, revision)
);
CREATE TABLE IF NOT EXISTS review_integrations (
 job_id TEXT NOT NULL REFERENCES review_jobs(job_id), revision INTEGER NOT NULL,
 repo TEXT NOT NULL, branch TEXT NOT NULL, integration_sha TEXT NOT NULL,
 target_branch TEXT NOT NULL, integrator TEXT NOT NULL,
 ci_digest TEXT NOT NULL, ci_runs_json TEXT NOT NULL, created_us INTEGER NOT NULL,
 PRIMARY KEY(job_id, revision)
);
CREATE TABLE IF NOT EXISTS review_merge_intents (
 merge_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES review_jobs(job_id),
 revision INTEGER NOT NULL, integration_sha TEXT NOT NULL,
 target_branch TEXT NOT NULL, expected_head_sha TEXT NOT NULL,
 head_principal TEXT NOT NULL, grant_receipt TEXT NOT NULL,
 decisions_digest TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('PREPARED','MERGED','INVALIDATED')),
 observed_head_sha TEXT, created_us INTEGER NOT NULL, reconciled_us INTEGER,
 UNIQUE(job_id, revision)
);
CREATE TABLE IF NOT EXISTS review_idempotency (
 idempotency_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
 outcome_json TEXT NOT NULL, created_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS review_jobs_status_idx ON review_jobs(status,updated_us);
CREATE INDEX IF NOT EXISTS review_revisions_job_idx ON review_revisions(job_id,revision);
CREATE INDEX IF NOT EXISTS review_merge_status_idx ON review_merge_intents(status,created_us);

-- Studio V1 initial schema: scheduler
-- Owned by WG-E. Install through the sole StateStore migration registry.
CREATE TABLE IF NOT EXISTS scheduler_jobs (
 job_id TEXT PRIMARY KEY,
 state TEXT NOT NULL CHECK (state IN ('APPROVED','READY','LEASED','RUNNING','CHECKPOINTED','YIELDED','SUBMITTED','QA_PENDING','REWORK','VERIFIED','OWNER_ACCEPTED','BLOCKED','CANCELLED')),
 approval_ref TEXT NOT NULL,
 approved_by TEXT NOT NULL,
 scope_digest TEXT NOT NULL,
 department TEXT NOT NULL,
 domain TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('general','qa')),
 roles_json TEXT NOT NULL,
 priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 100),
 repo TEXT,
 branch TEXT,
 source_sha TEXT,
 paths_json TEXT NOT NULL,
 resources_json TEXT NOT NULL,
 parent_job_id TEXT,
 retry_budget INTEGER NOT NULL CHECK(retry_budget BETWEEN 0 AND 32),
 attempts INTEGER NOT NULL DEFAULT 0,
 assignment_epoch INTEGER NOT NULL DEFAULT 0,
 assigned_worker_id TEXT,
 assigned_principal_id TEXT,
 preferred_worker_id TEXT,
 shift_id TEXT,
 lease_until_us INTEGER,
 created_us INTEGER NOT NULL,
 last_dispatch_us INTEGER,
 last_subject_sha TEXT,
 author_principal_id TEXT,
 created_seq INTEGER NOT NULL UNIQUE,
 FOREIGN KEY(parent_job_id) REFERENCES scheduler_jobs(job_id)
);
CREATE INDEX IF NOT EXISTS scheduler_jobs_ready_scan ON scheduler_jobs(state, created_seq, department);
CREATE INDEX IF NOT EXISTS scheduler_jobs_seq ON scheduler_jobs(created_seq);
CREATE INDEX IF NOT EXISTS scheduler_jobs_department ON scheduler_jobs(department, state, priority DESC, created_seq);
CREATE INDEX IF NOT EXISTS scheduler_jobs_assignee ON scheduler_jobs(assigned_worker_id, assignment_epoch);
CREATE INDEX IF NOT EXISTS scheduler_jobs_parent ON scheduler_jobs(parent_job_id);
CREATE TABLE IF NOT EXISTS scheduler_dependencies (
 job_id TEXT NOT NULL REFERENCES scheduler_jobs(job_id),
 prerequisite_job_id TEXT NOT NULL REFERENCES scheduler_jobs(job_id),
 PRIMARY KEY(job_id,prerequisite_job_id),
 CHECK(job_id <> prerequisite_job_id)
);
CREATE INDEX IF NOT EXISTS scheduler_dependencies_parent ON scheduler_dependencies(prerequisite_job_id);
CREATE TABLE IF NOT EXISTS scheduler_shifts (
 shift_id TEXT PRIMARY KEY,
 worker_id TEXT NOT NULL,
 principal_id TEXT NOT NULL,
 authority_epoch INTEGER NOT NULL,
 lane TEXT NOT NULL CHECK(lane IN ('general','qa')),
 role_id TEXT NOT NULL,
 department TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('ACTIVE','RELEASED')),
 admitted_us INTEGER NOT NULL,
 expiry_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS scheduler_shifts_capacity ON scheduler_shifts(state,lane);
CREATE INDEX IF NOT EXISTS scheduler_shifts_worker ON scheduler_shifts(worker_id,authority_epoch,state);
CREATE TABLE IF NOT EXISTS scheduler_leases (
 job_id TEXT NOT NULL REFERENCES scheduler_jobs(job_id),
 kind TEXT NOT NULL CHECK(kind IN ('path','resource')),
 repo TEXT NOT NULL,
 branch TEXT NOT NULL,
 value TEXT NOT NULL,
 assignment_epoch INTEGER NOT NULL,
 PRIMARY KEY(job_id,kind,repo,branch,value)
);
CREATE INDEX IF NOT EXISTS scheduler_leases_conflicts ON scheduler_leases(kind,repo,branch,value);
CREATE TABLE IF NOT EXISTS scheduler_dispatch_history (
 department TEXT PRIMARY KEY,
 last_dispatch_seq INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scheduler_counters (
 name TEXT PRIMARY KEY,
 value INTEGER NOT NULL
);
INSERT OR IGNORE INTO scheduler_counters(name,value) VALUES ('created_seq',0),('dispatch_seq',0);
CREATE TABLE IF NOT EXISTS scheduler_idempotency (
 idempotency_key TEXT PRIMARY KEY,
 digest TEXT NOT NULL,
 principal_id TEXT NOT NULL,
 state TEXT NOT NULL,
 code TEXT NOT NULL,
 receipt_id TEXT,
 response_json TEXT NOT NULL
);

-- Studio V1 initial schema: shift
-- Registered by WG-A's module migration loader; never applied by WG-D itself.
CREATE TABLE IF NOT EXISTS shift_responses (
 response_id TEXT PRIMARY KEY,
 worker_id TEXT NOT NULL,
 principal_id TEXT NOT NULL,
 credential_id TEXT NOT NULL,
 authority_epoch INTEGER NOT NULL CHECK(authority_epoch > 0),
 control_epoch INTEGER NOT NULL CHECK(control_epoch >= 0),
 state TEXT NOT NULL CHECK(state IN ('RUNNING','DRAINING','PAUSED','STOPPED','YIELDED','BLOCKED','UNKNOWN')),
 created_us INTEGER NOT NULL,
 updated_us INTEGER NOT NULL,
 finished_us INTEGER,
 poll_count INTEGER NOT NULL DEFAULT 0,
 capsule_json TEXT,
 capsule_sha256 TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS shift_one_live_response_per_worker
 ON shift_responses(worker_id) WHERE state IN ('RUNNING','DRAINING','PAUSED','UNKNOWN');
CREATE INDEX IF NOT EXISTS shift_responses_by_worker ON shift_responses(worker_id, updated_us);
CREATE TABLE IF NOT EXISTS shift_attempts (
 attempt_id TEXT PRIMARY KEY,
 response_id TEXT NOT NULL REFERENCES shift_responses(response_id),
 job_id TEXT NOT NULL,
 assignment_epoch INTEGER NOT NULL CHECK(assignment_epoch > 0),
 lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
 assignment_id TEXT,
 source_sha TEXT,
 status TEXT NOT NULL CHECK(status IN ('CLAIMED','WORKING','CHECKPOINTED','SUBMITTED','UNKNOWN','YIELDED')),
 created_us INTEGER NOT NULL,
 updated_us INTEGER NOT NULL,
 UNIQUE(response_id, job_id, assignment_epoch)
);
CREATE INDEX IF NOT EXISTS shift_attempts_by_response ON shift_attempts(response_id, created_us);
CREATE TABLE IF NOT EXISTS shift_job_receipts (
 receipt_id TEXT PRIMARY KEY,
 response_id TEXT NOT NULL REFERENCES shift_responses(response_id),
 attempt_id TEXT NOT NULL REFERENCES shift_attempts(attempt_id),
 job_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('WORK','SUBMIT','EXTERNAL_UNKNOWN','CHECKPOINT')),
 report_json TEXT NOT NULL,
 report_sha256 TEXT NOT NULL,
 created_us INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS shift_receipts_by_attempt ON shift_job_receipts(attempt_id, created_us);
CREATE TABLE IF NOT EXISTS shift_claim_intents (
 response_id TEXT NOT NULL REFERENCES shift_responses(response_id),
 idempotency_key TEXT NOT NULL,
 request_digest TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','UNKNOWN','RESOLVED')),
 attempt_id TEXT,
 created_us INTEGER NOT NULL,
 PRIMARY KEY(response_id, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS shift_unresolved_claim_per_response
 ON shift_claim_intents(response_id) WHERE status IN ('PENDING','UNKNOWN');
CREATE TABLE IF NOT EXISTS shift_replays (
 idempotency_key TEXT PRIMARY KEY,
 principal_id TEXT NOT NULL,
 request_digest TEXT NOT NULL,
 outcome_json TEXT NOT NULL,
 created_us INTEGER NOT NULL
);

-- Studio V1 initial schema: single_operator/capabilities
-- AJ owns only cap_* tables; migrations are registered by the P2 integrator.
-- This SQL is immutable and loaded by StateStore's versioned migration manager.
CREATE TABLE IF NOT EXISTS cap_hosts (
    host_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, origin TEXT NOT NULL, revision INTEGER NOT NULL,
    authority_epoch INTEGER NOT NULL, source_sha TEXT NOT NULL,
    capability_config_json TEXT NOT NULL, registered_at_us INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0,1))
);
CREATE TABLE IF NOT EXISTS cap_receipts (
    receipt_id TEXT PRIMARY KEY, host_id TEXT NOT NULL, capability_id TEXT NOT NULL,
    host_revision INTEGER NOT NULL, authority_epoch INTEGER NOT NULL,
    source_sha TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL,
    observer_type TEXT NOT NULL, observed_at_us INTEGER NOT NULL,
    expires_at_us INTEGER NOT NULL, evidence_json TEXT NOT NULL,
    FOREIGN KEY(host_id) REFERENCES cap_hosts(host_id)
);
CREATE INDEX IF NOT EXISTS cap_receipts_latest ON cap_receipts(host_id,capability_id,observed_at_us DESC);
CREATE TABLE IF NOT EXISTS cap_shifts (
    shift_id TEXT PRIMARY KEY, host_id TEXT NOT NULL, worker_id TEXT NOT NULL,
    session_ref TEXT NOT NULL, session_epoch INTEGER NOT NULL,
    host_revision INTEGER NOT NULL, state TEXT NOT NULL,
    lease_until_us INTEGER NOT NULL, jobs_completed INTEGER NOT NULL DEFAULT 0,
    wake_verified INTEGER NOT NULL DEFAULT 0, budget_remaining INTEGER NOT NULL DEFAULT 0,
    real_host INTEGER NOT NULL DEFAULT 0,
    last_observed_us INTEGER NOT NULL, manual_launch_ref TEXT NOT NULL,
    FOREIGN KEY(host_id) REFERENCES cap_hosts(host_id)
);
CREATE TABLE IF NOT EXISTS cap_host_events (
    observation_id TEXT PRIMARY KEY, shift_id TEXT NOT NULL, event_kind TEXT NOT NULL,
    event_sha TEXT NOT NULL, observed_at_us INTEGER NOT NULL, event_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cap_messages (
    message_id TEXT PRIMARY KEY, sender_worker_id TEXT NOT NULL,
    recipient_worker_id TEXT NOT NULL, recipient_shift_id TEXT NOT NULL,
    session_epoch INTEGER NOT NULL, job_id TEXT, project_id TEXT,
    kind TEXT NOT NULL, message_sha TEXT NOT NULL, message_json TEXT NOT NULL,
    created_at_us INTEGER NOT NULL, expires_at_us INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cap_deliveries (
    delivery_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL, receipt_sha TEXT NOT NULL, observed_at_us INTEGER NOT NULL,
    FOREIGN KEY(message_id) REFERENCES cap_messages(message_id)
);
CREATE TABLE IF NOT EXISTS cap_idempotency (
    actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_sha TEXT NOT NULL, outcome_json TEXT NOT NULL,
    PRIMARY KEY(actor_id,idempotency_key)
);

-- Studio V1 initial schema: single_operator/convergence
-- WG-AI; sole original StateStore owner applies this namespaced migration.
CREATE TABLE convergence_features (
 project_id TEXT NOT NULL, feature_id TEXT NOT NULL, issue_ref TEXT NOT NULL,
 candidate_sha256 TEXT NOT NULL, build_sha256 TEXT, revision INTEGER NOT NULL CHECK(revision>0),
 artifact_ref TEXT NOT NULL, author_id TEXT NOT NULL,
 capacity_json TEXT NOT NULL, proof_json TEXT NOT NULL,
 updated_at_us INTEGER NOT NULL,
 PRIMARY KEY(project_id,feature_id)
);
CREATE TABLE convergence_tasks (
 project_id TEXT NOT NULL, feature_id TEXT NOT NULL, task_id TEXT NOT NULL,
 department_id TEXT NOT NULL, author_id TEXT NOT NULL,
 status TEXT NOT NULL, duration_units INTEGER NOT NULL,
 dependencies_json TEXT NOT NULL, provider TEXT, lease_waiting INTEGER NOT NULL,
 reviewer_waiting INTEGER NOT NULL, tests_waiting INTEGER NOT NULL,
 command_id TEXT NOT NULL,
 PRIMARY KEY(project_id,feature_id,task_id),
 FOREIGN KEY(project_id,feature_id) REFERENCES convergence_features(project_id,feature_id)
);
CREATE INDEX convergence_tasks_by_project ON convergence_tasks(project_id,feature_id,status);
CREATE TABLE convergence_reviews (
 review_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, feature_id TEXT NOT NULL,
 candidate_sha256 TEXT NOT NULL, build_sha256 TEXT NOT NULL,
 kind TEXT NOT NULL, result TEXT NOT NULL, observer_type TEXT NOT NULL,
 reviewer_principal TEXT NOT NULL, evidence_json TEXT NOT NULL,
 verified_receipt_ref TEXT NOT NULL, owner_decision_ref TEXT,
 issued_at_us INTEGER NOT NULL
);
CREATE INDEX convergence_review_by_feature ON convergence_reviews(project_id,feature_id,issued_at_us);
CREATE TABLE convergence_observations (
 observation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, feature_id TEXT NOT NULL,
 candidate_sha256 TEXT NOT NULL, data_json TEXT NOT NULL,
 reporter_principal TEXT NOT NULL, issued_at_us INTEGER NOT NULL
);
CREATE TABLE convergence_messages (
 message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, feature_id TEXT NOT NULL,
 from_worker_id TEXT NOT NULL, to_worker_id TEXT NOT NULL,
 message_kind TEXT NOT NULL CHECK(message_kind IN ('NEEDS_FROM','PROVIDES')),
 dependency_id TEXT NOT NULL, artifact_ref TEXT NOT NULL, subject_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PROPOSED','VERIFIED')),
 created_at_us INTEGER NOT NULL
);
CREATE INDEX convergence_msg_by_recipient ON convergence_messages(to_worker_id,project_id,created_at_us);
CREATE TABLE convergence_proposals (
 proposal_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, feature_id TEXT NOT NULL,
 kind TEXT NOT NULL, target_author_id TEXT NOT NULL,
 data_json TEXT NOT NULL, created_at_us INTEGER NOT NULL
);
CREATE INDEX convergence_proposals_by_feature ON convergence_proposals(project_id,feature_id,created_at_us);
CREATE TABLE convergence_requests (
 idempotency_key TEXT PRIMARY KEY, command_digest TEXT NOT NULL,
 principal_id TEXT NOT NULL, outcome_json TEXT NOT NULL,
 created_at_us INTEGER NOT NULL
);

-- Studio V1 initial schema: single_operator/ownercontrol
CREATE TABLE IF NOT EXISTS ownercontrol_packets (
  packet_id TEXT PRIMARY KEY,
  payload_digest TEXT NOT NULL,
  semantic_digest TEXT NOT NULL,
  feature_id TEXT NOT NULL,
  milestone_id TEXT,
  priority TEXT NOT NULL,
  subject_digest TEXT NOT NULL,
  build_digest TEXT NOT NULL,
  source_digest TEXT NOT NULL,
  policy_revision INTEGER NOT NULL,
  authority_epoch INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('OWNER_REVIEW_REQUIRED','APPROVED','REJECTED','DEFERRED','EXPIRED','SUPERSEDED')),
  expires_at_us INTEGER NOT NULL,
  created_at_us INTEGER NOT NULL,
  owner_decision_ref TEXT,
  payload_json TEXT NOT NULL,
  changed_at_us INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ownercontrol_active_semantic ON ownercontrol_packets(semantic_digest) WHERE state='OWNER_REVIEW_REQUIRED';
CREATE INDEX IF NOT EXISTS ownercontrol_packet_queue ON ownercontrol_packets(state,priority,expires_at_us,feature_id);
CREATE TABLE IF NOT EXISTS ownercontrol_decisions (
  packet_id TEXT PRIMARY KEY REFERENCES ownercontrol_packets(packet_id),
  outcome TEXT NOT NULL CHECK(outcome IN ('APPROVED','REJECTED','DEFERRED')),
  evidence_digest TEXT NOT NULL,
  signature_ref TEXT NOT NULL,
  owner_principal_id TEXT NOT NULL,
  decision_at_us INTEGER NOT NULL,
  authority_epoch INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ownercontrol_queue_meta (
  id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
  max_nonurgent INTEGER NOT NULL CHECK(max_nonurgent BETWEEN 0 AND 500)
);
INSERT OR IGNORE INTO ownercontrol_queue_meta (id,revision,max_nonurgent) VALUES (1,0,15);
CREATE TABLE IF NOT EXISTS ownercontrol_commands (
  command_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL, intent_digest TEXT NOT NULL,
  intent_json TEXT NOT NULL, project_id TEXT NOT NULL,
  scope_digest TEXT NOT NULL, approval_class TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('READY_FOR_DISPATCH','HELD_SAFETY','AWAITING_OWNER','EXPIRED')),
  queue_revision INTEGER NOT NULL, authority_epoch INTEGER NOT NULL, owner_proof_ref TEXT NOT NULL,
  created_at_us INTEGER NOT NULL, expires_at_us INTEGER NOT NULL,
  UNIQUE(principal_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS ownercontrol_stops (
  scope_id TEXT PRIMARY KEY, stopped INTEGER NOT NULL CHECK(stopped IN (0,1)),
  revision INTEGER NOT NULL, proof_ref TEXT NOT NULL, updated_at_us INTEGER NOT NULL
);

-- Studio V1 initial schema: single_operator/personas
-- Owned namespace: persona. Integration must explicitly register this migration.
CREATE TABLE persona_profiles (
    worker_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    profile_digest TEXT NOT NULL CHECK (length(profile_digest) = 64),
    role_id TEXT NOT NULL,
    department TEXT NOT NULL,
    policy_revision INTEGER NOT NULL CHECK (policy_revision >= 0),
    assignment_epoch INTEGER NOT NULL CHECK (assignment_epoch >= 0),
    profile_json TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    PRIMARY KEY (worker_id, revision)
) WITHOUT ROWID;
CREATE TABLE persona_active (
    worker_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    profile_digest TEXT NOT NULL,
    FOREIGN KEY (worker_id, revision) REFERENCES persona_profiles(worker_id, revision)
) WITHOUT ROWID;
CREATE TABLE persona_capsules (
    capsule_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    shift_id TEXT NOT NULL,
    response_id TEXT NOT NULL,
    context_seq INTEGER NOT NULL CHECK (context_seq >= 1),
    capsule_digest TEXT NOT NULL,
    capsule_json TEXT NOT NULL,
    binding_json TEXT NOT NULL,
    prior_capsule_digest TEXT,
    created_at_us INTEGER NOT NULL,
    UNIQUE(worker_id, shift_id, context_seq),
    UNIQUE(worker_id, shift_id, response_id),
    FOREIGN KEY(worker_id) REFERENCES persona_active(worker_id)
) WITHOUT ROWID;
CREATE INDEX persona_capsules_shift ON persona_capsules(worker_id, shift_id, context_seq);

-- Studio V1 initial schema: source
-- Registered through the sole StateStore migration authority; this file does not open SQLite.
CREATE TABLE IF NOT EXISTS source_targets (
    target_key TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    branch_ref TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    mapping_epoch INTEGER NOT NULL CHECK (mapping_epoch > 0),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    configured_by TEXT NOT NULL,
    verified_head TEXT NOT NULL,
    verified_tree TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_operations (
    idempotency_key TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    outcome_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_proofs (
    receipt_id TEXT PRIMARY KEY,
    target_key TEXT NOT NULL REFERENCES source_targets(target_key),
    proof_kind TEXT NOT NULL,
    subject_sha TEXT NOT NULL,
    proof_digest TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    issued_at_us INTEGER NOT NULL,
    actor_principal_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS source_proofs_target_sha ON source_proofs (target_key, subject_sha);

-- Studio V1 initial schema: trust
-- WG-B namespace: registered security epochs, single-use launch capabilities,
-- live response handles, and mutation idempotency. Applied only by WG-A owner.
CREATE TABLE IF NOT EXISTS trust_workers (
 worker_id TEXT PRIMARY KEY, role_id TEXT NOT NULL,
 authority_epoch INTEGER NOT NULL CHECK(authority_epoch >= 0),
 revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
 modified_at_us INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trust_launches (
 ticket_id TEXT PRIMARY KEY, verifier BLOB NOT NULL,
 worker_id TEXT NOT NULL REFERENCES trust_workers(worker_id),
 role_id TEXT NOT NULL, job_id TEXT NOT NULL,
 scope_digest TEXT NOT NULL, scopes_json TEXT NOT NULL,
 authority_epoch INTEGER NOT NULL, connector_credential_id TEXT NOT NULL,
 issued_by TEXT NOT NULL, expires_at_us INTEGER NOT NULL,
 consumed_at_us INTEGER, revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
);
CREATE INDEX IF NOT EXISTS trust_launch_worker_epoch ON trust_launches(worker_id,authority_epoch);
CREATE TABLE IF NOT EXISTS trust_sessions (
 session_id TEXT PRIMARY KEY, verifier BLOB NOT NULL,
 ticket_id TEXT NOT NULL UNIQUE REFERENCES trust_launches(ticket_id),
 worker_id TEXT NOT NULL REFERENCES trust_workers(worker_id),
 role_id TEXT NOT NULL, job_id TEXT NOT NULL,
 shift_id TEXT NOT NULL UNIQUE, scopes_json TEXT NOT NULL,
 authority_epoch INTEGER NOT NULL, connector_credential_id TEXT NOT NULL,
 expires_at_us INTEGER NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
);
CREATE INDEX IF NOT EXISTS trust_session_worker_epoch ON trust_sessions(worker_id,authority_epoch);
CREATE TABLE IF NOT EXISTS trust_mutation_keys (
 idempotency_key TEXT PRIMARY KEY, operation TEXT NOT NULL,
 actor_principal_id TEXT NOT NULL, receipt_id TEXT NOT NULL
);

