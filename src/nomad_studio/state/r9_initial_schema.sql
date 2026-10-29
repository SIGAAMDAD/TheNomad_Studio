-- NOMAD STUDIO V1 R8: CORRECTED SINGLE NEW-ONLY INITIAL SCHEMA (contract, not deployed).
-- No historical migrations, upgrade/alias layers, or old department/rank tables.
PRAGMA foreign_keys=ON;

CREATE TABLE v1_events (
    event_id TEXT PRIMARY KEY,
    event_seq INTEGER NOT NULL UNIQUE,
    project_id TEXT,
    feature_id TEXT,
    domain TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    input_digest TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    issued_at_us INTEGER NOT NULL
);

CREATE TABLE v1_idempotency (
    namespace TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    created_at_us INTEGER NOT NULL,
    PRIMARY KEY(namespace,idempotency_key)
);

CREATE TABLE v1_transaction_receipts (
    receipt_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    created_at_us INTEGER NOT NULL
);

CREATE TABLE v1_outbox (
    message_id TEXT PRIMARY KEY,
    project_id TEXT,
    feature_id TEXT,
    domain TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','ACKED','UNKNOWN')),
    created_at_us INTEGER NOT NULL,
    retry_after_us INTEGER
);

CREATE TABLE v1_recovery_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    feature_id TEXT,
    source_digest TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at_us INTEGER NOT NULL
);

CREATE TABLE v1_projects (
    project_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    constitution_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('INCUBATING','ACTIVE','PAUSED')),
    authority_epoch INTEGER NOT NULL CHECK(authority_epoch >= 0)
);

CREATE TABLE v1_project_constitutions (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    epoch INTEGER NOT NULL CHECK(epoch > 0),
    constitution_digest TEXT NOT NULL,
    owner_activation_ref TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(project_id,epoch)
);

CREATE TABLE v1_bible_editions (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    kind TEXT NOT NULL CHECK(kind IN ('DESIGN','TECHNICAL')),
    edition_id TEXT PRIMARY KEY,
    epoch INTEGER NOT NULL CHECK(epoch >= 0),
    manifest_digest TEXT NOT NULL,
    source_repo TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    owner_activation_ref TEXT,
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
    payload_json TEXT NOT NULL,
    CHECK(active=0 OR owner_activation_ref IS NOT NULL),
    UNIQUE(project_id,edition_id)
);

CREATE TABLE v1_bible_clauses (
    edition_id TEXT NOT NULL REFERENCES v1_bible_editions(edition_id),
    clause_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('REQUIREMENT','GUIDELINE','OPEN_AREA')),
    scope TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    priority INTEGER NOT NULL CHECK(priority >= 0),
    PRIMARY KEY(edition_id,clause_id)
);

CREATE TABLE v1_owner_decisions (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    decision_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_digest TEXT NOT NULL,
    proof_ref TEXT NOT NULL UNIQUE,
    expires_at_us INTEGER NOT NULL,
    consumed_at_us INTEGER,
    payload_json TEXT NOT NULL,
    UNIQUE(project_id,decision_id)
);

CREATE TABLE v1_bible_exceptions (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    exception_id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL,
    clause_id TEXT NOT NULL,
    feature_id TEXT,
    scope_digest TEXT NOT NULL,
    owner_decision_id TEXT NOT NULL,
    expires_at_us INTEGER NOT NULL,
    FOREIGN KEY(project_id,edition_id) REFERENCES v1_bible_editions(project_id,edition_id),
    FOREIGN KEY(edition_id,clause_id) REFERENCES v1_bible_clauses(edition_id,clause_id),
    FOREIGN KEY(project_id,owner_decision_id) REFERENCES v1_owner_decisions(project_id,decision_id)
);

CREATE TABLE v1_briefs (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    brief_id TEXT PRIMARY KEY,
    feature_id TEXT NOT NULL UNIQUE,
    owner_principal_id TEXT NOT NULL,
    original_text TEXT NOT NULL,
    original_digest TEXT NOT NULL,
    owner_command_ref TEXT NOT NULL UNIQUE,
    submitted_at_us INTEGER NOT NULL,
    UNIQUE(project_id,brief_id),
    UNIQUE(project_id,feature_id)
);

CREATE TABLE v1_feature_contracts (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    feature_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    brief_id TEXT NOT NULL,
    original_brief_digest TEXT NOT NULL,
    bible_pair_digest TEXT NOT NULL,
    contract_digest TEXT NOT NULL UNIQUE,
    scope_digest TEXT NOT NULL,
    deliverables_digest TEXT NOT NULL,
    acceptance_digest TEXT NOT NULL,
    budget_limit_units INTEGER NOT NULL CHECK(budget_limit_units >= 0),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(feature_id,revision),
    UNIQUE(project_id,feature_id,contract_digest),
    FOREIGN KEY(project_id,brief_id) REFERENCES v1_briefs(project_id,brief_id),
    FOREIGN KEY(project_id,feature_id) REFERENCES v1_briefs(project_id,feature_id)
);

