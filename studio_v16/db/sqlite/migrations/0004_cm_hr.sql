-- Department, document, leadership and response state share the same authority file.
CREATE TABLE department_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL REFERENCES event_journal(event_id),
 sender_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 recipient_department TEXT NOT NULL,
 recipient_id TEXT NOT NULL REFERENCES principals(principal_id),
 correlation_id TEXT, causation_id TEXT, legacy_archive_ref TEXT,
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND length(metadata_json)<=16384),
 created_at INTEGER NOT NULL,
 UNIQUE(event_id,recipient_department,recipient_id)
) STRICT;
CREATE INDEX department_recipient_sequence_idx ON department_events(recipient_department,recipient_id,sequence);
CREATE TABLE department_inbox_cursors (
 recipient_department TEXT NOT NULL,recipient_id TEXT NOT NULL REFERENCES principals(principal_id),
 last_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_sequence>=0),updated_at INTEGER NOT NULL,
 PRIMARY KEY(recipient_department,recipient_id)
) STRICT;
CREATE TABLE department_deliveries (
 recipient_department TEXT NOT NULL,recipient_id TEXT NOT NULL REFERENCES principals(principal_id),
 sequence INTEGER NOT NULL REFERENCES department_events(sequence),observed_at INTEGER NOT NULL,
 PRIMARY KEY(recipient_department,recipient_id,sequence)
) STRICT;
CREATE TABLE leader_state (
 department TEXT PRIMARY KEY,principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 leader_epoch INTEGER NOT NULL CHECK(leader_epoch>0),delegation_paused INTEGER NOT NULL DEFAULT 0 CHECK(delegation_paused IN(0,1)),
 updated_at INTEGER NOT NULL
) STRICT;
CREATE TABLE leader_handoffs (
 handoff_sequence INTEGER PRIMARY KEY AUTOINCREMENT,department TEXT NOT NULL,
 old_principal_id TEXT REFERENCES principals(principal_id),new_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 old_epoch INTEGER NOT NULL,new_epoch INTEGER NOT NULL CHECK(new_epoch=old_epoch+1),
 decision_id TEXT NOT NULL UNIQUE REFERENCES decisions(decision_id),
 approver_principal_id TEXT NOT NULL REFERENCES principals(principal_id),created_at INTEGER NOT NULL,
 CHECK(approver_principal_id<>new_principal_id),UNIQUE(department,new_epoch)
) STRICT;
CREATE TABLE document_refs (
 file_id TEXT NOT NULL,revision TEXT NOT NULL,
 exported_sha256 TEXT NOT NULL CHECK(length(exported_sha256)=64 AND exported_sha256 NOT GLOB '*[^0-9a-f]*'),
 owner_principal_id TEXT NOT NULL REFERENCES principals(principal_id),department TEXT NOT NULL,
 reader_scope_json TEXT NOT NULL CHECK(json_valid(reader_scope_json) AND json_type(reader_scope_json)='array'),
 source_ref TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(file_id,revision)
) STRICT;
CREATE TABLE document_heads (
 file_id TEXT PRIMARY KEY,revision TEXT NOT NULL,writer_principal_id TEXT REFERENCES principals(principal_id),
 writer_generation INTEGER NOT NULL DEFAULT 0 CHECK(writer_generation>=0),
 writer_token TEXT UNIQUE,writer_expires_at INTEGER,leader_epoch INTEGER NOT NULL CHECK(leader_epoch>0),
 FOREIGN KEY(file_id,revision) REFERENCES document_refs(file_id,revision)
) STRICT;
CREATE TABLE response_attempts (
 response_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL,principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 department TEXT NOT NULL,control_epoch INTEGER NOT NULL DEFAULT 0 CHECK(control_epoch>=0),
 status TEXT NOT NULL DEFAULT 'RUNNING' CHECK(status IN('RUNNING','YIELDED','STOPPED','UNKNOWN')),
 observed_control_epoch INTEGER,started_at INTEGER NOT NULL,ended_at INTEGER,report_json TEXT CHECK(report_json IS NULL OR json_valid(report_json)),
 resume_json TEXT CHECK(resume_json IS NULL OR json_valid(resume_json))
) STRICT;
CREATE INDEX response_status_idx ON response_attempts(department,status,started_at);
CREATE TABLE response_attempt_jobs (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,response_id TEXT NOT NULL REFERENCES response_attempts(response_id),
 job_id TEXT NOT NULL REFERENCES jobs(job_id),attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 outcome TEXT NOT NULL CHECK(outcome IN('CHECKPOINTED','SUBMITTED','YIELDED','STOPPED','UNKNOWN')),
 acceptance_json TEXT NOT NULL CHECK(json_valid(acceptance_json)),evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 created_at INTEGER NOT NULL,UNIQUE(response_id,attempt_id)
) STRICT;
CREATE TABLE response_controls (
 response_id TEXT NOT NULL REFERENCES response_attempts(response_id),control_epoch INTEGER NOT NULL CHECK(control_epoch>0),
 nonce TEXT NOT NULL UNIQUE,action TEXT NOT NULL CHECK(action IN('STOP','DRAIN','CONTINUE','PAUSE')),
 issuer_principal_id TEXT NOT NULL REFERENCES principals(principal_id),leader_epoch INTEGER NOT NULL,
 owner_decision_id TEXT REFERENCES decisions(decision_id),created_at INTEGER NOT NULL,
 PRIMARY KEY(response_id,control_epoch)
) STRICT;
CREATE TABLE supervisory_observations (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,observer_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 subject_id TEXT NOT NULL,kind TEXT NOT NULL,evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),observed_at INTEGER NOT NULL,UNIQUE(observer_principal_id,subject_id,evidence_sha256)
) STRICT;
CREATE INDEX supervisor_subject_idx ON supervisory_observations(subject_id,sequence);
CREATE TABLE recovery_cases (
 case_id TEXT PRIMARY KEY,department TEXT NOT NULL,subject_id TEXT NOT NULL,reason TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('OPEN','ALERT_DELIVERY_PENDING','ALERTED','CLOSED')),
 owner_packet_json TEXT NOT NULL CHECK(json_valid(owner_packet_json)),created_at INTEGER NOT NULL,
 alert_attempts INTEGER NOT NULL DEFAULT 0 CHECK(alert_attempts>=0),next_retry_at INTEGER NOT NULL,
 claim_token TEXT,claim_expires_at INTEGER,delivery_receipt_json TEXT CHECK(delivery_receipt_json IS NULL OR json_valid(delivery_receipt_json))
) STRICT;
CREATE INDEX recovery_due_idx ON recovery_cases(status,next_retry_at,created_at);
CREATE TRIGGER leader_epoch_fence BEFORE UPDATE ON leader_state
WHEN NEW.leader_epoch<OLD.leader_epoch OR (NEW.principal_id<>OLD.principal_id AND NEW.leader_epoch<>OLD.leader_epoch+1)
BEGIN SELECT RAISE(ABORT,'STALE_LEADER'); END;
CREATE TRIGGER document_generation_fence BEFORE UPDATE ON document_heads
WHEN NEW.writer_generation<OLD.writer_generation
BEGIN SELECT RAISE(ABORT,'STALE_DOCUMENT_WRITER'); END;
CREATE TRIGGER response_terminal BEFORE UPDATE ON response_attempts
WHEN OLD.status IN('YIELDED','STOPPED','UNKNOWN')
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_RESPONSE_RECEIPT'); END;
CREATE TRIGGER immutable_department_events_update BEFORE UPDATE ON department_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_department_events_delete BEFORE DELETE ON department_events BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_department_deliveries_update BEFORE UPDATE ON department_deliveries BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_department_deliveries_delete BEFORE DELETE ON department_deliveries BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_leader_handoffs_update BEFORE UPDATE ON leader_handoffs BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_leader_handoffs_delete BEFORE DELETE ON leader_handoffs BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_document_refs_update BEFORE UPDATE ON document_refs BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_document_refs_delete BEFORE DELETE ON document_refs BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_response_attempt_jobs_update BEFORE UPDATE ON response_attempt_jobs BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_response_attempt_jobs_delete BEFORE DELETE ON response_attempt_jobs BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_response_controls_update BEFORE UPDATE ON response_controls BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_response_controls_delete BEFORE DELETE ON response_controls BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_supervisory_observations_update BEFORE UPDATE ON supervisory_observations BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
CREATE TRIGGER immutable_supervisory_observations_delete BEFORE DELETE ON supervisory_observations BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVIDENCE'); END;
