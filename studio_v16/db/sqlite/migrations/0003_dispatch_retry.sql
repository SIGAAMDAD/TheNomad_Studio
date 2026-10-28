-- Evolve the immutable dispatch ledger without modifying applied core migrations.
-- Copy all existing evidence inside the migrator's single transaction.
ALTER TABLE effect_dispatches RENAME TO effect_dispatches_v1;
CREATE TABLE effect_dispatches (
 dispatch_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 dispatch_id TEXT NOT NULL UNIQUE,
 publication_id TEXT NOT NULL REFERENCES publication_intents(publication_id),
 intent_sha256 TEXT NOT NULL CHECK(length(intent_sha256)=64 AND intent_sha256 NOT GLOB '*[^0-9a-f]*'),
 principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 assignment_epoch INTEGER NOT NULL CHECK(assignment_epoch>0),
 lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
 policy_revision TEXT NOT NULL,
 provider_operation_id TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'DISPATCH_STARTED' CHECK(status='DISPATCH_STARTED'),
 dispatch_started_at INTEGER NOT NULL,
 previous_dispatch_id TEXT UNIQUE REFERENCES effect_dispatches(dispatch_id)
) STRICT;
INSERT INTO effect_dispatches(dispatch_id,publication_id,intent_sha256,principal_id,
 assignment_epoch,lease_generation,policy_revision,provider_operation_id,status,dispatch_started_at)
SELECT dispatch_id,publication_id,intent_sha256,principal_id,assignment_epoch,lease_generation,
 policy_revision,provider_operation_id,status,dispatch_started_at FROM effect_dispatches_v1;
DROP TABLE effect_dispatches_v1;
CREATE UNIQUE INDEX dispatch_one_root_idx ON effect_dispatches(publication_id) WHERE previous_dispatch_id IS NULL;
CREATE INDEX dispatch_publication_status_idx ON effect_dispatches(publication_id,status,dispatch_sequence);
CREATE TRIGGER dispatch_no_update BEFORE UPDATE ON effect_dispatches
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_DISPATCH'); END;
CREATE TRIGGER dispatch_no_delete BEFORE DELETE ON effect_dispatches
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_DISPATCH'); END;
CREATE TABLE effect_absence_proofs (
 dispatch_id TEXT PRIMARY KEY REFERENCES effect_dispatches(dispatch_id),
 verifier_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
 observed_at INTEGER NOT NULL
) STRICT;
CREATE TRIGGER absence_no_update BEFORE UPDATE ON effect_absence_proofs
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_READBACK'); END;
CREATE TRIGGER absence_no_delete BEFORE DELETE ON effect_absence_proofs
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_READBACK'); END;
CREATE TRIGGER dispatch_retry_requires_absence BEFORE INSERT ON effect_dispatches
WHEN NEW.previous_dispatch_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM effect_dispatches d JOIN effect_absence_proofs p ON p.dispatch_id=d.dispatch_id
 WHERE d.dispatch_id=NEW.previous_dispatch_id AND d.publication_id=NEW.publication_id
 AND d.intent_sha256=NEW.intent_sha256)
BEGIN SELECT RAISE(ABORT,'READBACK_REQUIRED'); END;
