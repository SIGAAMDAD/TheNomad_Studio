# Workgroup C — Transactional journal and partitioned durable queue

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Master plan: 00_ARCHITECTURE.md
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

## Frozen contracts to consume
- EventEnvelopeV2, storage and queue interfaces from A
- Owner-approved storage/queue cost ceiling before cloud provisioning

## Contracts this group publishes
- Transactional insert-once journal
- Outbox publisher with retries and queue adapter
- Partition cursor and segment hash manifest implementation

## Work checklist
- Use unique constraints for raw and transport IDs
- Transactional event+outbox commit
- Per-job FIFO with distinct group IDs
- Bound archive segment size and record SHA256 chain
- Provide local emulated queue for deterministic tests

## Mandatory stop conditions
- Do not claim queue deduplication equals exactly-once
- Stop at local test fixtures if external storage/secrets are not authorized

## Required verification
- Crash injection before/after commit and queue ACK
- No loss of durably accepted IDs and no duplicate state effects
- Multiple job partitions make independent progress

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
