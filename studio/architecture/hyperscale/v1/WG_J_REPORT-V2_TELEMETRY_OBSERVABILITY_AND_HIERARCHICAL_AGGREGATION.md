# Workgroup J — Observability and hierarchical aggregation

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Historical design baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Reviewed implementation snapshot (recheck before coding): 505d8e59003e5d5c938c46bc056370ca2a2802fa
- Master plan: 00_ARCHITECTURE.md
- Hardening delta: hardening_patches.v1.json
- Wave: 2

## Objective
Build bounded per-workgroup/department/Director summaries and fair-queue telemetry without exposing private oversight case data.

## Scope
- REPORT-V2
- TELEMETRY

## Dependencies
- A
- C
- D
- E

## Integration gate
Start after required frozen Wave 1 contracts; do not attach live runtimes

## Allowed source paths
- services/coordination/observability/**
- tests/coordination/observability/**

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
- V2 event/authority contract from A
- Core partition and outbox telemetry from C

## Contracts this group publishes
- Worker/workgroup/department aggregate contracts
- Queue lag/error budget and cost meter
- Tamper-aware audit report versioning

## Hardening patches and required proofs
- **H12 — Independent monitor, incident triggers and evidence-based productivity** (HIGH): An external canary failure yields owner-visible alert without duplicating work or masking stale sources

## Work checklist
- Emit distinct registered/observed/verified statuses
- Track queue age, event dedup, CI lag and per-partition hot spots
- Implement page-by-exception drilldown and privacy filters
- Implement assigned hardening patch contracts H12 only after frozen upstream contracts; see hardening_patches.v1.json.

## Mandatory stop conditions
- Do not expose confidential case names or reporter identities
- Do not claim realtime execution for dormant chat sessions

## Required verification
- Director context size bounded with 10k worker fixture
- No invented worker execution status
- Latency stats and error budgets reproducible
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
