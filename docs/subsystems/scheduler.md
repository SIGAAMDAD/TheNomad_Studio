# WG-E / scheduler (frozen CONTRACT/WORK/1, STATE/1, SOURCE/1)

## Ownership and runtime entrypoint

`nomad_studio.scheduler.api.build_component(deps: Mapping[str, object]) -> Component` provides `execute(CommandEnvelope, VerifiedIdentity) -> Outcome` and `query(QueryEnvelope, VerifiedIdentity) -> Outcome`. The only imported non-standard modules are frozen `nomad_studio.contracts.v1` and this package's own modules. No sibling implementation is imported.

`deps["store"]` is mandatory and must be the **sole** authenticated WG-A `StateStore`. `deps["source"]`, `deps["review"]`, `deps["reconciliation"]` are optional sibling `Component` ports; missing relevant port makes those actions `BLOCKED/NOT_CONFIGURED`. `deps["now_us"]` optionally injects trusted microsecond wall clock (for tests). `deps["config"]` accepts a mapping of bounded static knobs. **The component does not open SQLite, HTTP clients, GitHub connections, or ChatGPT model executions.**

The `scheduler_*` tables are installed atomically with the pinned Studio V1 initial schema when the database is created. No per-component migration registration or embedded SQL fallback is required. The scheduler receives only the shared V1 StateStore.

## Verified identity policy

Every operation requires nonexpired `VerifiedIdentity` with nonempty principal/credential/issuer; its scopes and current authority epoch must originate from WG-B, not caller JSON or prompt text. Generic `scheduler.admin` covers base operation scopes but never overrides special approval, owner, review or worker-routing checks.

| Operation | Required trusted scope and checks |
| --- | --- |
| `scheduler.enqueue` | `scheduler.enqueue` plus `scheduler.approve` **or** `scheduler.owner` |
| `scheduler.reserve` (shift) | `scheduler.reserve`, `worker:<id>`, `role:<id>`, `department:<id>`; QA lane additionally `scheduler.qa_lane`; command assignment epoch equals identity authority epoch |
| `scheduler.reserve` (job) | `scheduler.reserve`, `worker:<id>`, active same-principal/worker/authority-epoch shift, matching approved department, lane and role |
| `scheduler.claim` | `scheduler.claim`, worker binding, active shift, exact assignment epoch/principal/worker/shift, unexpired job lease |
| `scheduler.release` (worker) | `scheduler.release`, bound assigned worker, exact job epoch; shift release requires owning principal/current identity authority epoch |
| `scheduler.release` (review) | `scheduler.release`, **explicit** `scheduler.qa` and non-author principal; independent WG-I verdict; exact submitted subject SHA |
| `scheduler.release` (owner accepted/cancelled) | `scheduler.release`, **explicit** `scheduler.owner`; cancellation with unknown effects requires verified safe reconciliation |
| `scheduler.reassign` | **explicit** `scheduler.reassign` **and** `scheduler.owner`, matching prior epoch, independently verified safe-to-retry reconciliation |
| `scheduler.ready` | `scheduler.read`, either `scheduler.read_all` / `scheduler.admin` or matching verified `department:` and `role:` scopes |

The `worker_id` printed in a prompt is a routing claim. The scheduler requires WG-B-verified `worker:<id>` scope and the active shift's principal and epoch. Old shift/assignment epochs are fenced; owner/source/review scopes cannot be self-asserted in a `CommandEnvelope`.

## Closed command shapes

All commands use `version="NOMAD/CMD/1"`, `domain="scheduler"`, a nonempty idempotency key and bounded `request_id`. If `command.job_id/shift_id/worker_id` and their payload equivalents both appear, they must match. Unrecognized payload or nested job fields are denied. Example formats below use JSON descriptions of `command.payload`, not raw unauthenticated wire requests.

