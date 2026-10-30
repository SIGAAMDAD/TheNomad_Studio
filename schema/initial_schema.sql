-- Initial greenfield Studio schema, from empty database. No historical migration.
-- Production Python driver: APSW only. The validation script uses stdlib sqlite3 for offline SQL proof.
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;

CREATE TABLE principals(principal_id TEXT PRIMARY KEY, auth_issuer TEXT NOT NULL, auth_subject TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('human','service','worker')), UNIQUE(auth_issuer,auth_subject)) STRICT;
CREATE TABLE projects(project_id TEXT PRIMARY KEY, name TEXT NOT NULL, charter_digest TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0), state TEXT NOT NULL CHECK(state IN ('draft','active','archived'))) STRICT;
CREATE TABLE project_grants(project_id TEXT NOT NULL,principal_id TEXT NOT NULL,grant_code TEXT NOT NULL,PRIMARY KEY(project_id,principal_id,grant_code),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(principal_id) REFERENCES principals(principal_id)) STRICT;
CREATE TABLE project_profiles(project_id TEXT NOT NULL,profile_id TEXT NOT NULL,config_digest TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>0),PRIMARY KEY(project_id,profile_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE project_repositories(project_id TEXT NOT NULL,repo_id TEXT NOT NULL,provider_key TEXT NOT NULL,remote_ref TEXT NOT NULL,expected_sha TEXT,PRIMARY KEY(project_id,repo_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE blobs(content_digest TEXT PRIMARY KEY,byte_size INTEGER NOT NULL CHECK(byte_size>=0),store_key TEXT NOT NULL UNIQUE, durability TEXT NOT NULL CHECK(durability IN ('staged','verified','missing'))) STRICT;
CREATE TABLE project_blobs(project_id TEXT NOT NULL,content_digest TEXT NOT NULL,purpose TEXT NOT NULL,PRIMARY KEY(project_id,content_digest,purpose),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(content_digest) REFERENCES blobs(content_digest)) STRICT;

CREATE TABLE package_revisions(package_digest TEXT PRIMARY KEY,package_id TEXT NOT NULL,package_version TEXT NOT NULL,manifest_digest TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('engine','discipline','genre','toolchain','template','tool','other')),state TEXT NOT NULL CHECK(state IN ('installed','disabled')),UNIQUE(package_id,package_version,package_digest)) STRICT;
CREATE TABLE package_dependencies(package_digest TEXT NOT NULL,required_digest TEXT NOT NULL,PRIMARY KEY(package_digest,required_digest),FOREIGN KEY(package_digest) REFERENCES package_revisions(package_digest),FOREIGN KEY(required_digest) REFERENCES package_revisions(package_digest),CHECK(package_digest<>required_digest)) STRICT;
CREATE TABLE tool_entrypoints(package_digest TEXT NOT NULL,entrypoint_id TEXT NOT NULL,executable_digest TEXT NOT NULL,args_schema_digest TEXT NOT NULL,output_schema_digest TEXT NOT NULL,required_isolation TEXT NOT NULL,PRIMARY KEY(package_digest,entrypoint_id),FOREIGN KEY(package_digest) REFERENCES package_revisions(package_digest)) STRICT;
CREATE TABLE specialist_roles(role_id TEXT PRIMARY KEY,role_revision TEXT NOT NULL,role_manifest_digest TEXT NOT NULL,department_id TEXT, state TEXT NOT NULL CHECK(state IN ('ready','unavailable','disabled')),FOREIGN KEY(department_id) REFERENCES departments(department_id)) STRICT;
CREATE TABLE departments(department_id TEXT PRIMARY KEY,name TEXT NOT NULL) STRICT;
CREATE TABLE project_packages(project_id TEXT NOT NULL,package_digest TEXT NOT NULL,config_digest TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),PRIMARY KEY(project_id,package_digest),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(package_digest) REFERENCES package_revisions(package_digest)) STRICT;
CREATE TABLE tool_grants(project_id TEXT NOT NULL,grant_id TEXT NOT NULL,package_digest TEXT NOT NULL,entrypoint_id TEXT NOT NULL,workspace_id TEXT NOT NULL,grant_digest TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),PRIMARY KEY(project_id,grant_id),FOREIGN KEY(project_id,package_digest) REFERENCES project_packages(project_id,package_digest),FOREIGN KEY(package_digest,entrypoint_id) REFERENCES tool_entrypoints(package_digest,entrypoint_id)) STRICT;

CREATE TABLE bibles(project_id TEXT NOT NULL,bible_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('design','technical')),title TEXT NOT NULL,PRIMARY KEY(project_id,bible_id),UNIQUE(project_id,bible_id,kind),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE bible_editions(project_id TEXT NOT NULL,bible_id TEXT NOT NULL,edition_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('design','technical')),content_digest TEXT NOT NULL,manifest_digest TEXT NOT NULL,owner_decision_id TEXT NOT NULL, publication_effect_digest TEXT NOT NULL,published_at TEXT NOT NULL,PRIMARY KEY(project_id,bible_id,edition_id),UNIQUE(project_id,owner_decision_id),UNIQUE(project_id,bible_id,edition_id,kind),FOREIGN KEY(project_id,bible_id,kind) REFERENCES bibles(project_id,bible_id,kind),FOREIGN KEY(content_digest) REFERENCES blobs(content_digest),FOREIGN KEY(manifest_digest) REFERENCES blobs(content_digest),FOREIGN KEY(project_id,owner_decision_id) REFERENCES owner_decisions(project_id,decision_id)) STRICT;
CREATE TABLE active_bible_editions(project_id TEXT NOT NULL,bible_id TEXT NOT NULL,edition_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('design','technical')),PRIMARY KEY(project_id,bible_id),FOREIGN KEY(project_id,bible_id,edition_id,kind) REFERENCES bible_editions(project_id,bible_id,edition_id,kind)) STRICT;
CREATE TABLE canon_snapshots(project_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,design_bible_id TEXT NOT NULL,design_edition_id TEXT NOT NULL,design_kind TEXT NOT NULL DEFAULT 'design' CHECK(design_kind='design'),technical_bible_id TEXT NOT NULL,technical_edition_id TEXT NOT NULL,technical_kind TEXT NOT NULL DEFAULT 'technical' CHECK(technical_kind='technical'),policy_digest TEXT NOT NULL,manifest_digest TEXT NOT NULL,PRIMARY KEY(project_id,snapshot_id),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(project_id,design_bible_id,design_edition_id,design_kind) REFERENCES bible_editions(project_id,bible_id,edition_id,kind),FOREIGN KEY(project_id,technical_bible_id,technical_edition_id,technical_kind) REFERENCES bible_editions(project_id,bible_id,edition_id,kind)) STRICT;
CREATE TABLE bible_clauses(project_id TEXT NOT NULL,bible_id TEXT NOT NULL,edition_id TEXT NOT NULL,clause_key TEXT NOT NULL,classification TEXT NOT NULL CHECK(classification IN ('requirement','guideline','open_area')),text_digest TEXT NOT NULL,scope_digest TEXT NOT NULL,provenance_digest TEXT NOT NULL,PRIMARY KEY(project_id,bible_id,edition_id,clause_key),FOREIGN KEY(project_id,bible_id,edition_id) REFERENCES bible_editions(project_id,bible_id,edition_id)) STRICT;
CREATE TABLE canon_facts(project_id TEXT NOT NULL,fact_id TEXT NOT NULL,subject_key TEXT NOT NULL,predicate TEXT NOT NULL,value_digest TEXT NOT NULL,evidence_digest TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('draft','verified','superseded','disputed')),snapshot_id TEXT,PRIMARY KEY(project_id,fact_id),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id)) STRICT;
CREATE TABLE bible_publication_intents(project_id TEXT NOT NULL,intent_id TEXT NOT NULL,bible_id TEXT NOT NULL,content_digest TEXT NOT NULL,manifest_digest TEXT NOT NULL,expected_edition_id TEXT,request_id TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('staging','verified','committed','failed','unknown')),PRIMARY KEY(project_id,intent_id),UNIQUE(project_id,request_id),FOREIGN KEY(project_id,bible_id) REFERENCES bibles(project_id,bible_id),FOREIGN KEY(content_digest) REFERENCES blobs(content_digest),FOREIGN KEY(manifest_digest) REFERENCES blobs(content_digest)) STRICT;

CREATE TABLE cabals(project_id TEXT NOT NULL,cabal_id TEXT NOT NULL,name TEXT NOT NULL,charter_digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('active','paused','closed')),PRIMARY KEY(project_id,cabal_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE cabal_members(project_id TEXT NOT NULL,cabal_id TEXT NOT NULL,member_id TEXT NOT NULL,role_id TEXT NOT NULL,permission_digest TEXT NOT NULL,PRIMARY KEY(project_id,cabal_id,member_id),FOREIGN KEY(project_id,cabal_id) REFERENCES cabals(project_id,cabal_id),FOREIGN KEY(role_id) REFERENCES specialist_roles(role_id)) STRICT;
CREATE TABLE deliberations(project_id TEXT NOT NULL,deliberation_id TEXT NOT NULL,cabal_id TEXT NOT NULL,subject_digest TEXT NOT NULL,record_digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('open','resolved','escalated')),PRIMARY KEY(project_id,deliberation_id),FOREIGN KEY(project_id,cabal_id) REFERENCES cabals(project_id,cabal_id)) STRICT;

