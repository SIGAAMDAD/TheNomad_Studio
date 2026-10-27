# Workgroup H — Synthetic scale and chaos benchmark harness

## Baseline
- Project: The Nomad Studio Hyperscale Coordinator V2
- Baseline: SIGAAMDAD/TheNomad_Studio@aa684e03dc465f9e049f5ac615e1285890f8d72a
- Master plan: 00_ARCHITECTURE.md
- Wave: 1

## Objective
Create non-paid synthetic workload generators for 100/1k/10k active workers and 100k registered metadata entries; measure throughput, event fairness and recovery.

## Scope
- LOAD-MODEL
- CHAOS-TESTS

## Dependencies
- A

## Integration gate
Start after approved Wave 0 contract freeze

## Allowed source paths
- services/coordination/bench/**
- tests/coordination/load/**

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
- Frozen V2 schemas from A
- No source writes or model invocation permitted in tests

## Contracts this group publishes
- Deterministic workload manifest and seedable generators
- Benchmark trace, baseline SLO and chaos injection report

## Work checklist
- Implement stateful fake worker IDs/epochs/messages
- Generate hot-job and even-distribution workloads
- Simulate delayed ACK, replay, crash, rate limiting and lease contention
- Publish reproducible metrics without inferred PASS

## Mandatory stop conditions
- Must never call paid ChatGPT/Work/Codex executors
- No claims of measured 10k capacity before runnable infra exists

## Required verification
- 100 simultaneous unique event IDs with no lost accepted ID
- 100x burst at distinct workload sizes
- P95/P99 latencies, memory, DB locks, queue lag and GitHub calls recorded

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