CREATE TABLE v1_feature_runs (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    feature_id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL,
    active_contract_digest TEXT NOT NULL,
    bible_pair_digest TEXT NOT NULL,
    phase TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    graph_revision INTEGER NOT NULL DEFAULT 0,
    qa_branch_ref TEXT,
    updated_at_us INTEGER NOT NULL,
    UNIQUE(project_id,feature_id),
    FOREIGN KEY(project_id,brief_id) REFERENCES v1_briefs(project_id,brief_id),
    FOREIGN KEY(project_id,feature_id,active_contract_digest) REFERENCES v1_feature_contracts(project_id,feature_id,contract_digest)
);

CREATE TABLE v1_owner_commands (
    command_id TEXT PRIMARY KEY,
    project_id TEXT,
    feature_id TEXT,
    owner_principal_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at_us INTEGER NOT NULL
);

CREATE TABLE v1_operator_receipts (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    receipt_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    subject_digest TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    issued_at_us INTEGER NOT NULL
);

CREATE TABLE v1_dashboard_projections (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    feature_id TEXT NOT NULL,
    projection_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at_us INTEGER NOT NULL,
    PRIMARY KEY(project_id,feature_id)
);

CREATE TABLE v1_graph_revisions (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    graph_revision INTEGER NOT NULL CHECK(graph_revision > 0),
    graph_digest TEXT NOT NULL,
    contract_digest TEXT NOT NULL,
    bible_pair_digest TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(feature_id,graph_revision)
);

CREATE TABLE v1_tasks (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    task_id TEXT PRIMARY KEY,
    graph_revision INTEGER NOT NULL,
    cabal_id TEXT NOT NULL,
    task_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    output_manifest_ref TEXT,
    budget_limit_units INTEGER NOT NULL CHECK(budget_limit_units >= 0),
    payload_json TEXT NOT NULL,
    UNIQUE(feature_id,task_id),
    FOREIGN KEY(feature_id,graph_revision) REFERENCES v1_graph_revisions(feature_id,graph_revision)
);

CREATE TABLE v1_dependencies (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    CHECK(parent_id <> child_id),
    PRIMARY KEY(parent_id,child_id),
    FOREIGN KEY(feature_id,parent_id) REFERENCES v1_tasks(feature_id,task_id),
    FOREIGN KEY(feature_id,child_id) REFERENCES v1_tasks(feature_id,task_id)
);

CREATE TABLE v1_worker_leases (
    lease_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES v1_tasks(task_id),
    worker_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK(epoch > 0),
    expires_at_us INTEGER NOT NULL,
    fenced INTEGER NOT NULL DEFAULT 0 CHECK(fenced IN (0,1))
);

CREATE TABLE v1_task_attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES v1_tasks(task_id),
    lease_epoch INTEGER NOT NULL,
    input_digest TEXT NOT NULL,
    result_digest TEXT,
    status TEXT NOT NULL,
    started_us INTEGER NOT NULL,
    finished_us INTEGER
);

CREATE TABLE v1_task_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES v1_tasks(task_id),
    lease_epoch INTEGER NOT NULL,
    host_receipt_ref TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    created_at_us INTEGER NOT NULL
);

CREATE TABLE v1_cabals (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    cabal_id TEXT NOT NULL,
    charter_epoch INTEGER NOT NULL CHECK(charter_epoch >= 0),
    scope_digest TEXT NOT NULL,
    bible_pair_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY(project_id,cabal_id)
);

CREATE TABLE v1_memberships (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    cabal_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    role_ref TEXT NOT NULL,
    valid_until_us INTEGER NOT NULL,
    FOREIGN KEY(project_id,cabal_id) REFERENCES v1_cabals(project_id,cabal_id),
    PRIMARY KEY(project_id,cabal_id,worker_id)
);

CREATE TABLE v1_deliberations (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    group_id TEXT PRIMARY KEY,
    cabal_id TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    scope_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_us INTEGER NOT NULL,
    FOREIGN KEY(project_id,cabal_id) REFERENCES v1_cabals(project_id,cabal_id),
    FOREIGN KEY(project_id,feature_id) REFERENCES v1_feature_runs(project_id,feature_id)
);

CREATE TABLE v1_council_cases (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    case_id TEXT PRIMARY KEY,
    bible_pair_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    conflict_digest TEXT NOT NULL
);

