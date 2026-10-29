# CAB-C R9 SQL conformance — frozen new-only R8 schema

**Pinned source SHA-256:** `237ef363f4206df180b31f1ab8ff147916b9b3660f8f23756eeb31f733356d43`.
This CAB owns precisely six writer tables; the P2 root retains database installation and mandatory single-authority locking. The tests carry byte-for-byte copies of the R8 frozen DDL and writer map in `tests/production/workgraph/fixtures/`. These are **test fixtures**, not an alternative runtime schema, migration or compatibility layer.

| Frozen table | Writes made by CAB-C | Constraints relied upon |
|---|---|---|
| `v1_graph_revisions` | Insert immutable graph revision + SHA-256 | `(feature_id, graph_revision)` PK, `feature_id` FK, `graph_digest`, `contract_digest`, `bible_pair_digest` |
| `v1_tasks` | Insert once per revision; update state and verified `output_manifest_ref` | Globally unique `task_id`; `(feature_id, graph_revision)` references frozen graph; single feature lineage |
| `v1_dependencies` | Insert exact same-feature parent and child per new revision | `CHECK(parent_id<>child_id)` and composite `(feature_id,parent_id/child_id)` FKs; DAG/cross-revision checked by service |
| `v1_worker_leases` | Insert epoch-fenced lease; set `fenced=1` | Lease ID PK; task FK; `epoch>0`, `fenced` boolean |
| `v1_task_attempts` | Insert attempt; advance terminal observed state/result digest | Attempt ID PK; task FK; `v1_job_attempt_lookup` index |
| `v1_task_checkpoints` | Insert authenticated checkpoint content digest and receipt reference | Checkpoint ID PK; task FK |

All other tables are **read-only** from this CAB: `v1_feature_runs` (CAB-J, active graph revision CAS, phase), `v1_feature_contracts` (CAB-B, immutable feature requirement and repository/branch scope), and `v1_host_sessions` (CAB-H, authenticated observed session). The service must only receive a P2-issued **CAB-C table-authorized writer**, not a generic SQLite connection/administrator or an unrestricted `StateStore.transact`. `writer.read(callable)` permits bounded read snapshots. `writer.write(callable)` executes under one serialized `BEGIN IMMEDIATE` with `PRAGMA foreign_keys=ON`, rollback on exception, and write permission only on these six tables. Preflight verifiers/host callbacks are invoked outside that transaction.

The task ID convention is `feature:r<graph_revision>:<logical_task_id>` so the frozen `task_id` global PK never overlaps between revisions. `FeatureRun.graph_revision` is advanced exclusively by CAB-J/P2 after a new immutable graph has committed; CAB-C does **not** edit CAB-J's row. Scheduler rejects absent/stale/nonactivated graph revisions. Rework inserts a full replacement revision with affected descendants reset and unchanged verified `output_manifest_ref` values preserved. Old leases are fenced before creating replacement tasks in a single CAB-C writer transaction. The resulting graph is **not dispatchable** until P2/CAB-J performs activation. No component mutates frozen DDL.

The frozen schema does not provide a table-level CHECK for scheduler attempt status or an epoch-uniqueness index. The CAB-C writer serializes lease CAS and checks the latest epoch and expires-at before every checkpoint or finish. A multiple-process SQLite-writer configuration would defeat these guarantees and is not allowed. `RECOVERY_BLOCKED` is not redispatched after an expired lease until an authenticated provider **readback** is supplied to `reconcile_expired`; its absence is not inferred from an old chat or stale in-memory receipt. Native proof is a P5 external acceptance gate; tests use explicit simulated verifiers.

## P2 wiring prerequisites

1. Import `nomad_studio.production.workgraph.api.WorkGraphService` and `nomad_studio.scheduler.api.SchedulingService`. `nomad_studio.shift.api` is their internal attempt helper. The legacy `build_component` interface has been removed. P2 alone changes `app.py`, central contracts, integration tests and packaging.
2. Supply the table-limited writer described above, a trusted monotonic/persistent microsecond clock, CAB-G `plan_guard`, CAB-G/CAB-A `allocation_guard`, `placement_guard` bound to current worker scope/skills/budget and immutable task digest, CAB-H `checkpoint_guard` bound to real native session/epoch readback, CAB-I/CAB-E `completion_guard` that checks exact verified artifact bytes, and P2/CAB-E/owner `rework_guard`. A recovery loop supplies CAB-H `recovery_guard` that verifies authentic provider state. Every guard returns the frozen `Decision` shape with `allowed/status/code/affected_ref/evidence_refs`; no unverifiable evidence ref is sufficient for production.
3. Source task resources are canonical relative POSIX resource keys; CAB-I maintains authoritative repo/branch ancestor/descendant source leases. CAB-C checks logical graph and overlapping active task resource claims but must not write CAB-I's lease tables or invoke source providers inside writer transactions.
4. P2/CAB-J atomically mediates `FeatureRun.graph_revision` activation and pause/cancel transitions. Graph revisions should be published only when dependent CAB-C changes and CAB-J active revision compare-and-set succeed. The frozen two-owner schema requires orchestrated *separate authorized transactions*; do not grant CAB-C rights to the director's run table to create apparent atomicity.
5. P2 must read back actual provisioned host sessions (CAB-H writer; `observed_real=1`), verify corresponding identity/scope in `placement_guard`, then drive dispatch. `lease_task` never manufactures a production host session. Expired attempts use `fence_expired` then `reconcile_expired` after provider readback. Production delivery still requires authenticated live-host and exact-commit QA evidence.

## Executed evidence scope

- Owned tests: exact R8 SQLite DDL in-memory with FK checks and simulated CAB-G/CAB-H/CAB-I guard results; task DAG/contract/schema, output coverage, collision, dependency, orphan graph, pause, stale session, retry and rework negative cases.
- **Not executed by CAB-C:** P2 22-port composition, production CAB-G/H/I adapters, real host/session activation, independent QA, full integrated regression, cold deployment/recovery, PR CI, merge, live service or pilot.
