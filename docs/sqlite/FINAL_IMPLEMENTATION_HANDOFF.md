# SQLite and unified add-ons — unvalidated SHADOW source

Repository: SIGAAMDAD/TheNomad_Studio. Branch: studio/v16-sqlite-unified.
Source-only status: IMPLEMENTED_SHADOW_UNVALIDATED. No production GO is claimed.
The user explicitly deferred tests. C0–C7 source is implemented; C8 test work and
all behavioral acceptance gates remain deferred. C9 provides the source handoff.

Original remote base: 8d0b9002e1fedababe72fceffe2a8d6f4bbb3fcd.
Verified A–I restoration commit: fa14928ea36ba68fe607448d690cc00bb64488c1,
tree 8143f39fb7b2f315f9b4c55de33486f260fca22f, matching the supplied Studio archive.
Code candidate: 5dbec6504191ca3be9c82899dbc1c392439ecde2,
tree 9a658ae9ab5887add2785ef79698262a6d4b1a76. The following handoff commit changes
documentation/receipts only; obtain its exact SHA from the branch for review.
Main remained at the original base during final verification. All remote ref
updates used expected-head leases; no history was rewritten or main modified.
The remote SQLite store blob matched local 7685892e2c7eddb6e2aaafc628d32e5d1d878546.

| Checkpoint | Source delivered |
|---|---|
| C0 | DRAFT DB-STORAGE/2-SQLITE, exact baseline/frozen hashes, honest authority status |
| C1 | Strict core schema, checksum migrations, private local WAL owner and bounded writer lane |
| C2 | Explicit transactional claims/checkpoints/reassign/ACK/outbox/import/resume and backend-neutral factory/runtime |
| C3 | Full immutable publication intents, pre-effect dispatch, trusted absence proofs, bounded retries, recovery after revocation/SHADOW |
| C4 | Addressed indexed inbox, ordered observed ACKs, immutable document revisions, fenced document writers, leader epochs, response controls |
| C5 | Pinned document access, leader/control adapters, manual multi-job runner, durable attempt/control observations and report cards |
| C6 | Department controller views, bounded subtree quarantine, compact Git status batching, distinct auditor/watchdog and durable owner alert cases |
| C7 | Online snapshot/manifest/restore, optional actual PostgreSQL snapshot transfer, telemetry and operational runbooks |
| C8 | DEFERRED_BY_USER; no test suites, fault injection, load measurements or operational drills run |
| C9 | Authorized source branch, static integrity receipt and resumable acceptance handoff |

Changed code is in studio_v16/db/sqlite, communications, controller, watchdog,
gateway/adapters/provider integration, studio/v16/worker, and infra/sqlite.
The 19 frozen WG-A files and all four original PostgreSQL migration files are
byte-identical to the supplied A–I source. The separate Oversight archive and
confidential cases were not merged or modified. Existing role registry/manuals
and legacy suites remain in place; their validators were not executed.

## Schema and runtime

Eight immutable checksum migrations are recorded in STATIC_RECEIPT.json. Migration
0003 evolves dispatch retry evidence; department additions therefore begin at 0004
rather than rewriting already committed core migrations. The declared schema has
44 application tables: the original 25, effect_dispatches, effect_absence_proofs,
department_events, department_inbox_cursors, department_deliveries, leader_state,
leader_handoffs, document_refs, document_heads, response_attempts,
response_attempt_jobs, response_controls, supervisory_observations, recovery_cases,
response_job_starts, response_control_observations, job_departments, transfer_runs,
historical_schema_migrations. This is a declaration inventory, not runtime proof.

Required service platform: POSIX local durable filesystem, SQLite >=3.45.0,
Python sqlite3 and the existing jsonschema dependency. The available interpreter
reported SQLite 3.53.1. No database was initialized or deployed in this run.
The one logical owner uses an OS lease plus bounded writer admission and short
BEGIN IMMEDIATE transactions. Connections enforce WAL/FULL, foreign keys,
trusted_schema=OFF and recursive triggers. Workers receive no file handles.

Compose server configuration using MIGRATION_RUNBOOK.md. Privileged credentials,
verified owner/QA evidence, per-principal Scope policy, recovery identity allowlist,
document/leader fences and eligible-job selection must be supplied by the isolated
service. The source does not manufacture those deployment identities or approvals.
No scheduler, external heartbeat host, email delivery account or AI launcher was
provisioned. Twelve optional department archive branches remain disabled.

The manual response runner checks authenticated controls between jobs and records
each claim before execution. Legacy claim calls are fenced to their worker's latest
response once response tracking is in use. Missing reports stay UNKNOWN/UNOBSERVED.
Document readers verify pinned bytes; unsupported historical reads fail closed.
Provider calls run outside transactions. Timeout/unknown effects require readback;
only independently proved absence plus fresh ACTIVE checks permits another dispatch.

## Actual checks and deferred gates

Python compileall and git diff --check passed. A literal-SQL AST review found zero
placeholder/argument mismatches at 136 sites. All 19 frozen hashes and four original
PG migration byte comparisons passed. The source tree and selected remote blob
were read back after leased remote commits. STATIC_RECEIPT.json contains the evidence.

No migrations, SQLite command paths, backups, restores, PostgreSQL transfer,
unit/integration tests, live provider tests, 174-role validator, legacy regression,
failure injection, query plans or load benchmarks were executed. New test suites
were also deferred for the requested speed pass. ACCEPTANCE_STATUS.json explicitly
marks every original SQLite/TX/PUB/RC/STOP/CM/HR/PERF/RECOVER gate NOT_RUN.
There are no measured latency percentiles, contention counts, throughput or RPO/RTO.

PG_EXISTS=UNKNOWN. The optional exporter uses a real READ ONLY REPEATABLE READ
snapshot when supplied an actual operator connection. The importer targets a fresh
SHADOW file, verifies source/table/row digests, archives the PG migration ledger,
preserves terminal evidence and records original authority mode as provenance.
No dataset or transfer success is asserted. No live owner signature, independent
exact-SHA QA, off-host restore, two-invocation manual canary or ACTIVE authorization
exists in this handoff. These gates remain open.

## Next execution

Begin at C8 when the user requests tests. Read IMPLEMENTATION_STATE.json, the exact
branch HEAD and ACCEPTANCE_STATUS.json. Add real-file migration/command/publication,
communication/control, concurrency/crash and backup/restore tests before claiming
behavioral completion. Run focused checks, then the existing full unittest suite,
Studio/production/role validators and scale probe. Repair introduced failures and
tie all results to the tested candidate SHA. Independent QA follows those gates.

Operator initialization, optional transfer and snapshot/restore commands are in
MIGRATION_RUNBOOK.md. ROLLBACK.md requires quiescence, independent provider readback,
preservation of the original DB/WAL and one owner-authorized writer-routing change.
Never promote by manually editing authority_mode or overwrite a live database.
