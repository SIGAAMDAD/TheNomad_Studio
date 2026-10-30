# WG-C — storage source handoff

## Provenance / scope

- Input archive: `Nomad_Studio_R5_Executable_One_Wave_Replan.zip` SHA-256 `84ba2f2a7fc038e656684f16aa4f9d75ffc398593703462205369889230e5a4b`.
- Plan file SHA-256: `8d68e255008369a299c66ca1a0c253e60faecb2ef46415ad09cbb9b38b41d09b`.
- Source baseline: **greenfield empty source tree**, no existing Studio source merged. `CONTRACTS/initial_schema.sql` and `CONTRACTS/state_machines.json` are copied byte-for-byte into `schema/`; `schema/table_owners.json` transcribes `ARCHITECTURE/14_EXCLUSIVE_STATE_DOMAIN_OWNERSHIP.md` (66 records).
- Changed files only: `src/studio/storage/**`, `schema/**`, `tests/unit/c/**`. No protected contracts, worker ownership, API, host, UI or other workgroup paths modified. No branch created or remote commit made; WG-M owns canonical integration.

## Implemented

1. Fresh-schema-only APSW store: `ScopedStore(fresh=True)` installs frozen SQL; reopen validates **all tables, indexes and triggers** against the same APSW engine, foreign keys and database quick check. WAL, full synchronous mode and busy timeout. No migrations or stdlib `sqlite3` runtime use.
2. `ScopedTransaction.begin/execute_named/select_named/commit/rollback`, owner-bound domain capabilities, authenticated-context project filtering, complete-PK/CAS updates, keyset pages (1–250 rows), named bounded `count`/`sum`, and frozen state machine transitions. No generic worker SQL endpoint. Domain bindings must be minted and sealed by trusted composition before exposing the service.
3. Project isolation for Bible/task/approval records and global byte deduplication behind `project_blobs`. Physical SHA-256 verification and durable atomic blob persistence, with no business read path to other-project blobs. Unscoped backup images must not be granted project-blob access.
4. Request-body SHA-256 idempotency including first-project bootstrap, reply-by-content-digest replay, pending/UNKNOWN handling, exact `effect_intents` outbox with verified-readback API (physical remote authentication belongs to G), and fenced `leases` compare-and-swap for D.
5. `RelayCursorJournal`: persist/read verified ref SHA, decline ambiguous/incomplete scans, CAS cursor advance. Manual browser chats are never launched by C; GitHub traversal/byte verification belongs to G/I.
6. Full-Studio snapshot via APSW online backup, blob inventory, durable manifest, exact SQL+foreign key verification and atomic restore to a **new** database path, with readback digest and fsynced recovery log. `backup_sets` registration verifies actual APSW SQLite bytes. Actual live promotion and per-project recovery policy remain WG-M-owned.
7. Bounded task queue/count aggregations, graph reachability/cycle check with fail-closed traversal cap, and index-use/1,000-row pagination fixture.

## Exact tests and observed status (Python 3.13.5 / SQLite reference 3.46.1)

| Command / check | Status | Evidence |
|---|---|---|
| `PYTHONPATH=<package>/src python -m unittest discover -s <package>/tests/unit/c -v` | **15 PASS, 1 SKIPPED** | Offline `sqlite3` reference-oracle logic, real content hashing and filesystem writes. APSW runtime test skips because the package is unavailable. |
| `python <input>/AUDIT/test_sql_reference.py` | **PASS** | Frozen 66-table SQL reference; all embedded adversarial cases rejected. |
| `python <input>/tests/test_forge_alignment.py` | **PASS** | 13 owners, 66 state writers, frozen operation alignment. |
| `PYTHONPATH=<package>/src python -m compileall -q <package>/src/studio/storage` | **PASS** | Python syntax compiled. |
| Production AST scan for `import sqlite3` and frozen-file byte equality | **PASS** | 12 Python production files, zero stdlib SQLite imports; initial SQL and state machine bytes unchanged. |
| APSW-specific database installation, WAL, backup, real rollback, restoration | **NOT_RUN** | `apsw` unavailable; attempted `python -m pip install apsw -q`, failed DNS resolution. No alternate production driver was used or claimed as equivalent. |
| Cross-worker API/GitHub/remote/CI/browser integration | **NOT_RUN** | No other worker implementation, credentialed GitHub adapter or live browser session available in this isolated source packet. |
| WG-L independent acceptance / WG-M integrated promotion | **NOT_RUN** | Requires exact canonical merge and independent QA. |

## Integration contracts / remaining checks

- **WG-M:** add APSW dependency in its owned `pyproject.toml`; initialize this store from the greenfield DB, bind each domain once at bootstrap, seal bindings, and supply source path resolution. Do not import or reimplement another SQLite driver. Use a single canonical integration tree.
- **WG-J:** inject authenticated `ActorContext` with real principal identity, `allowed_project_ids`, and `trusted_proof_ref`. This local store enforces the resulting scope; it cannot verify a caller-authored proof string against a provider.
- **WG-A/B/D/E/F/G/H/I:** invoke owner-binding-scoped named commands, and use `IdempotencyJournal` for local replay before performing business writes. State updates use frozen transition rules. D uses `cas_lease` and `ProductionQueries`; I uses `RelayCursorJournal`. No browser-authored SQL is accepted.
- **WG-G:** independently verify exact remote effect path/hash/commit before calling `EffectJournal.record_readback`. Register physically verified whole-database backup blobs through `ContentAddressedStore.register_verified_unscoped` without project linkage; C may then register a verified `backup_sets` row. Project artifacts use `put_project`/`read_project`.
- **WG-L:** rerun `test_apsw_runtime.py` with real APSW, expand with race/kill/fault injection and tested production composition; do not credit SQLite-reference tests as APSW runtime proof.
- **WG-M:** only privileged recovery/promotion can replace a running Studio DB; `restore_to_fresh_database` intentionally cannot overwrite existing databases. A full-Studio DB image must not be exposed as a per-project artifact, since it contains multiple projects.

These are handoff dependencies and verification boundaries, **not additional ratification gates** for independently runnable source.