CREATE TABLE v1_council_proposals (
    proposal_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES v1_council_cases(case_id),
    author_cabal_id TEXT NOT NULL,
    proposal_digest TEXT NOT NULL,
    compliance_ref TEXT,
    selected INTEGER NOT NULL DEFAULT 0 CHECK(selected IN (0,1))
);

CREATE TABLE v1_council_dissent (
    dissent_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES v1_council_cases(case_id),
    author_id TEXT NOT NULL,
    reason_digest TEXT NOT NULL
);

CREATE TABLE v1_skill_capabilities (
    skill_id TEXT PRIMARY KEY,
    source_digest TEXT NOT NULL,
    qualification_tier TEXT NOT NULL,
    project_scope_id TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE v1_worker_capabilities (
    worker_id TEXT NOT NULL,
    skill_id TEXT NOT NULL REFERENCES v1_skill_capabilities(skill_id),
    verified_evidence_ref TEXT NOT NULL,
    available INTEGER NOT NULL CHECK(available IN (0,1)),
    PRIMARY KEY(worker_id,skill_id)
);

CREATE TABLE v1_cabal_capacity (
    project_id TEXT NOT NULL REFERENCES v1_projects(project_id),
    cabal_id TEXT NOT NULL,
    resource_class TEXT NOT NULL,
    capacity_units INTEGER NOT NULL CHECK(capacity_units >= 0),
    reserved_units INTEGER NOT NULL DEFAULT 0 CHECK(reserved_units >= 0),
    PRIMARY KEY(project_id,cabal_id,resource_class),
    FOREIGN KEY(project_id,cabal_id) REFERENCES v1_cabals(project_id,cabal_id)
);

CREATE TABLE v1_budget_reservations (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    reservation_id TEXT PRIMARY KEY,
    cabal_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    unit_limit INTEGER NOT NULL CHECK(unit_limit >= 0),
    reserved_units INTEGER NOT NULL CHECK(reserved_units >= 0),
    measured_units INTEGER NOT NULL DEFAULT 0 CHECK(measured_units >= 0),
    state TEXT NOT NULL
);

CREATE TABLE v1_budget_settlements (
    settlement_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL REFERENCES v1_budget_reservations(reservation_id),
    provider_receipt_ref TEXT NOT NULL UNIQUE,
    measured_units INTEGER NOT NULL CHECK(measured_units >= 0),
    recorded_at_us INTEGER NOT NULL
);

CREATE TABLE v1_persona_refs (
    worker_id TEXT PRIMARY KEY,
    persona_source_digest TEXT NOT NULL,
    style_only INTEGER NOT NULL DEFAULT 1 CHECK(style_only=1)
);

CREATE TABLE v1_verified_identities (
    principal_id TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    issuer_id TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK(epoch >= 0),
    valid_until_us INTEGER NOT NULL,
    evidence_ref TEXT NOT NULL,
    PRIMARY KEY(principal_id,credential_id)
);

CREATE TABLE v1_host_sessions (
    session_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    authenticated_principal TEXT NOT NULL,
    capability_digest TEXT NOT NULL,
    observed_real INTEGER NOT NULL CHECK(observed_real IN (0,1)),
    session_epoch INTEGER NOT NULL CHECK(session_epoch >= 0)
);

CREATE TABLE v1_host_activations (
    session_id TEXT NOT NULL REFERENCES v1_host_sessions(session_id),
    job_id TEXT NOT NULL,
    host_native_receipt_ref TEXT NOT NULL UNIQUE,
    observed_at_us INTEGER NOT NULL,
    PRIMARY KEY(session_id,job_id)
);

CREATE TABLE v1_provider_capabilities (
    provider_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    verified_state TEXT NOT NULL CHECK(verified_state IN ('NOT_VERIFIED','SUPPORTED','UNSUPPORTED')),
    evidence_ref TEXT,
    PRIMARY KEY(provider_id,capability_name)
);

CREATE TABLE v1_trusted_host_observations (
    observation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES v1_host_sessions(session_id),
    provider_native_ref TEXT NOT NULL UNIQUE,
    observation_digest TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL
);

CREATE TABLE v1_context_bundles (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    bundle_id TEXT PRIMARY KEY,
    bible_pair_digest TEXT NOT NULL,
    source_digest TEXT,
    bundle_digest TEXT NOT NULL UNIQUE,
    content_ref TEXT NOT NULL
);

CREATE TABLE v1_canon_outcomes (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    outcome_id TEXT PRIMARY KEY,
    acceptance_ref TEXT NOT NULL,
    canon_digest TEXT NOT NULL,
    source_commit TEXT,
    status TEXT NOT NULL
);

CREATE TABLE v1_artifact_manifests (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    byte_proof_ref TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(feature_id,task_id) REFERENCES v1_tasks(feature_id,task_id)
);

CREATE TABLE v1_asset_cas (
    content_digest TEXT PRIMARY KEY,
    storage_ref TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    media_type TEXT NOT NULL
);

CREATE TABLE v1_source_leases (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    lease_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    resource_path TEXT NOT NULL,
    resource_path_key TEXT NOT NULL,
    expected_parent_commit TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    expires_at_us INTEGER NOT NULL
);

CREATE TABLE v1_source_commits (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    commit_record_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    expected_parent_sha TEXT NOT NULL,
    provider_receipt_ref TEXT NOT NULL,
    verified INTEGER NOT NULL CHECK(verified IN (0,1))
);

CREATE TABLE v1_effect_intents (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    effect_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    scope_digest TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PREPARED','AUTHORIZED','DISPATCHED','COMMITTED','REJECTED','UNKNOWN_EXTERNAL')),
    target_ref TEXT NOT NULL,
    owner_decision_ref TEXT,
    created_at_us INTEGER NOT NULL
);

CREATE TABLE v1_effect_idempotency (
    provider_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    effect_id TEXT NOT NULL REFERENCES v1_effect_intents(effect_id),
    PRIMARY KEY(provider_id,idempotency_key)
);

CREATE TABLE v1_provider_receipts (
    provider_receipt_id TEXT PRIMARY KEY,
    effect_id TEXT NOT NULL REFERENCES v1_effect_intents(effect_id),
    provider_id TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    observed_state TEXT NOT NULL,
    recorded_us INTEGER NOT NULL
);

CREATE TABLE v1_archive_refs (
    archive_id TEXT PRIMARY KEY,
    feature_id TEXT,
    content_digest TEXT NOT NULL,
    storage_ref TEXT NOT NULL,
    created_at_us INTEGER NOT NULL
);

CREATE TABLE v1_quality_requirements (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    requirement_id TEXT NOT NULL,
    deliverable_id TEXT NOT NULL,
    acceptance_check_id TEXT NOT NULL,
    requirement_digest TEXT NOT NULL,
    PRIMARY KEY(feature_id,requirement_id,acceptance_check_id)
);

CREATE TABLE v1_test_executions (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    execution_id TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    test_id TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    log_digest TEXT NOT NULL,
    adapter_digest TEXT NOT NULL,
    toolchain_digest TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    runner_principal_id TEXT NOT NULL,
    observed_at_us INTEGER NOT NULL
);

CREATE TABLE v1_review_assignments (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    review_id TEXT PRIMARY KEY,
    task_id TEXT,
    subject_digest TEXT NOT NULL,
    reviewer_principal_id TEXT NOT NULL,
    author_lineage_digest TEXT NOT NULL,
    state TEXT NOT NULL
);

CREATE TABLE v1_review_verdicts (
    verdict_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES v1_review_assignments(review_id),
    subject_digest TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('ACCEPT','REJECT','INCONCLUSIVE')),
    reviewer_principal_id TEXT NOT NULL
);

