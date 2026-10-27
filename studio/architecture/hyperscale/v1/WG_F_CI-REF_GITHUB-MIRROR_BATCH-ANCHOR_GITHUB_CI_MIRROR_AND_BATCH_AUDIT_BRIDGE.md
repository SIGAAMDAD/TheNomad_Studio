# Workgroup F — GitHub CI mirror and batch audit bridge

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Master plan: 00_ARCHITECTURE.md
- Wave: 2

## Objective
Map source CI/QA evidence and immutable segment hashes back to Studio GitHub without one commit per worker event.

## Scope
- CI-REF
- GITHUB-MIRROR
- BATCH-ANCHOR

## Dependencies
- A
- B
- C

## Integration gate
Start after required frozen Wave 1 contracts; do not attach live runtimes

## Allowed source paths
- services/coordination/github_bridge/**
- tests/coordination/github_bridge/**

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
- Event and segment manifest contract from A
- Signed ingress adapter from B
- Core archive receipts from C
- Existing scripts/studio_ci_evidence.py metadata semantics

## Contracts this group publishes
- Verified source CI readback adapter
- Batch manifest and audit mirror into GitHub
- Source SHA mismatch fail-closed fixture

## Work checklist
- Preserve independent QA vs CI metadata separation
- Batch record hashes and source run IDs only
- Implement GitHub API rate-budget and retry handling
- Keep low-volume Studio runtime state updates separate from high-volume messages

## Mandatory stop conditions
- No source workflow edits until authorized integration
- No single hot branch per-message commit

## Required verification
- Missing artifact bytes never reported verified
- Batch manifest anchors match original segments
- GitHub token failures/backpressure do not lose committed events

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