CREATE TABLE features(project_id TEXT NOT NULL,feature_id TEXT NOT NULL,brief_digest TEXT NOT NULL,snapshot_id TEXT NOT NULL,source_revision_id TEXT,revision INTEGER NOT NULL DEFAULT 1,state TEXT NOT NULL CHECK(state IN ('proposed','planned','running','review','completed','blocked','canceled')),PRIMARY KEY(project_id,feature_id),UNIQUE(project_id,feature_id,snapshot_id),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id)) STRICT;
CREATE TABLE tasks(project_id TEXT NOT NULL,task_id TEXT NOT NULL,feature_id TEXT NOT NULL,cabal_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,role_id TEXT,acceptance_digest TEXT NOT NULL,priority INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),state TEXT NOT NULL CHECK(state IN ('pending','ready','leased','running','submitted','review','accepted','failed','blocked','canceled')),PRIMARY KEY(project_id,task_id),FOREIGN KEY(project_id,feature_id,snapshot_id) REFERENCES features(project_id,feature_id,snapshot_id),FOREIGN KEY(project_id,cabal_id) REFERENCES cabals(project_id,cabal_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id),FOREIGN KEY(role_id) REFERENCES specialist_roles(role_id)) STRICT;
CREATE TABLE task_dependencies(project_id TEXT NOT NULL,task_id TEXT NOT NULL,depends_on_task_id TEXT NOT NULL,PRIMARY KEY(project_id,task_id,depends_on_task_id),CHECK(task_id<>depends_on_task_id),FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,task_id),FOREIGN KEY(project_id,depends_on_task_id) REFERENCES tasks(project_id,task_id)) STRICT;
CREATE TABLE leases(project_id TEXT NOT NULL,task_id TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>0),worker_id TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','expired','released')),PRIMARY KEY(project_id,task_id,generation),FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,task_id)) STRICT;
-- Stable browser conversation / response-shift identities. A lease belongs to an assignment,
-- never to the durable conversation. A prepared shift is not presumed executing.
CREATE TABLE worker_conversations(
 project_id TEXT NOT NULL,worker_id TEXT NOT NULL,principal_id TEXT NOT NULL,
 role_id TEXT NOT NULL,browser_chat_ref TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('registered','disabled','retired')),
 PRIMARY KEY(project_id,worker_id),UNIQUE(project_id,browser_chat_ref),
 FOREIGN KEY(project_id) REFERENCES projects(project_id),
 FOREIGN KEY(principal_id) REFERENCES principals(principal_id),
 FOREIGN KEY(role_id) REFERENCES specialist_roles(role_id)) STRICT;
CREATE TABLE worker_shifts(
 project_id TEXT NOT NULL,shift_id TEXT NOT NULL,worker_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_hello','active','stopping','finished','interrupted','unknown')),
 created_at TEXT NOT NULL,observed_at TEXT,hello_commit_sha TEXT,hello_digest TEXT,
 finished_at TEXT,final_commit_sha TEXT,final_digest TEXT,
 PRIMARY KEY(project_id,shift_id),UNIQUE(project_id,shift_id,worker_id),
 FOREIGN KEY(project_id,worker_id) REFERENCES worker_conversations(project_id,worker_id),
 CHECK((state <> 'active') OR (hello_commit_sha IS NOT NULL AND hello_digest IS NOT NULL))) STRICT;
CREATE UNIQUE INDEX one_open_shift_per_worker ON worker_shifts(project_id,worker_id)
 WHERE state IN ('awaiting_hello','active','stopping');
CREATE TABLE shift_assignments(
 project_id TEXT NOT NULL,assignment_id TEXT NOT NULL,shift_id TEXT NOT NULL,worker_id TEXT NOT NULL,
 task_id TEXT NOT NULL,lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
 state TEXT NOT NULL CHECK(state IN ('assigned','observed','submitted','accepted','failed','canceled')),
 PRIMARY KEY(project_id,assignment_id),UNIQUE(project_id,task_id,lease_generation),
 FOREIGN KEY(project_id,shift_id,worker_id) REFERENCES worker_shifts(project_id,shift_id,worker_id),
 FOREIGN KEY(project_id,task_id,lease_generation) REFERENCES leases(project_id,task_id,generation)) STRICT;
CREATE TABLE command_outbox(
 project_id TEXT NOT NULL,command_id TEXT NOT NULL,worker_id TEXT NOT NULL,shift_id TEXT NOT NULL,
 assignment_id TEXT,kind TEXT NOT NULL CHECK(kind IN ('ASSIGN','STOP')),
 envelope_digest TEXT NOT NULL,envelope_json TEXT NOT NULL,remote_path TEXT NOT NULL,
 remote_commit_sha TEXT,created_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','published','unknown','observed','completed','canceled')),
 PRIMARY KEY(project_id,command_id),UNIQUE(project_id,remote_path),
 FOREIGN KEY(project_id,shift_id,worker_id) REFERENCES worker_shifts(project_id,shift_id,worker_id),
 FOREIGN KEY(project_id,assignment_id) REFERENCES shift_assignments(project_id,assignment_id),
 CHECK(state NOT IN ('published','observed','completed') OR remote_commit_sha IS NOT NULL),
 CHECK((kind='ASSIGN' AND assignment_id IS NOT NULL) OR (kind='STOP' AND assignment_id IS NULL))) STRICT;
CREATE TABLE command_receipts(
 project_id TEXT NOT NULL,receipt_id TEXT NOT NULL,command_id TEXT NOT NULL,
 worker_id TEXT NOT NULL,shift_id TEXT NOT NULL,kind TEXT NOT NULL,
 result_digest TEXT NOT NULL,remote_commit_sha TEXT NOT NULL,
 observed_at TEXT NOT NULL,PRIMARY KEY(project_id,receipt_id),
 FOREIGN KEY(project_id,command_id) REFERENCES command_outbox(project_id,command_id),
 FOREIGN KEY(project_id,shift_id,worker_id) REFERENCES worker_shifts(project_id,shift_id,worker_id)) STRICT;
CREATE INDEX idx_command_outbox_ready ON command_outbox(project_id,state,created_at);
CREATE TRIGGER command_envelope_immutable BEFORE UPDATE OF
 project_id,command_id,worker_id,shift_id,assignment_id,kind,envelope_digest,envelope_json,remote_path,created_at ON command_outbox
 BEGIN SELECT RAISE(ABORT,'command envelope immutable'); END;
CREATE TRIGGER command_receipt_matches_shift BEFORE INSERT ON command_receipts
 BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM command_outbox c WHERE c.project_id=NEW.project_id
 AND c.command_id=NEW.command_id AND c.worker_id=NEW.worker_id AND c.shift_id=NEW.shift_id
 AND c.state IN ('published','observed','completed')) THEN RAISE(ABORT,'receipt not bound to published command') END; END;
CREATE TRIGGER shift_assignment_matches_lease BEFORE INSERT ON shift_assignments
 BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM leases l WHERE l.project_id=NEW.project_id
 AND l.task_id=NEW.task_id AND l.generation=NEW.lease_generation AND l.worker_id=NEW.worker_id)
 THEN RAISE(ABORT,'shift assignment worker differs from lease') END; END;
CREATE TABLE sessions(project_id TEXT NOT NULL,session_id TEXT NOT NULL,author_principal_id TEXT NOT NULL,task_id TEXT NOT NULL,lease_generation INTEGER NOT NULL,session_mode TEXT NOT NULL DEFAULT 'manual_chatgpt_browser' CHECK(session_mode='manual_chatgpt_browser'),browser_chat_ref TEXT,package_digest TEXT NOT NULL,snapshot_id TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('prepared','awaiting_manual_start','active_observed','quiet','stop_requested','completed_verified','failed_verified','unknown')),PRIMARY KEY(project_id,session_id),FOREIGN KEY(project_id,task_id,lease_generation) REFERENCES leases(project_id,task_id,generation),FOREIGN KEY(author_principal_id) REFERENCES principals(principal_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id),FOREIGN KEY(package_digest) REFERENCES package_revisions(package_digest)) STRICT;
CREATE TABLE checkpoints(project_id TEXT NOT NULL,checkpoint_id TEXT NOT NULL,session_id TEXT NOT NULL,context_digest TEXT NOT NULL,relay_cursor TEXT,recorded_at TEXT NOT NULL,PRIMARY KEY(project_id,checkpoint_id),FOREIGN KEY(project_id,session_id) REFERENCES sessions(project_id,session_id)) STRICT;
CREATE TABLE relay_messages(
 project_id TEXT NOT NULL,message_id TEXT NOT NULL,channel_id TEXT NOT NULL,
 from_session_id TEXT,to_session_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('HELLO','ASSIGN','NOTE','QUESTION','ANSWER','EVIDENCE','REVIEW','HANDOFF','STOP','STATUS')),
 task_id TEXT NOT NULL,lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
 canon_snapshot_id TEXT NOT NULL,contract_digest TEXT NOT NULL,envelope_digest TEXT NOT NULL,
 payload_digest TEXT NOT NULL,remote_path TEXT NOT NULL,remote_commit_sha TEXT,
 delivery_state TEXT NOT NULL CHECK(delivery_state IN ('queued','published','observed','unknown','rejected')),ack_at TEXT,
 PRIMARY KEY(project_id,message_id),UNIQUE(project_id,message_id,channel_id),UNIQUE(project_id,remote_path),
 FOREIGN KEY(project_id,channel_id) REFERENCES relay_channels(project_id,channel_id),
 FOREIGN KEY(project_id,from_session_id) REFERENCES sessions(project_id,session_id),
 FOREIGN KEY(project_id,to_session_id) REFERENCES sessions(project_id,session_id),
 FOREIGN KEY(project_id,task_id,lease_generation) REFERENCES leases(project_id,task_id,generation),
 FOREIGN KEY(project_id,canon_snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id)
) STRICT;
CREATE TABLE tool_operations(project_id TEXT NOT NULL,operation_id TEXT NOT NULL,session_id TEXT NOT NULL,grant_id TEXT NOT NULL,entrypoint_id TEXT NOT NULL,lease_generation INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('prepared','running','observed','unknown','failed','canceled')),receipt_digest TEXT,PRIMARY KEY(project_id,operation_id),FOREIGN KEY(project_id,session_id) REFERENCES sessions(project_id,session_id),FOREIGN KEY(project_id,grant_id) REFERENCES tool_grants(project_id,grant_id)) STRICT;