CREATE TABLE v1_qa_ready_receipts (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    receipt_id TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    contract_digest TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    quality_digest TEXT NOT NULL,
    quality_signer TEXT NOT NULL,
    created_at_us INTEGER NOT NULL,
    UNIQUE(feature_id,commit_sha)
);

CREATE TABLE v1_independent_qa (
    feature_id TEXT NOT NULL REFERENCES v1_feature_runs(feature_id),
    qa_id TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    reviewer_principal_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('PASS','FAIL','INCONCLUSIVE')),
    evidence_digest TEXT NOT NULL,
    recorded_at_us INTEGER NOT NULL
);

-- Mechanically important indices and partial uniqueness.
CREATE UNIQUE INDEX v1_bible_one_active_kind ON v1_bible_editions(project_id,kind) WHERE active=1;
CREATE UNIQUE INDEX v1_source_one_lease ON v1_source_leases(repo,branch,resource_path_key) WHERE active=1;
CREATE INDEX v1_source_active_path_prefix ON v1_source_leases(repo,branch,active,resource_path_key);
CREATE UNIQUE INDEX v1_one_active_task_lease ON v1_worker_leases(task_id) WHERE fenced=0;
CREATE INDEX v1_ready_task_lookup ON v1_tasks(feature_id,graph_revision,state);
CREATE INDEX v1_job_attempt_lookup ON v1_task_attempts(task_id,lease_epoch);
CREATE INDEX v1_effects_pending ON v1_effect_intents(state,created_at_us);
CREATE INDEX v1_feature_run_status ON v1_feature_runs(project_id,phase);
CREATE INDEX v1_review_sha ON v1_test_executions(feature_id,commit_sha,test_id);
CREATE INDEX v1_bible_clauses_scope ON v1_bible_clauses(edition_id,scope,kind);
CREATE INDEX v1_host_session_jobs ON v1_host_activations(session_id,observed_at_us);
