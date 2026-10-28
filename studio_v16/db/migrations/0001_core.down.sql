-- DESTRUCTIVE: only for an empty disposable test database or after a verified backup.
-- Production rollback switches writer authority; it MUST NOT discard historical tables.
DROP SCHEMA nomad_v16 CASCADE;