CREATE TABLE source_revisions(project_id TEXT NOT NULL,revision_id TEXT NOT NULL,repo_id TEXT NOT NULL,commit_sha TEXT NOT NULL,tree_digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('staged','canonical','superseded')),PRIMARY KEY(project_id,revision_id),UNIQUE(project_id,repo_id,commit_sha),FOREIGN KEY(project_id,repo_id) REFERENCES project_repositories(project_id,repo_id)) STRICT;
CREATE TABLE artifacts(project_id TEXT NOT NULL,artifact_id TEXT NOT NULL,kind TEXT NOT NULL,content_digest TEXT NOT NULL,manifest_digest TEXT NOT NULL,producer_session_id TEXT,source_revision_id TEXT,snapshot_id TEXT,PRIMARY KEY(project_id,artifact_id),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(content_digest) REFERENCES blobs(content_digest),FOREIGN KEY(manifest_digest) REFERENCES blobs(content_digest),FOREIGN KEY(project_id,producer_session_id) REFERENCES sessions(project_id,session_id),FOREIGN KEY(project_id,source_revision_id) REFERENCES source_revisions(project_id,revision_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id)) STRICT;
CREATE TABLE submissions(project_id TEXT NOT NULL,submission_id TEXT NOT NULL,session_id TEXT NOT NULL,task_id TEXT NOT NULL,lease_generation INTEGER NOT NULL,source_revision_id TEXT,manifest_digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('staged','submitted','qa_passed','qa_failed','integrated','rejected')),PRIMARY KEY(project_id,submission_id),FOREIGN KEY(project_id,session_id) REFERENCES sessions(project_id,session_id),FOREIGN KEY(project_id,task_id,lease_generation) REFERENCES leases(project_id,task_id,generation),FOREIGN KEY(project_id,source_revision_id) REFERENCES source_revisions(project_id,revision_id)) STRICT;
CREATE TABLE submission_artifacts(project_id TEXT NOT NULL,submission_id TEXT NOT NULL,artifact_id TEXT NOT NULL,PRIMARY KEY(project_id,submission_id,artifact_id),FOREIGN KEY(project_id,submission_id) REFERENCES submissions(project_id,submission_id),FOREIGN KEY(project_id,artifact_id) REFERENCES artifacts(project_id,artifact_id)) STRICT;
CREATE TABLE qa_reviews(project_id TEXT NOT NULL,review_id TEXT NOT NULL,submission_id TEXT NOT NULL,reviewer_principal_id TEXT NOT NULL,author_session_id TEXT NOT NULL,source_revision_id TEXT,acceptance_digest TEXT NOT NULL,verdict TEXT NOT NULL CHECK(verdict IN ('pending','pass','fail','incomplete')),evidence_digest TEXT,PRIMARY KEY(project_id,review_id),FOREIGN KEY(project_id,submission_id) REFERENCES submissions(project_id,submission_id),FOREIGN KEY(reviewer_principal_id) REFERENCES principals(principal_id),FOREIGN KEY(project_id,author_session_id) REFERENCES sessions(project_id,session_id),FOREIGN KEY(project_id,source_revision_id) REFERENCES source_revisions(project_id,revision_id)) STRICT;
CREATE TABLE qa_receipts(project_id TEXT NOT NULL,review_id TEXT NOT NULL,validator_digest TEXT NOT NULL,artifact_id TEXT NOT NULL,evidence_digest TEXT NOT NULL,PRIMARY KEY(project_id,review_id,validator_digest,artifact_id),FOREIGN KEY(project_id,review_id) REFERENCES qa_reviews(project_id,review_id),FOREIGN KEY(project_id,artifact_id) REFERENCES artifacts(project_id,artifact_id)) STRICT;
CREATE TABLE ci_runs(project_id TEXT NOT NULL,ci_id TEXT NOT NULL,source_revision_id TEXT NOT NULL,provider_key TEXT NOT NULL,remote_run_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','passed','failed','unknown')),evidence_digest TEXT,PRIMARY KEY(project_id,ci_id),FOREIGN KEY(project_id,source_revision_id) REFERENCES source_revisions(project_id,revision_id)) STRICT;
CREATE TABLE integration_intents(project_id TEXT NOT NULL,intent_id TEXT NOT NULL,submission_id TEXT NOT NULL,expected_revision_id TEXT NOT NULL,staged_revision_id TEXT,new_revision_id TEXT,state TEXT NOT NULL CHECK(state IN ('prepared','staged','qa_checked','promoted','conflict','failed','unknown')),request_id TEXT NOT NULL,PRIMARY KEY(project_id,intent_id),UNIQUE(project_id,request_id),FOREIGN KEY(project_id,submission_id) REFERENCES submissions(project_id,submission_id),FOREIGN KEY(project_id,expected_revision_id) REFERENCES source_revisions(project_id,revision_id),FOREIGN KEY(project_id,new_revision_id) REFERENCES source_revisions(project_id,revision_id)) STRICT;

CREATE TABLE budgets(project_id TEXT NOT NULL,budget_id TEXT NOT NULL,resource_key TEXT NOT NULL,limit_amount INTEGER NOT NULL CHECK(limit_amount>=0),reserved_amount INTEGER NOT NULL DEFAULT 0 CHECK(reserved_amount>=0),spent_amount INTEGER NOT NULL DEFAULT 0 CHECK(spent_amount>=0),CHECK(spent_amount+reserved_amount<=limit_amount),PRIMARY KEY(project_id,budget_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE reservations(project_id TEXT NOT NULL,reservation_id TEXT NOT NULL,budget_id TEXT NOT NULL,operation_id TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),state TEXT NOT NULL CHECK(state IN ('reserved','consumed','released')),PRIMARY KEY(project_id,reservation_id),FOREIGN KEY(project_id,budget_id) REFERENCES budgets(project_id,budget_id)) STRICT;
CREATE TABLE usage_receipts(project_id TEXT NOT NULL,usage_id TEXT NOT NULL,reservation_id TEXT,provider_ref TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>=0),evidence_digest TEXT NOT NULL,PRIMARY KEY(project_id,usage_id),UNIQUE(project_id,provider_ref),FOREIGN KEY(project_id,reservation_id) REFERENCES reservations(project_id,reservation_id)) STRICT;

CREATE TABLE owner_decisions(project_id TEXT NOT NULL,decision_id TEXT NOT NULL,subject_type TEXT NOT NULL CHECK(subject_type IN ('project','bible','feature','task','cabal','artifact','release')),subject_id TEXT NOT NULL,action TEXT NOT NULL,effect_digest TEXT NOT NULL,snapshot_id TEXT,verified_owner_principal_id TEXT NOT NULL,proof_digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('approved','rejected','revoked')),PRIMARY KEY(project_id,decision_id),UNIQUE(project_id,decision_id,subject_type,subject_id,action,effect_digest),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id),FOREIGN KEY(verified_owner_principal_id) REFERENCES principals(principal_id)) STRICT;
CREATE TABLE effect_intents(project_id TEXT NOT NULL,effect_id TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,action TEXT NOT NULL,effect_digest TEXT NOT NULL,snapshot_id TEXT,decision_id TEXT,approval_required INTEGER NOT NULL CHECK(approval_required IN (0,1)),state TEXT NOT NULL CHECK(state IN ('prepared','dispatched','observed','unknown','failed')),provider_key TEXT NOT NULL,remote_operation_id TEXT,request_id TEXT NOT NULL,PRIMARY KEY(project_id,effect_id),UNIQUE(project_id,request_id),CHECK(approval_required=0 OR decision_id IS NOT NULL),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id),FOREIGN KEY(project_id,decision_id,subject_type,subject_id,action,effect_digest) REFERENCES owner_decisions(project_id,decision_id,subject_type,subject_id,action,effect_digest)) STRICT;
CREATE TABLE approval_consumptions(project_id TEXT NOT NULL,decision_id TEXT NOT NULL,effect_id TEXT NOT NULL,consumed_at TEXT NOT NULL,PRIMARY KEY(project_id,decision_id),UNIQUE(project_id,effect_id),FOREIGN KEY(project_id,decision_id) REFERENCES owner_decisions(project_id,decision_id),FOREIGN KEY(project_id,effect_id) REFERENCES effect_intents(project_id,effect_id)) STRICT;
CREATE TABLE bootstrap_idempotency(principal_id TEXT NOT NULL,request_id TEXT NOT NULL,payload_digest TEXT NOT NULL,result_project_id TEXT,status TEXT NOT NULL CHECK(status IN ('started','finished','unknown')),PRIMARY KEY(principal_id,request_id),FOREIGN KEY(principal_id) REFERENCES principals(principal_id),FOREIGN KEY(result_project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE idempotency(project_id TEXT NOT NULL,action_owner TEXT NOT NULL,request_id TEXT NOT NULL,payload_digest TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('started','finished','unknown')),result_digest TEXT,PRIMARY KEY(project_id,action_owner,request_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE workforce_command_results(
 project_id TEXT NOT NULL,action_owner TEXT NOT NULL,request_id TEXT NOT NULL,
 result_json TEXT NOT NULL,PRIMARY KEY(project_id,action_owner,request_id),
 FOREIGN KEY(project_id,action_owner,request_id)
 REFERENCES idempotency(project_id,action_owner,request_id)) STRICT;
CREATE TRIGGER workforce_result_cannot_update BEFORE UPDATE ON workforce_command_results
BEGIN SELECT RAISE(ABORT,'workforce result immutable'); END;
CREATE TRIGGER workforce_result_cannot_delete BEFORE DELETE ON workforce_command_results
BEGIN SELECT RAISE(ABORT,'workforce result immutable'); END;
CREATE TRIGGER idempotency_finished_immutable BEFORE UPDATE ON idempotency
WHEN OLD.status='finished'
BEGIN SELECT RAISE(ABORT,'finished idempotency immutable'); END;
CREATE TRIGGER idempotency_finished_cannot_delete BEFORE DELETE ON idempotency
WHEN OLD.status='finished'
BEGIN SELECT RAISE(ABORT,'finished idempotency immutable'); END;

CREATE TABLE audit_events(project_id TEXT NOT NULL,event_seq INTEGER NOT NULL,event_type TEXT NOT NULL,subject_type TEXT,subject_id TEXT,payload_digest TEXT NOT NULL,actor_principal_id TEXT NOT NULL,PRIMARY KEY(project_id,event_seq),FOREIGN KEY(project_id) REFERENCES projects(project_id),FOREIGN KEY(actor_principal_id) REFERENCES principals(principal_id)) STRICT;
CREATE TABLE external_accounts(project_id TEXT NOT NULL,account_id TEXT NOT NULL,provider_key TEXT NOT NULL,credential_ref TEXT NOT NULL,enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),PRIMARY KEY(project_id,account_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE releases(project_id TEXT NOT NULL,release_id TEXT NOT NULL,source_revision_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,release_digest TEXT NOT NULL,decision_id TEXT,state TEXT NOT NULL CHECK(state IN ('prepared','published','failed','unknown')),PRIMARY KEY(project_id,release_id),FOREIGN KEY(project_id,source_revision_id) REFERENCES source_revisions(project_id,revision_id),FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id),FOREIGN KEY(project_id,decision_id) REFERENCES owner_decisions(project_id,decision_id)) STRICT;
CREATE TABLE release_artifacts(project_id TEXT NOT NULL,release_id TEXT NOT NULL,artifact_id TEXT NOT NULL,PRIMARY KEY(project_id,release_id,artifact_id),FOREIGN KEY(project_id,release_id) REFERENCES releases(project_id,release_id),FOREIGN KEY(project_id,artifact_id) REFERENCES artifacts(project_id,artifact_id)) STRICT;
CREATE TABLE notifications(project_id TEXT NOT NULL,notification_id TEXT NOT NULL,kind TEXT NOT NULL,subject_id TEXT NOT NULL,delivery_state TEXT NOT NULL CHECK(delivery_state IN ('queued','sent','failed','unknown')),acknowledged_at TEXT, ack_principal_id TEXT,PRIMARY KEY(project_id,notification_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE oversight_cases(project_id TEXT NOT NULL,case_id TEXT NOT NULL,subject_id TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('open','investigating','resolved')),evidence_digest TEXT,PRIMARY KEY(project_id,case_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE reports(project_id TEXT NOT NULL,report_id TEXT NOT NULL,report_digest TEXT NOT NULL,delivery_state TEXT NOT NULL CHECK(delivery_state IN ('queued','sent','failed','unknown')),PRIMARY KEY(project_id,report_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;

CREATE INDEX idx_tasks_ready ON tasks(project_id,state,priority DESC,task_id);
CREATE INDEX idx_leases_live ON leases(project_id,status,expires_at);
CREATE INDEX idx_effects_pending ON effect_intents(project_id,state,effect_id);
CREATE INDEX idx_events_cursor ON audit_events(project_id,event_seq);
CREATE INDEX idx_sessions_active ON sessions(project_id,state,task_id);
CREATE INDEX idx_review_submission ON qa_reviews(project_id,submission_id,verdict);
CREATE INDEX idx_artifacts_digest ON artifacts(project_id,content_digest);
CREATE INDEX idx_canon_current ON active_bible_editions(project_id,kind,bible_id);
CREATE INDEX idx_budget_usage ON reservations(project_id,budget_id,state);

-- Subject existence is resolved against real domain records, not a forgeable generic registry.
CREATE TRIGGER decision_subject_exists BEFORE INSERT ON owner_decisions BEGIN
 SELECT CASE WHEN NEW.subject_type='project' AND NOT EXISTS (SELECT 1 FROM projects WHERE project_id=NEW.project_id AND project_id=NEW.subject_id) THEN RAISE(ABORT,'invalid owner subject') END;
 SELECT CASE WHEN NEW.subject_type='bible' AND NOT EXISTS (SELECT 1 FROM bibles WHERE project_id=NEW.project_id AND bible_id=NEW.subject_id) THEN RAISE(ABORT,'invalid owner subject') END;
 SELECT CASE WHEN NEW.subject_type='feature' AND NOT EXISTS (SELECT 1 FROM features WHERE project_id=NEW.project_id AND feature_id=NEW.subject_id) THEN RAISE(ABORT,'invalid owner subject') END;
 SELECT CASE WHEN NEW.subject_type='task' AND NOT EXISTS (SELECT 1 FROM tasks WHERE project_id=NEW.project_id AND task_id=NEW.subject_id) THEN RAISE(ABORT,'invalid owner subject') END;
 SELECT CASE WHEN NEW.subject_type='cabal' AND NOT EXISTS (SELECT 1 FROM cabals WHERE project_id=NEW.project_id AND cabal_id=NEW.subject_id) THEN RAISE(ABORT,'invalid owner subject') END;
 SELECT CASE WHEN NEW.subject_type='artifact' AND NOT EXISTS (SELECT 1 FROM artifacts WHERE project_id=NEW.project_id AND artifact_id=NEW.subject_id) THEN RAISE(ABORT,'invalid owner subject') END;
 SELECT CASE WHEN NEW.subject_type='release' AND NOT EXISTS (SELECT 1 FROM releases WHERE project_id=NEW.project_id AND release_id=NEW.subject_id) THEN RAISE(ABORT,'invalid owner subject') END;
END;
CREATE TRIGGER reject_invalid_decision_use BEFORE INSERT ON approval_consumptions BEGIN
 SELECT CASE WHEN NOT EXISTS (
   SELECT 1 FROM owner_decisions d JOIN effect_intents e ON (d.project_id=e.project_id AND d.decision_id=e.decision_id)
   WHERE d.project_id=NEW.project_id AND d.decision_id=NEW.decision_id AND e.effect_id=NEW.effect_id
   AND d.state='approved' AND e.approval_required=1
   AND d.subject_type=e.subject_type AND d.subject_id=e.subject_id
   AND d.action=e.action AND d.effect_digest=e.effect_digest AND d.snapshot_id IS e.snapshot_id
 ) THEN RAISE(ABORT,'decision does not authorize effect') END;
END;
CREATE TRIGGER reject_unapproved_dispatch BEFORE UPDATE OF state ON effect_intents WHEN NEW.state='dispatched' AND NEW.approval_required=1 BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM approval_consumptions c WHERE c.project_id=NEW.project_id AND c.decision_id=NEW.decision_id AND c.effect_id=NEW.effect_id) THEN RAISE(ABORT,'approval not consumed') END;
END;
CREATE TRIGGER reject_unverified_bible_active BEFORE INSERT ON active_bible_editions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM bible_editions e JOIN blobs b ON b.content_digest=e.content_digest JOIN blobs m ON m.content_digest=e.manifest_digest WHERE e.project_id=NEW.project_id AND e.bible_id=NEW.bible_id AND e.edition_id=NEW.edition_id AND b.durability='verified' AND m.durability='verified') THEN RAISE(ABORT,'Bible bytes not durable') END;
END;
CREATE TRIGGER reject_unverified_bible_active_update BEFORE UPDATE OF edition_id ON active_bible_editions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM bible_editions e JOIN blobs b ON b.content_digest=e.content_digest JOIN blobs m ON m.content_digest=e.manifest_digest WHERE e.project_id=NEW.project_id AND e.bible_id=NEW.bible_id AND e.edition_id=NEW.edition_id AND b.durability='verified' AND m.durability='verified') THEN RAISE(ABORT,'Bible bytes not durable') END;
END;

-- A single canonical source pointer per project/repository, promoted by transactional CAS.
CREATE UNIQUE INDEX idx_one_canonical_source ON source_revisions(project_id,repo_id) WHERE state='canonical';
CREATE TRIGGER immutable_bible_edition_update BEFORE UPDATE ON bible_editions BEGIN SELECT RAISE(ABORT,'published edition immutable'); END;
CREATE TRIGGER immutable_bible_edition_delete BEFORE DELETE ON bible_editions BEGIN SELECT RAISE(ABORT,'published edition immutable'); END;
CREATE TRIGGER immutable_canon_update BEFORE UPDATE ON canon_snapshots BEGIN SELECT RAISE(ABORT,'Canon snapshot immutable'); END;
CREATE TRIGGER immutable_canon_delete BEFORE DELETE ON canon_snapshots BEGIN SELECT RAISE(ABORT,'Canon snapshot immutable'); END;
CREATE TRIGGER immutable_approved_decision_effect BEFORE UPDATE OF project_id,subject_type,subject_id,action,effect_digest,snapshot_id,proof_digest,verified_owner_principal_id ON owner_decisions BEGIN SELECT RAISE(ABORT,'approved effect identity immutable'); END;
CREATE TRIGGER prevent_blob_verification_retraction BEFORE UPDATE OF durability ON blobs WHEN OLD.durability='verified' AND NEW.durability<>'verified' BEGIN SELECT RAISE(ABORT,'verified byte reference immutable; report missing externally'); END;
CREATE TRIGGER reject_revoked_decision_dispatch BEFORE UPDATE OF state ON effect_intents WHEN NEW.state='dispatched' AND NEW.approval_required=1 BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM owner_decisions WHERE project_id=NEW.project_id AND decision_id=NEW.decision_id AND state='approved') THEN RAISE(ABORT,'decision revoked or absent') END;
END;

CREATE TRIGGER reject_same_author_review BEFORE INSERT ON qa_reviews BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM sessions s WHERE s.project_id=NEW.project_id AND s.session_id=NEW.author_session_id AND s.author_principal_id=NEW.reviewer_principal_id) THEN RAISE(ABORT,'reviewer is author') END;
END;
CREATE TRIGGER decision_owner_must_be_human BEFORE INSERT ON owner_decisions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM principals WHERE principal_id=NEW.verified_owner_principal_id AND kind='human') THEN RAISE(ABORT,'verified owner must be human') END;
END;

-- Prevent direct insertion or transitions to effect success without a consumed exact decision.
CREATE TRIGGER reject_unconsumed_effect_insert BEFORE INSERT ON effect_intents WHEN NEW.approval_required=1 AND NEW.state<>'prepared' BEGIN SELECT RAISE(ABORT,'approved effect must start prepared'); END;
CREATE TRIGGER reject_unconsumed_effect_transition BEFORE UPDATE OF state ON effect_intents WHEN NEW.approval_required=1 AND NEW.state IN ('observed','unknown') BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM approval_consumptions c JOIN owner_decisions d ON d.project_id=c.project_id AND d.decision_id=c.decision_id WHERE c.project_id=NEW.project_id AND c.effect_id=NEW.effect_id AND d.state='approved' AND d.snapshot_id IS NEW.snapshot_id) THEN RAISE(ABORT,'unconsumed decision or wrong snapshot') END;
END;
CREATE TRIGGER reject_submission_session_mismatch BEFORE INSERT ON submissions BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM sessions s WHERE s.project_id=NEW.project_id AND s.session_id=NEW.session_id AND s.task_id=NEW.task_id AND s.lease_generation=NEW.lease_generation) THEN RAISE(ABORT,'submission author task/lease mismatch') END;
END;
CREATE TRIGGER reject_review_submission_author_mismatch BEFORE INSERT ON qa_reviews BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM submissions s WHERE s.project_id=NEW.project_id AND s.submission_id=NEW.submission_id AND s.session_id=NEW.author_session_id) THEN RAISE(ABORT,'review author session mismatch') END;
END;