| Operation | Payload fields / behavior |
| --- | --- |
| `enqueue` | `{"jobs":[job,...]}`. 1..64 records by default; **atomic** batch. Owner-approved job refs required, unknown dependency denied; same-batch forward references, cycles, parent depth/child count checked. Starts `APPROVED`. |
| `reserve`, `kind="shift"` | `shift_id`, `worker_id`, `role_id`, `department`, `lane` (`general` or `qa`, default `general`), `ttl_us`. Uses `command.assignment_epoch` = verified identity authority epoch. Distinct active shift per worker/authority epoch. |
| `reserve`, `kind="job"` | `job_id`, `shift_id`, `worker_id`, `ttl_us` and `command.assignment_epoch` = current job assignment epoch. To let scheduler select fairly, set `job_id="auto"` and leave command assignment epoch unset. Source-bearing jobs require `command.source_sha` equal to approved current job base SHA. Returns new **incremented** `assignment_epoch`. |
| `claim` | `job_id`, `shift_id`, `worker_id`; optional `ttl_us` renews a **currently valid** running lease for the same principal/worker/job epoch. Source-bearing jobs require `command.source_sha`. Cannot renew an expired or transferred lease. |
| `release`, `kind="shift"` | `shift_id`; `command.assignment_epoch` = owning shift authority epoch. Releases admission, does not automatically release any job's source/resource leases. |
| `release`, `kind="job"` | `job_id`, `status`, `command.assignment_epoch`; `submitted` needs exact `subject_sha` (40/64 lowercase hex) and assigned `worker_id`. Worker statuses: `submitted`, `yielded`, `blocked`; they preserve fenced resources. QA statuses: `verified` / `rework`, both require `subject_sha`, `review_ref`, separate reviewer and independent review port. Owner statuses: `owner_accepted` (only VERIFIED job), `cancelled` (approved not leased, or independent external-effect reconciliation via `reconciliation_ref`). |
| `reassign` | `job_id`, `replacement_worker_id`, `expected_epoch`, `reconciliation_ref`, optional `new_source_sha` when the owner must rebase a task after a source/QA revision. `expected_epoch` is verified again inside the transaction. Once independently reconciled, increments epoch, clears old leases/assignment, returns to `APPROVED`, and constrains the next reservation to the specified replacement worker. |

Nested `job` schema: mandatory `job_id`, `approval_ref`, `scope_digest` (64 lowercase SHA256), `department`, `domain`, `eligible_roles` (1..64). Optional `kind` (`general`/`qa`), `priority` (0..100, default 50), `repo` (`owner/name`), `branch`, `source_sha` (40 or 64 lowercase hex), `paths` (up to 128), `resources` (up to 128), `dependencies` (up to 64), `retry_budget` (0..32, default 2) and `parent_job_id`. If any source triplet field is present all are required. Repository paths are strict relative POSIX paths: no traversal, glob, `.git`, `//`, empty or special segments; they are case-folded conservatively when leasing. Resources are global exclusive names. Every source job is verified via WG-F `source.check_paths` *before* insertion. The approving principal is derived from `VerifiedIdentity`, not from the payload.

`retry_budget=0` allows one attempted lease; at most `1+retry_budget` attempts. No unbounded job generation: batches, child count and depth are capped. Existing child jobs stay attached to original parent; a QA repair does not erase original job history.

## Ready queries and fairness

`QueryEnvelope(version="NOMAD/QUERY/1",domain="scheduler",operation="ready",payload={"department"?:str,"domain"?:str,"kind"?:str},limit=1..100,cursor?:numeric-created-seq-string)`.

Returns a bounded candidate `jobs` array with `job_id`, `department`, `domain`, `kind`, `priority`, `effective_priority`, `assignment_epoch`, `source_sha`, plus `next_cursor` for keyset paging. Readiness requires approved work, accepted attempt count, and **all** predecessors `VERIFIED` or `OWNER_ACCEPTED`. A candidate is omitted if overlapping resource/path leases remain. SQL filters dependency prerequisites before the bounded scan; caller can page past the current window. `reserve(job_id="auto")` scans at most eight bounded windows when no selectable job is found. A backpressure response does not claim that no eligible job exists beyond this explicit bounded horizon.

