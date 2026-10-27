# Workgroup D — Incremental inbox and acknowledgment projectors

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Historical design baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Reviewed implementation snapshot (recheck before coding): 505d8e59003e5d5c938c46bc056370ca2a2802fa
- Master plan: 00_ARCHITECTURE.md
- Hardening delta: hardening_patches.v1.json
- Wave: 2

## Objective
Implement sharded receiver projections and bounded, cursor-based inboxes; ACK validation tied to exact recipient, assignment epoch and original event.

## Scope
- INBOX-V2
- ACK-V2

## Dependencies
- A
- C

## Integration gate
Start after required frozen Wave 1 contracts; do not attach live runtimes

## Allowed source paths
- services/coordination/projections/**
- tests/coordination/projections/**

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
- Frozen EventEnvelopeV2/ACK contract from A
- Core journal/outbox and partition cursor from C

## Contracts this group publishes
- Per-worker incremental inbox
- Effect-idempotency + per-recipient ACK store
- Source-ref based compact inbox context

## Hardening patches and required proofs
- **H06 — Assignment-epoch inbox and ACK semantics** (BLOCKING): Reassignment and 100k-event historical replay pass without unbounded O(all-events) steady-state work
- **H07 — Verified project resume capsule and context reuse** (HIGH): Capsule rebuild deterministically from immutable refs, and restart recovers last accepted work without context drift

## Work checklist
- Apply deltas only to affected recipient/job keys
- Separate RECEIVED, AVAILABLE, ACKED and SUPERSEDED states
- Reassign pending deliveries without accepting stale ACKs
- Allow bounded pagination and deterministic rebuild
- Implement assigned hardening patch contracts H06, H07 only after frozen upstream contracts; see hardening_patches.v1.json.

## Mandatory stop conditions
- No rewriting old studio/comms history
- No direct privileged runtime state mutations from untrusted events

## Required verification
- Replay any event twice; one state effect
- Reassignment/old-epoch ACK attack blocked
- Incremental projection matches reference full replay
- Load harness reports no unbounded per-event full-history work
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
