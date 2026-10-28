# DB-CONTRACT/1 - provider-independent atomic intent and durable reconciliation

## Typed operations

`db-command.v1.schema.json` provides `CLAIM`, `CHECKPOINT`, `ACK`, `REASSIGN`, `RESERVE_PUBLICATION`, `RECONCILE_PUBLICATION`, `OUTBOX_SCHEDULE`, `OUTBOX_ACK` commands. Each is an atomic intent with immutable `command_id`, fenced `job_id`, `attempt_id`, `worker_id`, `assignment_epoch` and `authority_revision`. Each variant has a closed `payload` shape. Implementations MUST reload server-trusted authorization/lease state in the same transaction and must **never trust the request's epochs** as their source of truth. `db-result` yields `COMMITTED`, `DUPLICATE`, `DENIED`, `RETRYABLE_BLOCKED` with a persisted commit sequence for new effects. `DUPLICATE` requires the exact same payload hash; a changed payload reusing a command/idempotency key is `IDEMPOTENCY_CONFLICT`.

| Command | Transaction invariant and storage mapping |
|---|---|
| `CLAIM` | Lock eligible job row; verify owner approval, dependencies, budget and live worker grant; create unique `attempt_id`; advance exclusive lease generation and persist `holder_attempt_id`; commit or no lease. Never launch any chat. |
| `CHECKPOINT` | Verify live attempt/assignment, preserve `STARTED/HEARTBEAT/YIELDED/SUBMITTED/STOPPED` observation semantics; immutable checkpoint digest+artifact URI; append event and advance attempt cursor. `elapsed_observed_seconds` is observed only. |
| `ACK` | `delivery_effects` unique `(event_id, recipient_worker_id, delivery_assignment_epoch, effect_name)`; compare `delivery_assignment_epoch` to live recipient epoch; update cursor in the **same transaction**. ACK external queue only after commit. |
| `REASSIGN` | In one transaction revoke prior assignment, increment **both** `assignment_epoch` and `lease_generation` monotonically, transfer verified checkpoint SHA without transferring a grant. Never reuse retired attempt ID. |
| `RESERVE_PUBLICATION` | Unique `(job_id, artifact_sha256)` plus globally unique stable idempotency key; persist external publication intention/outbox event atomically. No GitHub/Drive API call in transaction. |
| `RECONCILE_PUBLICATION` | A trusted provider adapter records readback `PRESENT/ABSENT/CONFLICT` and exact revision/digest. `PRESENT` alone does not certify the source without matching expected artifact, operation and independent evidence. |
| `OUTBOX_SCHEDULE` | Unique event/destination/kind, canonical payload hash; delivery intent in same transaction as event. Only deterministic notifications/provider requests; **never AI/model/task initiation**. |
| `OUTBOX_ACK` | Mark delivered only after verified provider receipt; repeated ACK is idempotent. |

`db-event` is immutable and `delivery_authority=ARCHIVE_ONLY`. Preserve original V1 event IDs and transport identities; represent transformed observations as separate linked envelopes and do not rewrite old event history. `producer/transport_delivery_id` is unique and separate from `event_id`; a separate `(event_id,consumer_id,assignment_epoch,effect_name)` gate prevents duplicate effects. The schema contains no queue consumer that restarts ordinary ChatGPT chats.

## Recovery and storage

WG-C implements actual PostgreSQL 15+ ACID, row locking/`SKIP LOCKED`, generation fencing, event/inbox/outbox, schema migration ledger and resumable replay. `source_publications` and `document_publications` store proposed/observed exact SHA/revision and exported digest; long content stays in GitHub/Drive. After an external timeout, READ BACK BEFORE RETRY using the identical idempotency key; if already present, reconcile, if absent and authorization current, retry, otherwise block. Crash before commit => no effect; after commit before transport ACK => deduped replay. No SQL transaction wraps an external request, no inferred success from a timeout, and no duplicated work from a changed attempt.

DB authority changes **only** at an owner-approved, signed cutover watermark / baseline change ID after shadow reconciliation. Until then SQL is shadow-only, and GitHub/runtime/Drive historical state remains in original authorities. Migration import preserves unverified events as unverified observations, never authenticated grants. A rollback restores one runtime writer and retains immutable event/attempt/audit receipts; do not drop historical data. The PostgreSQL migration DDL, restore/proxy outage harness and deployment belong to WG-C, not WG-A.