-- Project authorization is separate from physical content-addressed deduplication.
CREATE TRIGGER bible_project_blob_scoped BEFORE INSERT ON bible_editions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM project_blobs WHERE project_id=NEW.project_id AND content_digest=NEW.content_digest AND purpose='bible-content') THEN RAISE(ABORT,'Bible content not project-scoped') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM project_blobs WHERE project_id=NEW.project_id AND content_digest=NEW.manifest_digest AND purpose='bible-manifest') THEN RAISE(ABORT,'Bible manifest not project-scoped') END;
END;
CREATE TRIGGER artifact_project_blob_scoped BEFORE INSERT ON artifacts BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM project_blobs WHERE project_id=NEW.project_id AND content_digest=NEW.content_digest AND purpose='artifact-content') THEN RAISE(ABORT,'artifact content not project-scoped') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM project_blobs WHERE project_id=NEW.project_id AND content_digest=NEW.manifest_digest AND purpose='artifact-manifest') THEN RAISE(ABORT,'artifact manifest not project-scoped') END;
END;

CREATE TRIGGER require_verified_bible_publication BEFORE INSERT ON bible_editions BEGIN
 SELECT CASE WHEN NOT EXISTS(
 SELECT 1 FROM owner_decisions d JOIN approval_consumptions a ON a.project_id=d.project_id AND a.decision_id=d.decision_id
 JOIN effect_intents e ON e.project_id=a.project_id AND e.effect_id=a.effect_id
 WHERE d.project_id=NEW.project_id AND d.decision_id=NEW.owner_decision_id AND d.subject_type='bible' AND d.subject_id=NEW.bible_id
 AND d.action='bible.publish' AND d.effect_digest=NEW.publication_effect_digest AND d.state='approved'
 AND e.subject_type='bible' AND e.subject_id=NEW.bible_id AND e.action='bible.publish' AND e.effect_digest=NEW.publication_effect_digest
 ) THEN RAISE(ABORT,'Bible publication lacks exact consumed owner decision') END;
