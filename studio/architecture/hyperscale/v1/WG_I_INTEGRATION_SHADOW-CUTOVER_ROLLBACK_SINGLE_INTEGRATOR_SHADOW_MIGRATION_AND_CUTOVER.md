# Workgroup I — Single-integrator shadow migration and cutover

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Historical design baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Reviewed implementation snapshot (recheck before coding): 505d8e59003e5d5c938c46bc056370ca2a2802fa
- Master plan: 00_ARCHITECTURE.md
- Hardening delta: hardening_patches.v1.json
- Wave: 3

## Objective
After cleanup baseline readback and owner authorization, connect independently reviewed components to the existing Studio via V1 compatibility adapter and phased shadow deployment.

## Scope
- INTEGRATION
- SHADOW-CUTOVER
- ROLLBACK

## Dependencies
- A
- B
- C
- D
- E
- F
- G
- H
- J

## Integration gate
Start only after all upstream reviews and cleanup baseline reconciliation

## Allowed source paths
- services/coordination/integration/**
- tests/coordination/integration/**
- scripts/studio_comms.py
- scripts/studio_inbox.py
- scripts/studio_ci_evidence.py
- .github/workflows/studio-comms-broker.yml
- .github/workflows/studio-director-pulse.yml
- scripts/studio.py
- scripts/studio_worker_health.py
- .github/workflows/studio-reconcile.yml
- tests/test_studio.py
- tests/test_studio_inbox.py
- tests/test_studio_reconcile_bundle.py
- tests/test_studio_v1_end_to_end.py
- tests/test_studio_worker_health.py

## Protected overlapping paths — do not touch
- studio/runtime/**
- studio/workforce/**
- studio/WORKER_CONTRACT.md
- studio/DIRECTOR_CONTRACT.md
- SIGAAMDAD/{Terrain3D,NomadFramework,The_Nomad,NomadMain,TheNomad_GOAP}/**

## Frozen contracts to consume
- All Wave 0-2 accepted contracts and source SHAs
- Fresh main cleanup baseline, runtime state and owner migration approval

## Contracts this group publishes
- Integration branch and source PR preserving Studio V4
- Single active projector epoch cutover and rollback runbook
- Actual benchmark/QA report with exact SHA

## Hardening patches and required proofs
- **H13 — V1 correctness repairs and compatibility integration** (BLOCKING): Fresh-source independent QA, no unverified runtime gate changes, full integration CI and regression suite green
- **H14 — Shadow-to-live cutover, rollback and bounded real pilot** (BLOCKING): Independent QA signs integrated exact SHA, owner approves deployment/credentials/budget, and rollback drill passes

## Work checklist
- Diff against other cleanup chat before touching hot files
- Run V1 vs V2 shadow projections with identical event stream
- Start two-job pilot then 100/1000 load stages
- Verify data/authorization deltas before cutover
- Preserve source flag and independent QA policy
- Implement assigned hardening patch contracts H13, H14 only after frozen upstream contracts; see hardening_patches.v1.json.

## Mandatory stop conditions
- Stop if cleanup work changes frozen contracts unexpectedly
- Stop on signature/authorization mismatch, lost accepted IDs or source state regression
- Never activate new paid agents or broaden source writes without owner permission
- Do not touch legacy hot controller paths until owner-authorized integration branch and fresh exact SHA baseline are verified.

## Required verification
- Existing Studio validation suite and new tests green
- Two-chat live ACK trace, source CI, QA and owner approval gates
- 100-worker burst + multi-day replay and outages
- Owner-approved rollback rehearsal
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
