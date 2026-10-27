# Workgroup G — Oversight isolation and signed enforcement adapter

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Historical design baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Reviewed implementation snapshot (recheck before coding): 505d8e59003e5d5c938c46bc056370ca2a2802fa
- Master plan: 00_ARCHITECTURE.md
- Hardening delta: hardening_patches.v1.json
- Wave: 2

## Objective
Implement independent service-to-service signed notice validation with opaque case IDs, scoped authority, expiry and replay protection, excluding case content.

## Scope
- OVERSIGHT-NOTICE

## Dependencies
- A
- C

## Integration gate
Start after required frozen Wave 1 contracts; do not attach live runtimes

## Allowed source paths
- services/coordination/oversight_gateway/**
- tests/coordination/oversight_gateway/**

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
- V2 event and permission contract from A
- Existing Oversight envelope/signature policy and independent key custody design

## Contracts this group publishes
- Minimal signed notice verification adapter
- Negative authorization and replay tests
- No-confidential-content trace contract

## Hardening patches and required proofs
- **H11 — Oversight key isolation, owner approval readback and replay** (BLOCKING): Independent security review confirms approval readback and case isolation before any real confidential intake

## Work checklist
- Keep private case store/keys outside production
- Verify issuer, owner authorization, lease scope and expiry
- Pass only approved operational status to deterministic controller
- Implement assigned hardening patch contracts H11 only after frozen upstream contracts; see hardening_patches.v1.json.

## Mandatory stop conditions
- Stop if separate credentials/key custody not provisioned
- Do not modify Oversight repo without separate owner-approved PR

## Required verification
- Production connector cannot read or decrypt case store
- Forged and expired notices fail
- No plaintext witness narratives in logs/events
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
