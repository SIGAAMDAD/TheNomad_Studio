# Hyperscale Studio coordination V1 — design package
Original design baseline: TheNomad_Studio/main @ aa684e03dc465f9e049f5ac615e1285890f8d72a. Reconciled with merged Studio 1.0.0 cleanup PR #24 @ 49c3a5bba445f1dd927b930283ffdeb3bbbb8b52. Proposed only; no owner architecture acceptance or production deployment.

Start here:
1. 00_ARCHITECTURE.md — control and data planes, durability, authority, partitioning and batch architecture.
2. 01_CONTRACTS_AND_SEMANTICS.md — events, worker attempts, delivery, ACKs, lease fencing and idempotency.
3. 02_MIGRATION_AND_WORKGROUPS.md — phased, rollback-safe route from V1 to horizontally partitioned V2.
4. 03_CAPACITY_OPERATIONS_TESTS.md — throughput assumptions, benchmarks, failure modes and observability.
5. 04_DEPLOYMENT_AND_SECURITY.md — permissions, external infrastructure and human approval gates.
6. 05_V1_CLEANUP_RECONCILIATION.md — Studio 1.0.0 merged changes and V2 compatibility constraints.
7. workgroups.json + 00_MASTER_PARALLELIZATION_AND_MERGE_MAP.md — dependency DAG and branch ownership.
8. WG_*.md and CHAT_LAUNCH_PROMPTS.md — ten bounded worker launch briefs for subsequent approved implementation.
9. tools/validate_workgroups.py — offline plan consistency checks. Execute with Python 3.11+: python tools/validate_workgroups.py workgroups.json (from this folder).

Important: the reported cleanup merged as PR #24. This design PR only adds or revises studio/architecture/hyperscale/v1/**. The merge is not evidence of the other chat's current running status. Re-read exact SHA before touching live code.

**Proposed new infrastructure** is not already running: PostgreSQL, queue, blob archive, durable gateway, new GitHub App identities, scaled worker identities, signed Oversight enforcement. A PR containing documentation is not a deployment.

**Existing behavior is unchanged:** GitHub Issue and source governance, runtime/source-write controls, source repository targets, CI/QA owner gates and ordinary ChatGPT manual activation.

Design checks: structured group fields, dependency DAG and obvious same-wave allowed-path conflicts can be checked offline; actual concurrent message throughput, failover, 100/1k/10k active worker tests, storage cost, independent actor authentication and E2E source/QA gates are NOT_RUN until implementation.

## Source-reviewed hardening delta (October 27, 2026)

- **09_RELIABILITY_AND_EXECUTION_PATCH_PLAN.md** — fifteen H00-H14 bounded patches based on the uploaded Studio and Oversight source, ranked by architectural consequence, prerequisites, exact ownership and fault tests.
- **hardening_patches.v1.json** — machine-readable patch DAG incorporated into the **same ten workgroups and four waves**.
- **tools/validate_hardening_plan.py** — offline validation of patch ownership, stage gates, dependency graph, and negative plan mutation checks.
- Implement from the **current** Studio main/runtime SHAs; the original `aa684e...` design baseline is not the implementation baseline.
- **Both uploaded snapshots passed their existing unit suites:** Studio 167 tests, Oversight 23. This is not live traffic capacity or external account security proof.
- **This branch is plan-only.** It neither fixes the identified controller code bug nor changes Oversight access, scheduled tasks, live queues, model execution or source permissions.