The selection score is approved priority + capped age bonus (one unit per `priority_age_us`, default one minute, cap 200). Department dispatch history is used as the **first** fairness key (departments least recently served win), then effective job priority, then creation sequence. Explicit job reservations may bypass advisory fair selection; dispatchers that require fairness must use the `auto` path. Source/readiness checks and the final conditional reservation run independently: approval and lease constraints are rechecked under the sole SQL transaction before acceptance.

The query also returns `admitted_active_shifts`, `general_active_shifts`, `capacity`, `qa_guaranteed_slots`, `source_or_resource_writers`, `registered_shift_workers` and `max_scan`. `registered_shift_workers` means distinct workers *ever observed on scheduler shift admissions*, **not** the authoritative registered slot count; WG-H/WG-B owns that registry. `source_or_resource_writers` counts jobs holding any path or resource lease, not attested live ChatGPT executions.

## Durable schema and transactional behavior

All tables and indexes are exclusively `scheduler_*`: `scheduler_jobs`, `scheduler_dependencies`, `scheduler_shifts`, `scheduler_leases`, `scheduler_dispatch_history`, `scheduler_counters`, `scheduler_idempotency`. Namespaced foreign keys/indexes, unique job/shift IDs, monotonic assignment epochs, retry counters, dependency index and bounded keyset job scan are enforced by SQLite. The StateStore owner must enable WAL/FULL/foreign keys and a serialized mutation queue per WG-A.

Accepted mutations are atomic: state changes + exact request digest idempotency receipt + `StateTx.append_event(DomainEvent)` in the **same** transaction. A journal append fault causes complete rollback. Lost responses after a successful commit replay the same receipt with no duplicate event. An idempotency key reused for a different payload/principal returns `CONFLICT/IDEMPOTENCY_CONFLICT`. External source/review/reconciliation calls occur **outside** the SQL transaction; an absent, throwing, ambiguous or disagreeing provider is blocked/denied, not silently treated as success.

A writer lease is `(repo,branch,normalized-path,job_id,assignment_epoch)`, and the scheduler checks equality plus ancestor/descendant prefix collisions; global resource names are mutually exclusive. Active leases are not expired/removed just because their clock or web chat appears stale. A job marked `YIELDED`, `BLOCKED`, `SUBMITTED`, or `REWORK` retains its existing writer/resource fences until independent evidence supports verified completion, owner cancellation, or authorized reassignment. A QA `PASS` releases locks only after exact subject bound verdict is verified. A replacement/owner cancellation requires external `verified=True` **and** `safe_to_retry=True` bound to `job_id`, `assignment_epoch` and immutable reconciliation ref. A same-worker rework requires a **new** assignment epoch, too.

## Concrete sibling integration contracts (ports, not imports)

| Supplied dependency | WG owner | Query sent by scheduler | Accepted result required |
| --- | --- | --- | --- |
| `store` | WG-A | `StateStore.read`, `StateStore.transact`, `StateTx.append_event` | Atomic, durable, serialized authority, pinned V1 initial schema |
| gateway identity | WG-B | Already-minted `VerifiedIdentity` passed to facade | Live scope/epoch/revocation validation at *every* dispatch; no wire-to-identity trust |
| `source` | WG-F | `source.check_paths` with job, repo, branch, `source_sha`, `scope_digest`, normalized paths, `approval_ref` | `Outcome(ACCEPTED)` plus `verified=True`, exact matching authorized fields |
| `source` | WG-F | `source.verify_base` with repo/branch/approved SHA | `Outcome(ACCEPTED)` plus `verified=True`, matching repo/branch/SHA independently read back |
| `review` | WG-I | `review.verdict` with job, submitted `subject_sha`, `review_ref` | `Outcome(ACCEPTED)` plus exact identifiers and `verdict=PASS` or `REWORK` |
| `reconciliation` | WG-J | `publication.reconcile` with job, old assignment epoch, `reconciliation_ref` | `Outcome(ACCEPTED)` plus exact identifiers and `verified=True, safe_to_retry=True` from independent source/provider evidence |
| shift and registry | WG-D/WG-H | Envelope `shift_id`, job/worker role/scope and epoch | The shift port must never treat an old `assignment_epoch` as authorization or a model prompt worker ID as identity |
| source writer and publication | WG-F/WG-J | Exact scheduler reservation `job_id/assignment_epoch` | Enforce the active lease generation again **at the external write boundary** with source SHA and conditional provider revision, reconcile unknown effects; a scheduler preflight alone cannot stop a later GitHub HEAD race |

