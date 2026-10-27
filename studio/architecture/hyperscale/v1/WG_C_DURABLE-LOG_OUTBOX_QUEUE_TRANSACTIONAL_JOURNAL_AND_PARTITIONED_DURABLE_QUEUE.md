# Workgroup C — Transactional journal and partitioned durable queue

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Historical design baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Reviewed implementation snapshot (recheck before coding): 505d8e59003e5d5c938c46bc056370ca2a2802fa
- Master plan: 00_ARCHITECTURE.md
- Hardening delta: hardening_patches.v1.json
- Wave: 1

## Objective
Implement PostgreSQL idempotent accepted event ledger, atomic outbox and queue publisher, per-job partition ordering, retries, checkpoint recovery and immutable segment manifest interface.

## Scope
- DURABLE-LOG
- OUTBOX
- QUEUE

## Dependencies
- A

## Integration gate
Start after approved Wave 0 contract freeze

## Allowed source paths
- services/coordination/core/**
- tests/coordination/core/**

## Protected overlapping paths — do not touch
- studio/runtime/**
- studio/workforce/**
- studio/WORKER_CONTRACT.md
- studio/DIRECTOR_CONTRACT.md
- SIGAAMDAD/{Terrain3D,NomadFramework,The_Nomad,NomadMain,TheNomad_GOAP}/**
- scripts/studio_comms.py
- scripts/studio_inbox.py
- scripts/studio_ci_evidence.py
- .github/workflows/studio-*.yml
- scripts/studio.py
- scripts/studio_worker_health.py
- tests/test_studio.py
- tests/test_studio_inbox.py
- .github/workflows/studio-reconcile.yml

## Frozen contracts to consume
- EventEnvelopeV2, storage and queue interfaces from A
- Owner-approved storage/queue cost ceiling before cloud provisioning

## Contracts this group publishes
- Transactional insert-once journal
- Outbox publisher with retries and queue adapter
- Partition cursor and segment hash manifest implementation

## Hardening patches and required proofs
- **H03 — Transactional accepted events and GitHub publication saga** (BLOCKING): All acknowledged accepted IDs recover after crash; no duplicate protected side effects or fabricated VERIFIED state
- **H04 — Backup, immutable archives, lifecycle retention and disaster recovery** (BLOCKING): A timed, witnessed restore drill passes measured owner-approved RPO/RTO, integrity and privacy checks

## Work checklist
- Use unique constraints for raw and transport IDs
- Transactional event+outbox commit
- Per-job FIFO with distinct group IDs
- Bound archive segment size and record SHA256 chain
- Provide local emulated queue for deterministic tests
- Implement assigned hardening patch contracts H03, H04 only after frozen upstream contracts; see hardening_patches.v1.json.

## Mandatory stop conditions
- Do not claim queue deduplication equals exactly-once
- Stop at local test fixtures if external storage/secrets are not authorized

## Required verification
- Crash injection before/after commit and queue ACK
- No loss of durably accepted IDs and no duplicate state effects
- Multiple job partitions make independent progress
- Independently verify every assigned hardening patch completion gate and test fixture; do not count design checks as live throughput.

## Deliverables
- Reviewed source PR on separately authorized implementation branch with exact source SHA
- Tests and evidence links (PASS/FAIL/NOT_RUN distinguished)
- V4 immutable event ledger handoff; unmerged until owner review

## Studio-wide requirements
- DESIGN ONLY until the Lead Director approves the architecture, Cloud/App credentials and each source-work order.
- Obtain a fresh exact main SHA and reconcile the other chat's cleanup before any integration touching existing scripts.
- One writer per mutable path/ABI; no cross-repository source implementation from the Studio hub.
- Do not alter studio/runtime source_writes_enabled=false, auto_ai_enabled=false, writer/reviewer capacity or owner merges.
- Never claim ordinary ChatGPT conversations can be autonomously awakened.
- Use actual GitHub source branches and commit SHA evidence; no ZIP handoffs or invented test passes.
- Read V4 department role playbooks and append an immutable department ledger event after every substantive response.
- Plan baseline aa684e03 is historical provenance only; resolve current main/runtime exact SHAs and policy revisions before actual implementation.
- Mandatory hardening items H00-H14 are proposed execution/verification gates, not permission to deploy, spend or modify production.
- Any oversight code or case-store changes require a separately approved scoped source job in TheNomad_Studio_Oversight; no production worker key access.
