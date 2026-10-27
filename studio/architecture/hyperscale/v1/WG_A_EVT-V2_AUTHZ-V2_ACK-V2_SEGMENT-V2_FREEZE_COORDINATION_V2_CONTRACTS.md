# Workgroup A — Freeze coordination V2 contracts

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Master plan: 00_ARCHITECTURE.md
- Wave: 0

## Objective
Define versioned typed event envelopes, reliable source/target IDs, epoch and fencing invariants, transactional journal/outbox interfaces, payload refs and schema compatibility.

## Scope
- EVT-V2
- AUTHZ-V2
- ACK-V2
- SEGMENT-V2

## Dependencies
- None

## Integration gate
Publish contracts before implementation

## Allowed source paths
- services/coordination/contracts/**
- tests/coordination/contracts/**

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
- Existing Studio V1 event and inbox schemas; V4 Issues/Projects approval rules
- Current source CI/QA acceptance and independent Oversight boundary

## Contracts this group publishes
- Immutable EventEnvelopeV2 schema and v1->v2 mapping
- Ingress/queue/storage interfaces, idempotency key specification
- Contract tests for source identity, assignment epochs and ACK

## Work checklist
- Write machine-checkable JSON schemas and typed interfaces
- Assign journal and delivery state transitions
- Document authority reference vs worker self-claim
- Create golden V1 and V2 fixtures and consumer conformance tests

## Mandatory stop conditions
- No code touching live broker/controller
- No downstream implementation until interface ownership and schema version freeze

## Required verification
- JSON schema validation, malformed/unknown-field rejection, duplicate replay, stale epochs
- Independent architecture review and owner acceptance of public coordination contracts

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