**Integration change requests (no frozen-contract edits here):** align exact WG-F/WG-I/WG-J own response payloads to the fields above or provide a composition-root `Component` adapter, and ensure WG-A initializes the pinned V1 schema and commits scheduler audit events in one StateTx. In the installed wheel, verify the one canonical `schema_v1.sql` exists and has the expected SHA-256. WG-Q should load-test the final composed gateway + actual WG-A StateStore with true 800-client concurrent network traffic; this fake-port module test cannot establish production host/connector throughput.

## Capacity and runtime knobs

All caps are **service-side**, independent of model UI settings and account entitlements:

| Setting | Default | Bound |
| --- | ---: | ---: |
| `max_active_shifts` | 500 | 1..500 |
| `qa_guaranteed_slots` | 50 | 0..`max_active_shifts` |
| `max_enqueue_batch` | 64 | 1..128 |
| `max_dependencies` | 64 | 1..128 |
| `max_children_per_parent` | 32 | 1..128 |
| `max_parent_depth` | 8 | 1..64 |
| `max_ready_scan` | 2048 | 1..4096 |
| `priority_age_us` | 60,000,000 | 1..86,400,000,000 |
| `max_lease_ttl_us` | 3,600,000,000 | 1..86,400,000,000 |

General-lane admissions are capped at `max_active_shifts - qa_guaranteed_slots`. QA-lane shifts may fill remaining total capacity; a 501st active shift is denied. Shift expiry is advisory for new operations and does **not** auto-reclaim occupancy without authenticated release/revocation reconciliation; operator/shift protocol must explicitly close shifts or arrange fenced epoch transitions. `APPROVED` jobs cannot self-launch model responses. Server worker/shift accounts are separate from registered roles and actual host-attested running work.

## Failure codes and external gates

Common results: `DENIED/INVALID_SCHEMA`, `DENIED/INVALID_PATH`, `DENIED/OWNER_APPROVAL_REQUIRED`, `DENIED/INVALID_SCOPE`, `DENIED/EXPIRED_GRANT`, `DENIED/STALE_ASSIGNMENT`, `DENIED/STALE_LEASE`, `CONFLICT/STALE_LEASE`, `CONFLICT/SOURCE_SHA_MISMATCH`, `CONFLICT/IDEMPOTENCY_CONFLICT`, `BLOCKED/BACKPRESSURE`, `BLOCKED/QA_PENDING`, `BLOCKED/NOT_CONFIGURED`, `BLOCKED/PROVIDER_UNKNOWN`, `BLOCKED/REBASE_REQUIRED`; accepted mutations have stable event receipt IDs. Store faults propagate as exceptions so callers cannot confuse lost commit acknowledgments with successful or denied operations; retry with same idempotency key for durable recovery.

Local WG-E isolated SQLite port tests cover cyclic DAG, unapproved job, two writer collision, 500/800 capacity under concurrent synthetic requests, QA fairness, old lease epoch denial, independent reconciliation, proof failures, source rebase, idempotent crash/restart, malicious identity and malformed fields, installed-wheel fresh-schema equivalence. The **actual WG-A transaction port, WG-B authenticated gateway and live ordinary ChatGPT connector, final composed WG-F/J/I readbacks, actual paid model execution concurrency, and remote source publication/CI** remain separate unexecuted external/integration acceptance gates; none is asserted to pass by these local tests.
