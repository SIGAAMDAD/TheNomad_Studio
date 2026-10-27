# Workgroup B — GitHub and signed intake adapter

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Historical design baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Reviewed implementation snapshot (recheck before coding): 505d8e59003e5d5c938c46bc056370ca2a2802fa
- Master plan: 00_ARCHITECTURE.md
- Hardening delta: hardening_patches.v1.json
- Wave: 1

## Objective
Implement stateless GitHub webhook/Issue-comment intake and reconciliation cursors against the frozen V2 durable append interface.

## Scope
- INGRESS-WEBHOOK
- RECONCILIATION

## Dependencies
- A

## Integration gate
Start after approved Wave 0 contract freeze

## Allowed source paths
- services/coordination/ingress/**
- tests/coordination/ingress/**

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
- EventEnvelopeV2 and ingest API from A
- GitHub webhook signature/delivery semantics and Studio job authority

## Contracts this group publishes
- Webhook/Issue ingress adapter
- Cursor-based overlapping reconciliation and replay contract

## Hardening patches and required proofs
- **H02 — Authenticated ingress and principal-bound event deduplication** (BLOCKING): No privilege granted by unverified transport identity; missed-delivery replay and source lineage are tested

## Work checklist
- Verify webhook signature before accepting traffic
- Commit inbound event before HTTP success
- Persist cursor windows and detect gaps
- Emit bounded rejection codes without raw secret leakage
- Implement assigned hardening patch contracts H02 only after frozen upstream contracts; see hardening_patches.v1.json.

## Mandatory stop conditions
- Do not issue or store permanent owner tokens
- Do not write existing scripts/studio_comms.py or workflows before integration

## Required verification
- Repeated webhook event does not duplicate durable effect
- Missed GitHub events reconcile after interruption
- Mock 429/503/API pagination and signature errors
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