END;
CREATE TRIGGER owner_decision_needs_project_grant BEFORE INSERT ON owner_decisions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM project_grants WHERE project_id=NEW.project_id AND principal_id=NEW.verified_owner_principal_id AND grant_code='owner') THEN RAISE(ABORT,'verified principal is not project owner') END;
END;

-- Owner-configured exact policy scope, separate from package templates and project game content.
CREATE TABLE project_policies(project_id TEXT NOT NULL,policy_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>0),policy_digest TEXT NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)),PRIMARY KEY(project_id,policy_id),FOREIGN KEY(project_id) REFERENCES projects(project_id)) STRICT;
CREATE TABLE project_policy_rules(project_id TEXT NOT NULL,policy_id TEXT NOT NULL,rule_id TEXT NOT NULL,subject_type TEXT NOT NULL,action TEXT NOT NULL,scope_digest TEXT NOT NULL,disposition TEXT NOT NULL CHECK(disposition IN ('delegated','owner_required','forbidden')),PRIMARY KEY(project_id,policy_id,rule_id),FOREIGN KEY(project_id,policy_id) REFERENCES project_policies(project_id,policy_id)) STRICT;
CREATE UNIQUE INDEX one_active_policy_per_project ON project_policies(project_id) WHERE active=1;


-- New initial schema: these guards protect cross-object publication/identity even if a domain service is bypassed.
CREATE TRIGGER immutable_source_revision_identity BEFORE UPDATE OF project_id,revision_id,repo_id,commit_sha,tree_digest ON source_revisions
BEGIN SELECT RAISE(ABORT,'source revision identity immutable'); END;
CREATE TRIGGER immutable_artifact_identity BEFORE UPDATE OF project_id,artifact_id,kind,content_digest,manifest_digest,producer_session_id,source_revision_id,snapshot_id ON artifacts
BEGIN SELECT RAISE(ABORT,'artifact identity immutable'); END;
CREATE TRIGGER forbid_direct_published_release_insert BEFORE INSERT ON releases WHEN NEW.state='published'
BEGIN SELECT RAISE(ABORT,'release must be prepared first'); END;
CREATE TRIGGER guard_published_release BEFORE UPDATE OF state ON releases WHEN NEW.state='published' AND OLD.state<>'published'
BEGIN
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM owner_decisions d
   JOIN approval_consumptions a ON a.project_id=d.project_id AND a.decision_id=d.decision_id
   JOIN effect_intents e ON e.project_id=a.project_id AND e.effect_id=a.effect_id
   WHERE d.project_id=NEW.project_id AND d.decision_id=NEW.decision_id
     AND d.subject_type='release' AND d.subject_id=NEW.release_id
     AND d.action='release.publish' AND d.effect_digest=NEW.release_digest
     AND d.snapshot_id=NEW.snapshot_id AND d.state='approved'
     AND e.subject_type='release' AND e.subject_id=NEW.release_id
     AND e.action='release.publish' AND e.effect_digest=NEW.release_digest
     AND e.snapshot_id=NEW.snapshot_id AND e.approval_required=1 AND e.state='observed'
 ) THEN RAISE(ABORT,'release not exactly approved and observed') END;
END;
CREATE TRIGGER forbid_republish_release BEFORE UPDATE OF state ON releases WHEN OLD.state='published' AND NEW.state<>'published'
BEGIN SELECT RAISE(ABORT,'published release state immutable'); END;

