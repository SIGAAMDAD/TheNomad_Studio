# Hyperscale Studio coordination V1 — design package
Baseline: TheNomad_Studio/main @ aa684e03dc465f9e049f5ac615e1285890f8d72a, before any concurrent cleanup commits. Proposed only; no owner architecture acceptance or production deployment.

Start here:
1. 00_ARCHITECTURE.md — control and data planes, durability, authority, partitioning and batch architecture.
2. 01_CONTRACTS_AND_SEMANTICS.md — events, worker attempts, delivery, ACKs, lease fencing and idempotency.
3. 02_MIGRATION_AND_WORKGROUPS.md — phased, rollback-safe route from V1 to horizontally partitioned V2.
4. 03_CAPACITY_OPERATIONS_TESTS.md — throughput assumptions, benchmarks, failure modes and observability.
5. 04_DEPLOYMENT_AND_SECURITY.md — permissions, external infrastructure and human approval gates.
6. workgroups.json + 00_MASTER_PARALLELIZATION_AND_MERGE_MAP.md — dependency DAG and branch ownership.
7. WG_*.md and CHAT_LAUNCH_PROMPTS.md — ten bounded worker launch briefs for subsequent approved implementation.
8. tools/validate_workgroups.py — offline plan consistency checks. Execute with Python 3.11+: python tools/validate_workgroups.py workgroups.json (from this folder).

Important: the user has another chat cleaning the same repository. This design PR changes only studio/architecture/hyperscale/v1/**. Do not infer the other chat's runtime state or merge this into live scripts without a new baseline inspection.

**Proposed new infrastructure** is not already running: PostgreSQL, queue, blob archive, durable gateway, new GitHub App identities, scaled worker identities, signed Oversight enforcement. A PR containing documentation is not a deployment.

**Existing behavior is unchanged:** GitHub Issue and source governance, runtime/source-write controls, source repository targets, CI/QA owner gates and ordinary ChatGPT manual activation.

Design checks: structured group fields, dependency DAG and obvious same-wave allowed-path conflicts can be checked offline; actual concurrent message throughput, failover, 100/1k/10k active worker tests, storage cost, independent actor authentication and E2E source/QA gates are NOT_RUN until implementation.
