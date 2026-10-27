# Workgroup E — Dependency DAG, writer leases and scheduler

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Historical design baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Reviewed implementation snapshot (recheck before coding): 505d8e59003e5d5c938c46bc056370ca2a2802fa
- Master plan: 00_ARCHITECTURE.md
- Hardening delta: hardening_patches.v1.json
- Wave: 2

## Objective
Implement versioned dependency readiness, path-overlap conflict detection and deterministic fenced ownership; keep actual ChatGPT execution separate.

## Scope
- DAG-V2
- LEASE-FENCE

## Dependencies
- A
- C
- D

## Integration gate
Start after required frozen Wave 1 contracts; do not attach live runtimes

## Allowed source paths
- services/coordination/scheduler/**
- tests/coordination/scheduler/**

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
- Frozen job/authorization/epoch contract from A
- Core journal transaction API from C
- V4 source ownership and protected paths

## Contracts this group publishes
- DAG eligibility projection and bounded ready queue
- Lease compare-and-swap plus generation fencing
- Explicit executors-not-alive status

## Hardening patches and required proofs
- **H08 — Actual shift executor adapters and admission/budget policy** (BLOCKING): Verified supported executor integration with approved cost ceiling and no side effects for disabled auto_ai/source_writes
- **H09 — Isolated source worktrees, writer leases and safe publishing** (BLOCKING): Two workers cannot publish conflicting protected paths or merge using stale QA/lease evidence

## Work checklist
- Validate workstream and source ref against approved Source Registry
- Reject intersecting mutable paths and stale lease generations
- Record executor UNKNOWN separate from assigned state
- Surface fair ready list to human/scheduled dispatch adapters
- Implement assigned hardening patch contracts H08, H09 only after frozen upstream contracts; see hardening_patches.v1.json.

## Mandatory stop conditions
- Do not change source_writes_enabled or reviewer counts
- Do not edit protected gameplay/framework/renderer repositories

## Required verification
- Two overlapping writers cannot both pass lease gate
- Old epoch and stale source SHA reject
- 1000 independent jobs can be scheduled logically
- No paid/model launch from message alone
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