-- Proposal authority: proposals are unapproved requests; owner decisions are only created after trusted resolution.
CREATE TABLE decision_proposals(
 project_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
 subject_type TEXT NOT NULL CHECK(subject_type IN ('project','bible','feature','task','cabal','artifact','release')),
 subject_id TEXT NOT NULL, action TEXT NOT NULL, effect_digest TEXT NOT NULL,
 snapshot_id TEXT, requester_principal_id TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected')),
 created_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 PRIMARY KEY(project_id,proposal_id), FOREIGN KEY(project_id) REFERENCES projects(project_id),
 FOREIGN KEY(project_id,snapshot_id) REFERENCES canon_snapshots(project_id,snapshot_id),
 FOREIGN KEY(requester_principal_id) REFERENCES principals(principal_id)
) STRICT;
CREATE TRIGGER immutable_proposal_identity BEFORE UPDATE OF project_id,proposal_id,subject_type,subject_id,action,effect_digest,snapshot_id,requester_principal_id ON decision_proposals
BEGIN SELECT RAISE(ABORT,'owner proposal identity immutable'); END;
CREATE TRIGGER owner_resolution_requires_proposal BEFORE INSERT ON owner_decisions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM decision_proposals p
  WHERE p.project_id=NEW.project_id AND p.proposal_id=NEW.decision_id AND p.state='pending'
    AND p.subject_type=NEW.subject_type AND p.subject_id=NEW.subject_id AND p.action=NEW.action
    AND p.effect_digest=NEW.effect_digest AND p.snapshot_id IS NEW.snapshot_id)
 THEN RAISE(ABORT,'owner decision lacks exact pending proposal') END;
 SELECT CASE WHEN NEW.state NOT IN ('approved','rejected') THEN RAISE(ABORT,'new resolution must approve or reject') END;
END;
CREATE TRIGGER finalize_decision_proposal AFTER INSERT ON owner_decisions
BEGIN UPDATE decision_proposals SET state=NEW.state,revision=revision+1 WHERE project_id=NEW.project_id AND proposal_id=NEW.decision_id; END;

-- New Studio backups and recovery requests are durable and project-bound; not legacy upgrades.
CREATE TABLE backup_sets(
 project_id TEXT NOT NULL, backup_digest TEXT NOT NULL, database_blob_digest TEXT NOT NULL,
 manifest_digest TEXT NOT NULL, schema_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('staging','verified','failed')),
 created_at TEXT NOT NULL, PRIMARY KEY(project_id,backup_digest),
 FOREIGN KEY(project_id) REFERENCES projects(project_id),
 FOREIGN KEY(database_blob_digest) REFERENCES blobs(content_digest),
 FOREIGN KEY(manifest_digest) REFERENCES blobs(content_digest)
) STRICT;
CREATE TABLE recovery_jobs(
 project_id TEXT NOT NULL, recovery_id TEXT NOT NULL, backup_digest TEXT NOT NULL,
 expected_project_revision INTEGER NOT NULL CHECK(expected_project_revision>0),
 state TEXT NOT NULL CHECK(state IN ('requested','verifying','staged','committed','failed','unknown')),
 receipt_digest TEXT, created_at TEXT NOT NULL,
 PRIMARY KEY(project_id,recovery_id),
 FOREIGN KEY(project_id,backup_digest) REFERENCES backup_sets(project_id,backup_digest)
) STRICT;
CREATE INDEX idx_backup_project ON backup_sets(project_id,state,created_at);
CREATE INDEX idx_restore_pending ON recovery_jobs(project_id,state,created_at);
CREATE TRIGGER reject_unverified_restore BEFORE UPDATE OF state ON recovery_jobs WHEN NEW.state='committed'
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM backup_sets b WHERE b.project_id=NEW.project_id AND b.backup_digest=NEW.backup_digest AND b.state='verified') THEN RAISE(ABORT,'restore requires verified backup') END; END;


-- QA independence can be proven for human and AI reviewers, including common AI session lineage.
CREATE TABLE session_lineages(
 project_id TEXT NOT NULL,session_id TEXT NOT NULL,
 root_session_id TEXT NOT NULL,parent_session_id TEXT, lineage_digest TEXT NOT NULL,
 PRIMARY KEY(project_id,session_id),
 FOREIGN KEY(project_id,session_id) REFERENCES sessions(project_id,session_id),
 FOREIGN KEY(project_id,root_session_id) REFERENCES sessions(project_id,session_id),
 FOREIGN KEY(project_id,parent_session_id) REFERENCES sessions(project_id,session_id)
) STRICT;
CREATE TABLE qa_independence_proofs(
 project_id TEXT NOT NULL,review_id TEXT NOT NULL,
 author_session_id TEXT NOT NULL,reviewer_principal_id TEXT NOT NULL,
 reviewer_root_session_id TEXT, independence_evidence_digest TEXT NOT NULL,
 PRIMARY KEY(project_id,review_id),
 FOREIGN KEY(project_id,review_id) REFERENCES qa_reviews(project_id,review_id),
 FOREIGN KEY(project_id,author_session_id) REFERENCES sessions(project_id,session_id),
 FOREIGN KEY(reviewer_principal_id) REFERENCES principals(principal_id),
 FOREIGN KEY(project_id,reviewer_root_session_id) REFERENCES sessions(project_id,session_id)
) STRICT;
CREATE TRIGGER reject_same_lineage_qa_proof BEFORE INSERT ON qa_independence_proofs
BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM sessions s WHERE s.project_id=NEW.project_id AND s.session_id=NEW.author_session_id AND s.author_principal_id=NEW.reviewer_principal_id)
 THEN RAISE(ABORT,'reviewer is author') END;
 SELECT CASE WHEN NEW.reviewer_root_session_id IS NOT NULL AND EXISTS(
   SELECT 1 FROM session_lineages x JOIN session_lineages y ON x.project_id=y.project_id
   WHERE x.project_id=NEW.project_id AND x.session_id=NEW.author_session_id AND y.session_id=NEW.reviewer_root_session_id
     AND x.root_session_id=y.root_session_id
 ) THEN RAISE(ABORT,'reviewer shares author lineage') END;
END;
CREATE TRIGGER require_qa_evidence_on_pass BEFORE UPDATE OF verdict ON qa_reviews WHEN NEW.verdict='pass'
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM qa_independence_proofs p WHERE p.project_id=NEW.project_id AND p.review_id=NEW.review_id
   AND p.author_session_id=NEW.author_session_id AND p.reviewer_principal_id=NEW.reviewer_principal_id)
 THEN RAISE(ABORT,'QA independence proof missing') END;
 SELECT CASE WHEN NEW.evidence_digest IS NULL OR NOT EXISTS(SELECT 1 FROM qa_receipts q WHERE q.project_id=NEW.project_id AND q.review_id=NEW.review_id)
 THEN RAISE(ABORT,'QA original artifact evidence missing') END;
END;
CREATE TRIGGER report_delivery_requires_observed_effect BEFORE UPDATE OF delivery_state ON reports WHEN NEW.delivery_state='sent'
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM effect_intents e WHERE e.project_id=NEW.project_id AND e.subject_type='report'
 AND e.subject_id=NEW.report_id AND e.action='report.deliver' AND e.state='observed')
 THEN RAISE(ABORT,'report delivery not observed') END;
END;


-- GitHub is the durable relay transport. These tables track verified projections, not model executions.
CREATE TABLE relay_channels(project_id TEXT NOT NULL,channel_id TEXT NOT NULL,repo_id TEXT NOT NULL,git_ref TEXT NOT NULL,root_path TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),PRIMARY KEY(project_id,channel_id),FOREIGN KEY(project_id,repo_id) REFERENCES project_repositories(project_id,repo_id)) STRICT;
CREATE TABLE relay_observations(project_id TEXT NOT NULL,observation_id TEXT NOT NULL,message_id TEXT NOT NULL,channel_id TEXT NOT NULL,remote_path TEXT NOT NULL,remote_commit_sha TEXT NOT NULL,envelope_digest TEXT NOT NULL,observed_at TEXT NOT NULL,verification TEXT NOT NULL CHECK(verification IN ('verified','rejected','unknown')),PRIMARY KEY(project_id,observation_id),UNIQUE(project_id,message_id,remote_commit_sha),FOREIGN KEY(project_id,message_id,channel_id) REFERENCES relay_messages(project_id,message_id,channel_id),FOREIGN KEY(project_id,channel_id) REFERENCES relay_channels(project_id,channel_id)) STRICT;
CREATE TABLE relay_receipts(
 project_id TEXT NOT NULL,receipt_id TEXT NOT NULL,message_id TEXT NOT NULL,
 channel_id TEXT NOT NULL,recipient_session_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('SEEN','CHECKPOINT','SUBMITTED','STOP_SEEN')),
 observed_message_sha256 TEXT NOT NULL,contract_digest TEXT NOT NULL,
 remote_path TEXT NOT NULL,remote_commit_sha TEXT NOT NULL,payload_digest TEXT NOT NULL,observed_at TEXT NOT NULL,
 PRIMARY KEY(project_id,receipt_id),UNIQUE(project_id,remote_path),
 UNIQUE(project_id,message_id,recipient_session_id,kind,remote_commit_sha),
 FOREIGN KEY(project_id,message_id,channel_id) REFERENCES relay_messages(project_id,message_id,channel_id),
 FOREIGN KEY(project_id,recipient_session_id) REFERENCES sessions(project_id,session_id)
) STRICT;
CREATE TABLE relay_sync_cursors(project_id TEXT NOT NULL,channel_id TEXT NOT NULL,observed_ref_sha TEXT NOT NULL,observed_at TEXT NOT NULL,PRIMARY KEY(project_id,channel_id),FOREIGN KEY(project_id,channel_id) REFERENCES relay_channels(project_id,channel_id)) STRICT;
CREATE INDEX idx_relay_observations_message ON relay_observations(project_id,message_id,observed_at);
CREATE INDEX idx_relay_receipts_session ON relay_receipts(project_id,recipient_session_id,observed_at);
CREATE TRIGGER verify_relay_receipt_recipient BEFORE INSERT ON relay_receipts BEGIN SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM relay_messages m WHERE m.project_id=NEW.project_id AND m.message_id=NEW.message_id AND m.to_session_id=NEW.recipient_session_id) THEN RAISE(ABORT,'relay receipt recipient mismatch') END; END;
CREATE TRIGGER forbid_unsourced_session_completion BEFORE UPDATE OF state ON sessions WHEN NEW.state='completed_verified' AND OLD.state<>'completed_verified' BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM submissions s WHERE s.project_id=NEW.project_id AND s.session_id=NEW.session_id AND s.state IN ('qa_passed','integrated')) THEN RAISE(ABORT,'no independently verified submission') END; END;

