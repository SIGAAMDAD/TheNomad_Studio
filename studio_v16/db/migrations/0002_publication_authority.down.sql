-- Disposable-test rollback only. Historical 0001 records remain untouched.
DROP TABLE IF EXISTS nomad_v16.publication_intents;
DROP FUNCTION IF EXISTS nomad_v16.protect_publication_intent();
ALTER TABLE nomad_v16.source_publications DROP COLUMN IF EXISTS patch_sha256;
