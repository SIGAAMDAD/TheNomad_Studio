# WG-C: PostgreSQL authority-state implementation (shadow only)

Consumes frozen WG-A `DB-CONTRACT/1`, `AUTH-V16/1`, `RUN-V16/1`, and schema IDs under `studio/v16/schemas/`. Owns only `studio_v16/db/**`, `infra/db/**` and `tests/v16/db/**`. This package does not modify V1 controller hot paths, create a managed database, grant real provider access, or enable automated ChatGPT execution.

## Storage surface for WG-F and WG-D

- `PostgresStore(lambda: psycopg.connect(dsn, autocommit=True))` constructs the storage adapter. Each `execute(command, trusted_context)` call uses one new connection and one transaction. It accepts the eight closed WG-A DB command variants, returning a `DB-CONTRACT/RESULT/1` object. Denials roll back all writes. Exact duplicate command ID/payload returns `DUPLICATE`; changed payload with same key returns `IDEMPOTENCY_CONFLICT`.
- `GatewayContext` is **in-process, gateway-supplied trusted state**, **not** an authentication protocol. WG-D must derive `principal_id`, provider readback/receipt evidence and source base from isolated authenticated credentials and current authoritative provider state. Do not expose `GatewayContext` construction to untrusted users/ChatGPT workers or use shared-account connectors. The database checks live job, assignment, approval, expiry, revocation, source and grant records under lock; it does not replace WG-D's independent issuer/scope/path/branch validation.
- `record_event(envelope, context)` deduplicates `(producer, transport_delivery_id)` independently of `(event_id, consumer_id, assignment_epoch, effect_name)`. Every imported/derived event retains `delivery_authority=ARCHIVE_ONLY`; it is never automatically a model dispatch.
- `reserve_outbox_delivery(context, ..., shadow_test=False)` reserves **only** deterministic provider/notice kinds. In SHADOW mode it refuses delivery unless called with `shadow_test=True` in an isolated local fake-provider test. External publication takes place **after** the SQL transaction by WG-G. Never interpret a successful reservation as a published effect.
- `mark_delivery_retry(...)`/expired claim transitions to `RECONCILE_REQUIRED`, **not** an automatic retry. WG-G must read provider state and call `reconcile_outbox(..., readback_state, observed_sha256, provider_ref, context)`. `PRESENT` with matching SHA means delivered, `ABSENT` schedules bounded exponential retry, conflict blocks. Return of an `OUTBOX_ACK` command requires verified receipt and matching digest. Pending publications similarly require `RECONCILE_PUBLICATION` with authenticated readback; matching source artifact remains `EXTERNAL_OBSERVED` until independent QA.
- `shadow_import(...)` and `reconcile_shadow(...)` store immutable GitHub/Drive/ledger snapshots and readback mismatches. Imports are observations, **never grants**. `resume_capsule(job_id, context)` returns a narrow DB state summary; `save_resume_capsule` and `load_resume_capsule` persist an exact, schema-valid WG-A `RUN-V16/RESUME/1` worker observation after an authenticated checkpoint. Workers/host must supply real evidence; there are no fabricated defaults.

`PostgresStore` deliberately has **no external network calls, GitHub/Drive clients, cutover method, AI/worker launch method, or production credentials**. `authority_mode` starts `SHADOW`; switching to ACTIVE requires later signed owner-gated release by WG-I, with independently reviewed gateway and rollback plan. WG-C does not perform that transition.

## Local testing (requires a disposable PostgreSQL 15+ instance)

```sh
# Local only, never point tests at a managed/production database.
docker compose -f infra/db/docker-compose.yml up -d --wait
python -m pip install -r infra/db/requirements.txt
export NOMAD_V16_TEST_PG_DSN='postgresql://nomad_test:local_test_only@127.0.0.1:55432/nomad_test'
python -m unittest discover -s tests/v16/db -p 'test_*.py' -v
# On a separate, empty restore-test database for pg_dump/pg_restore verification:
# export NOMAD_V16_RESTORE_TEST_PG_DSN='postgresql://...another disposable DB...'
# python infra/db/verify_backup_restore.py
```

`test_postgres_integration.py` refuses a preexisting `nomad_v16` schema, runs checksum migration replay/concurrent claims/replay/outbox/fences, and drops the disposable schema only at the end. The migration tool is `python -m studio_v16.db.migrate --dsn <local-DSN>`. Explicit destructive rollback requires `--rollback-disposable-test-only` and checks for `SHADOW`; production rollback changes **authority writer routing**, preserving the durable journal and snapshots. Verify backup/restore on separate disposable DB, then independently test PITR under the selected managed provider before cutover.

**Runtime roles:** migration role owns schema; gateway runtime role has only needed DML and sequence access; dashboard role is read-only. `infra/db/roles.template.sql` is a template, not deployed roles. Always use provider TLS/private routing, credential isolation, secret manager, backup/PITR and off-database immutable audit anchors in production.

## Remaining integration work

WG-D: verified issuer/credential isolation, live branch/path/QA evidence and signed owner decisions; WG-F: controller shadow projector, real resume metadata and reconciliation API wiring; WG-G: provider readback, idempotency-aware saga and fake-provider crash tests; WG-H: independent exact-artifact PostgreSQL/concurrency/security/PITR verification; WG-I: owner-authorized release and cutover/rollback with **one** runtime writer. Without these gates this package is an independently testable storage component, not a deployed secure Studio.

## Publication authority integration repair

See [PUBLICATION_AUTHORITY_V2.md](PUBLICATION_AUTHORITY_V2.md) for migration 0002, provider callback wiring, exact lock order, full-intent idempotency and required independent QA. The old partial RESERVE/RECONCILE DB commands fail closed.