-- A worker cannot be marked active from a self-asserted API field or a local-only assignment.
CREATE TRIGGER reject_direct_active_session_insert BEFORE INSERT ON sessions WHEN NEW.state='active_observed' BEGIN SELECT RAISE(ABORT,'active session needs observed Github relay'); END;
CREATE TRIGGER require_observed_hello_before_active BEFORE UPDATE OF state ON sessions WHEN NEW.state='active_observed' AND OLD.state<>'active_observed' BEGIN
 SELECT CASE WHEN NOT EXISTS (
 SELECT 1 FROM relay_messages m JOIN relay_observations o ON o.project_id=m.project_id AND o.message_id=m.message_id AND o.channel_id=m.channel_id
 JOIN leases l ON l.project_id=NEW.project_id AND l.task_id=NEW.task_id AND l.generation=NEW.lease_generation
 WHERE m.project_id=NEW.project_id AND m.from_session_id=NEW.session_id
 AND m.kind='HELLO' AND m.task_id=NEW.task_id AND m.lease_generation=NEW.lease_generation
 AND m.canon_snapshot_id=NEW.snapshot_id AND l.status='active'
 AND o.verification='verified' AND o.remote_path=m.remote_path AND o.envelope_digest=m.envelope_digest
 ) THEN RAISE(ABORT,'no exact verified HELLO for active lease and snapshot') END;
END;


-- R5: enforce relay envelope kind/identity and exact verified GitHub projection.
-- Database provenance proves recorded remote bytes, not the identity of a shared GitHub user.
CREATE TRIGGER reject_session_insert_without_manual_prepare BEFORE INSERT ON sessions
WHEN NEW.state<>'prepared' BEGIN SELECT RAISE(ABORT,'manual session must begin prepared'); END;
CREATE TRIGGER reject_misbound_relay_envelope BEFORE INSERT ON relay_messages BEGIN
 SELECT CASE WHEN NEW.from_session_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM sessions s WHERE s.project_id=NEW.project_id AND s.session_id=NEW.from_session_id
   AND s.task_id=NEW.task_id AND s.lease_generation=NEW.lease_generation AND s.snapshot_id=NEW.canon_snapshot_id
 ) THEN RAISE(ABORT,'relay sender task/lease/snapshot mismatch') END;
 SELECT CASE WHEN NEW.kind='HELLO' AND (NEW.from_session_id IS NULL OR NEW.to_session_id<>NEW.from_session_id)
 THEN RAISE(ABORT,'HELLO must be self-addressed by registered browser session') END;
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM sessions s WHERE s.project_id=NEW.project_id AND s.session_id=NEW.to_session_id
 ) THEN RAISE(ABORT,'relay recipient absent') END;
END;
CREATE TRIGGER reject_unmatched_verified_relay_observation BEFORE INSERT ON relay_observations
WHEN NEW.verification='verified' BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM relay_messages m WHERE m.project_id=NEW.project_id AND m.message_id=NEW.message_id
  AND m.channel_id=NEW.channel_id AND m.remote_path=NEW.remote_path
  AND m.envelope_digest=NEW.envelope_digest
  AND (m.remote_commit_sha IS NULL OR m.remote_commit_sha=NEW.remote_commit_sha)
 ) THEN RAISE(ABORT,'verified observation differs from pinned relay envelope') END;
END;
CREATE TRIGGER immutable_verified_relay_observation BEFORE UPDATE ON relay_observations
WHEN OLD.verification='verified' BEGIN SELECT RAISE(ABORT,'verified GitHub observation immutable'); END;
CREATE TRIGGER immutable_verified_relay_observation_delete BEFORE DELETE ON relay_observations
WHEN OLD.verification='verified' BEGIN SELECT RAISE(ABORT,'verified GitHub observation immutable'); END;
CREATE TRIGGER immutable_relay_message_identity BEFORE UPDATE OF project_id,message_id,channel_id,from_session_id,to_session_id,kind,task_id,lease_generation,canon_snapshot_id,contract_digest,envelope_digest,payload_digest,remote_path ON relay_messages
BEGIN SELECT RAISE(ABORT,'relay envelope identity immutable'); END;

-- R5: QA PASS must be an independently verified review of the submitted bytes.
CREATE TRIGGER forbid_direct_pass_review_insert BEFORE INSERT ON qa_reviews
WHEN NEW.verdict='pass' BEGIN SELECT RAISE(ABORT,'review must begin pending and gather independent evidence'); END;
CREATE TRIGGER reject_qa_pass_without_exact_submission BEFORE UPDATE OF verdict ON qa_reviews
WHEN NEW.verdict='pass' AND OLD.verdict<>'pass' BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM submissions s JOIN tasks t ON t.project_id=s.project_id AND t.task_id=s.task_id
  WHERE s.project_id=NEW.project_id AND s.submission_id=NEW.submission_id
  AND s.session_id=NEW.author_session_id AND s.state IN ('submitted','qa_passed','integrated')
  AND t.acceptance_digest=NEW.acceptance_digest
  AND s.source_revision_id IS NEW.source_revision_id
 ) THEN RAISE(ABORT,'QA review does not match submitted source and acceptance profile') END;
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM qa_receipts qr JOIN submission_artifacts sa ON sa.project_id=qr.project_id
     AND sa.artifact_id=qr.artifact_id
  WHERE qr.project_id=NEW.project_id AND qr.review_id=NEW.review_id
    AND sa.submission_id=NEW.submission_id
 ) THEN RAISE(ABORT,'QA receipt not linked to original submission artifact') END;
END;
CREATE TRIGGER immutable_accepted_review BEFORE UPDATE ON qa_reviews WHEN OLD.verdict='pass'
BEGIN SELECT RAISE(ABORT,'accepted QA review immutable; create new review to invalidate'); END;
CREATE TRIGGER immutable_accepted_review_delete BEFORE DELETE ON qa_reviews WHEN OLD.verdict='pass'
BEGIN SELECT RAISE(ABORT,'accepted QA review immutable'); END;
CREATE TRIGGER protect_accepted_qa_receipt_update BEFORE UPDATE ON qa_receipts
WHEN EXISTS(SELECT 1 FROM qa_reviews q WHERE q.project_id=OLD.project_id AND q.review_id=OLD.review_id AND q.verdict='pass')
BEGIN SELECT RAISE(ABORT,'accepted QA receipt immutable'); END;
CREATE TRIGGER protect_accepted_qa_receipt_delete BEFORE DELETE ON qa_receipts
WHEN EXISTS(SELECT 1 FROM qa_reviews q WHERE q.project_id=OLD.project_id AND q.review_id=OLD.review_id AND q.verdict='pass')
BEGIN SELECT RAISE(ABORT,'accepted QA receipt immutable'); END;
CREATE TRIGGER protect_accepted_qa_proof_update BEFORE UPDATE ON qa_independence_proofs
WHEN EXISTS(SELECT 1 FROM qa_reviews q WHERE q.project_id=OLD.project_id AND q.review_id=OLD.review_id AND q.verdict='pass')
BEGIN SELECT RAISE(ABORT,'accepted QA independence proof immutable'); END;
CREATE TRIGGER protect_accepted_qa_proof_delete BEFORE DELETE ON qa_independence_proofs
WHEN EXISTS(SELECT 1 FROM qa_reviews q WHERE q.project_id=OLD.project_id AND q.review_id=OLD.review_id AND q.verdict='pass')
BEGIN SELECT RAISE(ABORT,'accepted QA independence proof immutable'); END;
CREATE TRIGGER reject_premature_submission_qa_state BEFORE INSERT ON submissions
WHEN NEW.state IN ('qa_passed','integrated') BEGIN SELECT RAISE(ABORT,'submission cannot begin QA-accepted'); END;
CREATE TRIGGER require_exact_qa_pass_before_submission_acceptance BEFORE UPDATE OF state ON submissions
WHEN NEW.state IN ('qa_passed','integrated') AND OLD.state<>NEW.state BEGIN
 SELECT CASE WHEN NOT EXISTS (
 SELECT 1 FROM qa_reviews q JOIN tasks t ON t.project_id=NEW.project_id AND t.task_id=NEW.task_id
 JOIN qa_independence_proofs p ON p.project_id=q.project_id AND p.review_id=q.review_id
 JOIN qa_receipts qr ON qr.project_id=q.project_id AND qr.review_id=q.review_id
 JOIN submission_artifacts sa ON sa.project_id=NEW.project_id AND sa.submission_id=NEW.submission_id AND sa.artifact_id=qr.artifact_id
 WHERE q.project_id=NEW.project_id AND q.submission_id=NEW.submission_id
 AND q.verdict='pass' AND q.author_session_id=NEW.session_id
 AND q.source_revision_id IS NEW.source_revision_id AND q.acceptance_digest=t.acceptance_digest
 AND p.author_session_id=NEW.session_id AND p.reviewer_principal_id=q.reviewer_principal_id
 AND q.evidence_digest IS NOT NULL
 ) THEN RAISE(ABORT,'independent exact QA PASS and evidence required for submission state') END;
