# Workgroup B — GitHub and signed intake adapter

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Master plan: 00_ARCHITECTURE.md
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

## Frozen contracts to consume
- EventEnvelopeV2 and ingest API from A
- GitHub webhook signature/delivery semantics and Studio job authority

## Contracts this group publishes
- Webhook/Issue ingress adapter
- Cursor-based overlapping reconciliation and replay contract

## Work checklist
- Verify webhook signature before accepting traffic
- Commit inbound event before HTTP success
- Persist cursor windows and detect gaps
- Emit bounded rejection codes without raw secret leakage

## Mandatory stop conditions
- Do not issue or store permanent owner tokens
- Do not write existing scripts/studio_comms.py or workflows before integration

## Required verification
- Repeated webhook event does not duplicate durable effect
- Missed GitHub events reconcile after interruption
- Mock 429/503/API pagination and signature errors

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
