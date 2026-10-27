# Workgroup I — Single-integrator shadow migration and cutover

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Master plan: 00_ARCHITECTURE.md
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

## Work checklist
- Diff against other cleanup chat before touching hot files
- Run V1 vs V2 shadow projections with identical event stream
- Start two-job pilot then 100/1000 load stages
- Verify data/authorization deltas before cutover
- Preserve source flag and independent QA policy

## Mandatory stop conditions
- Stop if cleanup work changes frozen contracts unexpectedly
- Stop on signature/authorization mismatch, lost accepted IDs or source state regression
- Never activate new paid agents or broaden source writes without owner permission

## Required verification
- Existing Studio validation suite and new tests green
- Two-chat live ACK trace, source CI, QA and owner approval gates
- 100-worker burst + multi-day replay and outages
- Owner-approved rollback rehearsal

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
