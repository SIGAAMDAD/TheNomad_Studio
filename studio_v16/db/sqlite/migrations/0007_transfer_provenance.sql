CREATE TABLE transfer_runs (
 run_id TEXT PRIMARY KEY,source_manifest_sha256 TEXT NOT NULL UNIQUE CHECK(length(source_manifest_sha256)=64),
 source_authority_json TEXT NOT NULL CHECK(json_valid(source_authority_json)),imported_at INTEGER NOT NULL
) STRICT;
CREATE TABLE historical_schema_migrations (
 run_id TEXT NOT NULL REFERENCES transfer_runs(run_id),version TEXT NOT NULL,
 source_row_json TEXT NOT NULL CHECK(json_valid(source_row_json)),PRIMARY KEY(run_id,version)
) STRICT;
CREATE TRIGGER transfer_run_no_update BEFORE UPDATE ON transfer_runs
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TRANSFER'); END;
CREATE TRIGGER transfer_run_no_delete BEFORE DELETE ON transfer_runs
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TRANSFER'); END;
CREATE TRIGGER historical_migration_no_update BEFORE UPDATE ON historical_schema_migrations
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TRANSFER'); END;
CREATE TRIGGER historical_migration_no_delete BEFORE DELETE ON historical_schema_migrations
BEGIN SELECT RAISE(ABORT,'IMMUTABLE_TRANSFER'); END;
