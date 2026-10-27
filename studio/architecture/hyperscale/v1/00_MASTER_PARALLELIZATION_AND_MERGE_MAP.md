# Master parallelization and merge map

Project: **The Nomad Studio Hyperscale Coordinator V2**

Baseline: **SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a**

Architecture: [00_ARCHITECTURE.md](00_ARCHITECTURE.md)

## Waves

### Wave 0: Freeze shared contracts

- **WG-A** Freeze coordination V2 contracts (EVT-V2, AUTHZ-V2, ACK-V2, SEGMENT-V2)

Dependencies: None

Merge order: A

Acceptance gate: Schema/version/fencing invariants independently reviewed; frozen contract approved by owner

### Wave 1: Independent ingress, store and test harness

- **WG-B** GitHub and signed intake adapter (INGRESS-WEBHOOK, RECONCILIATION)
- **WG-C** Transactional journal and partitioned durable queue (DURABLE-LOG, OUTBOX, QUEUE)
- **WG-H** Synthetic scale and chaos benchmark harness (LOAD-MODEL, CHAOS-TESTS)

Dependencies: 0

Merge order: C -> B -> H

Acceptance gate: Durable insert/outbox and signed intake conformance; chaos harness runs without models

### Wave 2: Parallel projections and protected adapters

- **WG-D** Incremental inbox and acknowledgment projectors (INBOX-V2, ACK-V2)
- **WG-E** Dependency DAG, writer leases and scheduler (DAG-V2, LEASE-FENCE)
- **WG-F** GitHub CI mirror and batch audit bridge (CI-REF, GITHUB-MIRROR, BATCH-ANCHOR)
- **WG-G** Oversight isolation and signed enforcement adapter (OVERSIGHT-NOTICE)
- **WG-J** Observability and hierarchical aggregation (REPORT-V2, TELEMETRY)

Dependencies: 1

Merge order: D -> E -> F -> G -> J

Acceptance gate: Producer contracts unchanged; local integration and negative authorization tests pass

### Wave 3: Single integration, shadow, pilot and cutover

- **WG-I** Single-integrator shadow migration and cutover (INTEGRATION, SHADOW-CUTOVER, ROLLBACK)

Dependencies: 2

Merge order: I

Acceptance gate: Cleanup reconciled, exact-SHA CI + independent QA, shadow comparison, owner deployment approval

## Frozen contracts and ownership

- **WG-A** publishes: Immutable EventEnvelopeV2 schema and v1->v2 mapping; Ingress/queue/storage interfaces, idempotency key specification; Contract tests for source identity, assignment epochs and ACK
- **WG-B** publishes: Webhook/Issue ingress adapter; Cursor-based overlapping reconciliation and replay contract
- **WG-C** publishes: Transactional insert-once journal; Outbox publisher with retries and queue adapter; Partition cursor and segment hash manifest implementation
- **WG-H** publishes: Deterministic workload manifest and seedable generators; Benchmark trace, baseline SLO and chaos injection report
- **WG-D** publishes: Per-worker incremental inbox; Effect-idempotency + per-recipient ACK store; Source-ref based compact inbox context
- **WG-E** publishes: DAG eligibility projection and bounded ready queue; Lease compare-and-swap plus generation fencing; Explicit executors-not-alive status
- **WG-F** publishes: Verified source CI readback adapter; Batch manifest and audit mirror into GitHub; Source SHA mismatch fail-closed fixture
- **WG-G** publishes: Minimal signed notice verification adapter; Negative authorization and replay tests; No-confidential-content trace contract
- **WG-J** publishes: Worker/workgroup/department aggregate contracts; Queue lag/error budget and cost meter; Tamper-aware audit report versioning
- **WG-I** publishes: Integration branch and source PR preserving Studio V4; Single active projector epoch cutover and rollback runbook; Actual benchmark/QA report with exact SHA

Only WG-I may modify existing Studio hot scripts/workflows after cleanup reconciliation and explicit owner authorization. No workgroup may modify production source repositories from the Studio hub.