END;
CREATE TRIGGER forbid_direct_completed_session_insert BEFORE INSERT ON sessions
WHEN NEW.state='completed_verified' BEGIN SELECT RAISE(ABORT,'session must be prepared and reviewed first'); END;
CREATE TRIGGER require_actual_independent_qa_for_completion BEFORE UPDATE OF state ON sessions
WHEN NEW.state='completed_verified' AND OLD.state<>'completed_verified' BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM submissions s JOIN qa_reviews q ON q.project_id=s.project_id AND q.submission_id=s.submission_id
  JOIN tasks t ON t.project_id=s.project_id AND t.task_id=s.task_id
  JOIN qa_independence_proofs p ON p.project_id=q.project_id AND p.review_id=q.review_id
  JOIN qa_receipts qr ON qr.project_id=q.project_id AND qr.review_id=q.review_id
  JOIN submission_artifacts sa ON sa.project_id=s.project_id AND sa.submission_id=s.submission_id AND sa.artifact_id=qr.artifact_id
  WHERE s.project_id=NEW.project_id AND s.session_id=NEW.session_id
   AND s.task_id=NEW.task_id AND s.lease_generation=NEW.lease_generation
   AND s.state IN ('qa_passed','integrated') AND q.verdict='pass'
   AND q.author_session_id=NEW.session_id AND q.source_revision_id IS s.source_revision_id
   AND q.acceptance_digest=t.acceptance_digest AND p.author_session_id=NEW.session_id
   AND p.reviewer_principal_id=q.reviewer_principal_id AND q.evidence_digest IS NOT NULL
 ) THEN RAISE(ABORT,'cannot complete session without exact independently reviewed submission') END;
END;

CREATE TRIGGER immutable_relay_observation_any_update BEFORE UPDATE ON relay_observations
BEGIN SELECT RAISE(ABORT,'relay observations append-only'); END;
CREATE TRIGGER immutable_relay_observation_any_delete BEFORE DELETE ON relay_observations
BEGIN SELECT RAISE(ABORT,'relay observations append-only'); END;
CREATE TRIGGER require_observation_before_relay_observed BEFORE UPDATE OF delivery_state ON relay_messages
WHEN NEW.delivery_state='observed' AND OLD.delivery_state<>'observed' BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM relay_observations o WHERE o.project_id=NEW.project_id
 AND o.message_id=NEW.message_id AND o.channel_id=NEW.channel_id AND o.verification='verified'
 AND o.remote_path=NEW.remote_path AND o.envelope_digest=NEW.envelope_digest)
 THEN RAISE(ABORT,'relay observed needs exact GitHub readback') END;
END;

CREATE TRIGGER immutable_qa_review_identity BEFORE UPDATE OF project_id,review_id,submission_id,reviewer_principal_id,author_session_id,source_revision_id,acceptance_digest ON qa_reviews
BEGIN SELECT RAISE(ABORT,'QA review identity immutable'); END;
CREATE TRIGGER immutable_submission_identity BEFORE UPDATE OF project_id,submission_id,session_id,task_id,lease_generation,source_revision_id,manifest_digest ON submissions
BEGIN SELECT RAISE(ABORT,'submission identity immutable'); END;
CREATE TRIGGER immutable_submission_attachment_delete AFTER DELETE ON submission_artifacts
WHEN EXISTS(SELECT 1 FROM qa_reviews q WHERE q.project_id=OLD.project_id
 AND q.submission_id=OLD.submission_id AND q.verdict='pass')
BEGIN SELECT RAISE(ABORT,'accepted submission attachments immutable'); END;
CREATE TRIGGER immutable_submission_attachment_update BEFORE UPDATE ON submission_artifacts
WHEN EXISTS(SELECT 1 FROM qa_reviews q WHERE q.project_id=OLD.project_id
 AND q.submission_id=OLD.submission_id AND q.verdict='pass')
BEGIN SELECT RAISE(ABORT,'accepted submission attachments immutable'); END;

CREATE TRIGGER enforce_sessions_transitions BEFORE UPDATE OF state ON sessions
WHEN NEW.state<>OLD.state AND NOT ((OLD.state='active_observed' AND NEW.state='quiet') OR (OLD.state='active_observed' AND NEW.state='stop_requested') OR (OLD.state='active_observed' AND NEW.state='completed_verified') OR (OLD.state='active_observed' AND NEW.state='failed_verified') OR (OLD.state='active_observed' AND NEW.state='unknown') OR (OLD.state='awaiting_manual_start' AND NEW.state='active_observed') OR (OLD.state='awaiting_manual_start' AND NEW.state='stop_requested') OR (OLD.state='awaiting_manual_start' AND NEW.state='unknown') OR (OLD.state='failed_verified' AND NEW.state='awaiting_manual_start') OR (OLD.state='prepared' AND NEW.state='awaiting_manual_start') OR (OLD.state='prepared' AND NEW.state='unknown') OR (OLD.state='quiet' AND NEW.state='active_observed') OR (OLD.state='quiet' AND NEW.state='stop_requested') OR (OLD.state='quiet' AND NEW.state='unknown') OR (OLD.state='stop_requested' AND NEW.state='quiet') OR (OLD.state='stop_requested' AND NEW.state='completed_verified') OR (OLD.state='stop_requested' AND NEW.state='failed_verified') OR (OLD.state='stop_requested' AND NEW.state='unknown') OR (OLD.state='unknown' AND NEW.state='active_observed') OR (OLD.state='unknown' AND NEW.state='quiet') OR (OLD.state='unknown' AND NEW.state='stop_requested') OR (OLD.state='unknown' AND NEW.state='failed_verified'))
BEGIN SELECT RAISE(ABORT,'invalid sessions transition'); END;

CREATE TRIGGER enforce_submissions_transitions BEFORE UPDATE OF state ON submissions
WHEN NEW.state<>OLD.state AND NOT ((OLD.state='qa_failed' AND NEW.state='rejected') OR (OLD.state='qa_passed' AND NEW.state='integrated') OR (OLD.state='qa_passed' AND NEW.state='rejected') OR (OLD.state='staged' AND NEW.state='submitted') OR (OLD.state='staged' AND NEW.state='rejected') OR (OLD.state='submitted' AND NEW.state='qa_passed') OR (OLD.state='submitted' AND NEW.state='qa_failed') OR (OLD.state='submitted' AND NEW.state='rejected'))
BEGIN SELECT RAISE(ABORT,'invalid submissions transition'); END;

CREATE TRIGGER enforce_relay_messages_transitions BEFORE UPDATE OF delivery_state ON relay_messages
WHEN NEW.delivery_state<>OLD.delivery_state AND NOT ((OLD.delivery_state='published' AND NEW.delivery_state='observed') OR (OLD.delivery_state='published' AND NEW.delivery_state='unknown') OR (OLD.delivery_state='published' AND NEW.delivery_state='rejected') OR (OLD.delivery_state='queued' AND NEW.delivery_state='published') OR (OLD.delivery_state='queued' AND NEW.delivery_state='unknown') OR (OLD.delivery_state='queued' AND NEW.delivery_state='rejected') OR (OLD.delivery_state='unknown' AND NEW.delivery_state='published') OR (OLD.delivery_state='unknown' AND NEW.delivery_state='observed') OR (OLD.delivery_state='unknown' AND NEW.delivery_state='rejected'))
BEGIN SELECT RAISE(ABORT,'invalid relay_messages transition'); END;

-- Receipt is bound to the exact observed original message, not just a recipient label.
CREATE TRIGGER verify_original_for_relay_receipt BEFORE INSERT ON relay_receipts BEGIN
 SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM relay_messages m JOIN relay_observations o ON o.project_id=m.project_id
    AND o.message_id=m.message_id AND o.channel_id=m.channel_id AND o.verification='verified'
  WHERE m.project_id=NEW.project_id AND m.message_id=NEW.message_id
  AND m.channel_id=NEW.channel_id AND m.to_session_id=NEW.recipient_session_id
  AND m.envelope_digest=NEW.observed_message_sha256
  AND m.contract_digest=NEW.contract_digest
  AND o.envelope_digest=m.envelope_digest AND o.remote_path=m.remote_path
 ) THEN RAISE(ABORT,'relay receipt does not match a verified original message') END;
END;
CREATE TRIGGER immutable_relay_receipt_update BEFORE UPDATE ON relay_receipts
BEGIN SELECT RAISE(ABORT,'verified relay receipt immutable'); END;
CREATE TRIGGER immutable_relay_receipt_delete BEFORE DELETE ON relay_receipts
BEGIN SELECT RAISE(ABORT,'verified relay receipt immutable'); END;
