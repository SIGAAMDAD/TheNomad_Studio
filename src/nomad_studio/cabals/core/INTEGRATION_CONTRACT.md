# CAB-A — P2 composition contract

**Public service:** `from nomad_studio.cabals.core import CabalService`

Constructor: `CabalService(storage, bible, authority, *, clock_us, authenticated_worker)`.

- `storage` is an injected CAB-A-only transactional capability with `read(callable(view))` and `write_owned(callable(view))`. Views expose `fetch_one(sql, params)`, `fetch_all(sql, params)`; writable view exposes `execute(sql,params)` ONLY on `v1_cabals`, `v1_memberships`, `v1_deliberations`. All read/write callbacks are executed inside a coherent SQLite single-writer read/transaction boundary; mutations roll back atomically on rejection. **Do not inject unrestricted legacy `StateStore.transact`.**
- `bible` implements frozen CAB-D `BiblePort.snapshot_active_pair`. It must return the strict frozen `BiblePairSnapshot` record, current active pair digest and exact edition IDs/epochs/manifests. CAB-A independently confirms active edition rows in the same database snapshot as admission/task authorization.
- `authority` implements frozen CAB-D `AuthorityPort.assess_bible_conflict`. `open_deliberation` calls it on actual scoped question content; only `status=OK|ACCEPTED` with `code=OK` permits a new group.
- `clock_us` is an authoritative microsecond clock supplied by P2, not agent text. `authenticated_worker` is trusted request-scoped CAB-H identity context, not payload text. For a task, `authenticated_worker()==worker_principal` is mandatory. P2 MUST bind identity to each request/task; never store a mutable global current principal shared across concurrent requests.
- `reserve_cabals` is an internal scheduler/capacity planning entrypoint; P2 must not expose arbitrary reservation to unverified callers. CAB-G owns qualification rows, CAB-H owns worker identity rows; CAB-A only reads them and writes its three tables.
- `v1_tasks.payload_json` must contain exact complete `TaskSpec` JSON corresponding to passed task. Task rows are CAB-C-owned. P2 and CAB-C should preserve the complete record to make task authorization exact.
- `scoped_question` record shape defined by CAB-A (no `$defs` exists): `{"question_text": nonempty UTF-8 string <=16384 chars, "question_digest": sha256(question_text UTF-8), "member_ids": [unique worker IDs]}`. The member subset is checked against the parent roster. Request/replay maps to deterministic subgroup id. Subgroup itself is an authorized cabal with a distinct roster and unchanged scope/Bible pair.
- `Result`/`Decision` in CAB-A export the exact field/order and allowed R9 status/code vocabulary of `CONTRACTS/runtime_ports_v1.py`. P2 can consolidate them with canonical shared types at the composition boundary; NO pre-R9 wire adapter is supported.
- P2 translates trusted storage/adapter operational failures (e.g. SQLite busy/locked, corrupt DB, provider unavailable) at its capability boundary into the frozen R9 operational codes, preserving actual error evidence rather than returning success.

**Known DDL limitations:** see `SQL_CONFORMANCE_NOTES.md`. CAB-A cannot write a feature/parent/question FK or journal receipt without changing frozen schema/ownership. P2 must preserve source of question and compliance proof via its separate immutable trusted evidence facility if later auditing is required.
