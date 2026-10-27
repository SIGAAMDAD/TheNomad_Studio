# Non-disruptive migration: V1 -> V1.5 -> V2 -> hyperscale
Status: proposed sequencing. Another chat may be cleaning TheNomad_Studio. This plan owns only new architecture paths until an explicit integration gate; do not edit its scripts, workflows, source registry or live runtime in parallel.

## Frozen baseline and concurrent-work protection
Baseline for the design is main @ aa684e03dc465f9e049f5ac615e1285890f8d72a. The cleanup subsequently merged PR #24 as Studio 1.0.0, commit 49c3a5bba445f1dd927b930283ffdeb3bbbb8b52. This introduces lifecycle/health signals, a 100-worker synthetic relay test and 190 role launch prompts. The original design baseline remains a historical reference; implementations must freeze against the newly merged main. GitHub cannot establish whether that other chat remains active. Before implementation check all new cleanup commits/PRs and refresh the baseline; do not cherry-pick an obsolete script version over the cleaner's result.

Protected from all parallel builders until controlled integration:
- scripts/studio_comms.py, studio_inbox.py, studio_ci_evidence.py, studio_director_pulse.py, studio.py;
- .github/workflows/studio-comms-broker.yml, studio-reconcile.yml and other live workflows;
- studio/runtime: jobs.json, config.json, chat_workers.json, slots.json, V4 authority records;
- existing Studio governance, source_targets, architecture_policy and five source repositories.
Only the authorized integration group may modify these paths, after fresh SHA readback, cleanup reconciliation, owner-governed architecture clearance and CI.

## Wave 0: freeze V2 interfaces (one writer)
Publish typed events, trusted transport attribution, assignment epochs, per-job sequencing, dedup/outbox keys, recipient status/ACK, source CI evidence and aggregate report schemas. Include explicit V1 compatibility and a contract test suite. Design owner approval does NOT set source_writes_enabled=true.

## Wave 1: independent foundations
- Ingress adapter: GitHub webhook / Issue comment support, replay-safe cursors, bounded validation, stable status codes, no chat wake-up claim.
- Durable core: transactional insert + outbox, queue adapter, segment archive manifest, clear restart/retry semantics. Run in test infrastructure only.
- Load harness: deterministic test generator for 100, 1k, 10k and potential 100k registered worker identities, plus throughput/chaos fixtures. No paid AI calls; synthetic workers only.
All three consume frozen Wave 0 contracts. They may develop independently in non-overlapping new directories. No integration into existing workflow yet.

## Wave 2: parallel services against frozen core
- Incremental recipient inbox, ACK/lease-epoch enforcement and compact context projection.
- Scheduler / dependency DAG / conflict graph / writer lease fencing. It does not autonomously launch inactive chats.
- Verified GitHub evidence mirror and batch archival integration; explicit source CI/QA separation.
- Independent Oversight signal bridge and access-control tests (no case narrative).
- Observability, hierarchical department aggregation and cost/backpressure controls.
Each workgroup owns a separate code tree and shared contract tests, NOT a live hub hotspot. Integrate on a dedicated feature branch and run schema/API conformance before enabling any effect.

## Wave 3: single integrator, controlled cutover
1. Verify current cleanup branch is merged or its compatibility changes are incorporated. Fresh-read main and runtime exact SHAs.
2. Run all producers in SHADOW mode: ingest/verify events and generate reports, but do not publish production acknowledgments, dispatch workers, change permissions or source.
3. Replay a frozen stream including duplicates, reordering, stale assignments, missing messages and permissions. Compare projected recipient inboxes and evidence gates against V1; record expected semantic differences.
4. Enable V2 **read-only inbox projection** for 2 synthetic jobs, then 10, 100, 1k. One projector epoch is authoritative at any moment.
5. Deploy short-lived least-privilege GitHub App credentials and an isolated gateway only after owner approval; verify read/write scope with negative tests.
6. Route actual worker messages through a single **selected** active adapter; keep old source Issue comments for recovery until cutover is proven.
7. Implement controlled per-job writer leases and reviewer capacity separately; do not silently increase global max_concurrent_writers or max_reviewers.
8. Archive/anchor accepted batches. Measure replay, outages, lag and storage growth; freeze any rollout expansion on regressions.
9. Owner independently approves activation and merges each touched source/workflow PR. No implicit auto-spend or model execution.

## Cutover atomicity
A single monotonically increasing **projector_epoch** in the coordination store elects the active projection path (V1 vs V2); this is separate from per-job assignment_epoch and source lease_generation. A service cannot flip epochs based on a worker message. Shadow mirrors may read both streams but cannot ACK or mutate authoritative job state. At cutover:
- establish source watermarks and write a signed checkpoint;
- pause effects (not intake) briefly;
- drain events up to watermark and compare projections;
- commit epoch switch with owner-authorized migration receipt;
- replay later events idempotently under new epoch;
- preserve reversibility without deleting archived records.

## Rollback
Trigger on lost accepted messages, duplicate effects, unauthorized state changes, a stale fenced source writer, invalid QA authority or unbounded backlog. Disable new projector effects, move active epoch back only after explicit governance approval, resume from committed checkpoint, replay into compatible views, preserve all event IDs / audit segments, and investigate before re-enabling. Rolling back cannot magically undo already merged Git commits; source remediations are separate owner-reviewed PRs.

## Merge-safe implementation map
The machine-readable workgroup DAG is in workgroups.json. Detailed briefs and prompts are generated from that file. Wave 0 contracts precede all runtime writers. Wave 1 ingress/core/load can work in parallel after contracts freeze. Wave 2 independent services follow their declared prerequisites. Wave 3 integrator alone updates old Studio scripts and workflow entrypoints. Never use an automatically generated workgroup brief as permission to write a protected source path.

## Ownership and acceptance
- Producer implements a bounded branch, pushes real commits and exports reproducible tests.
- Independent QA reviews the exact SHA; the integrating worker cannot self-approve.
- The Lead Director approves spending, identity provisioning, architecture and merge policy; authenticated department heads may execute fully gated merges as authorized in Studio 1.0.0.
- Every substantive worker response ends with immutable V4 department event ledger; the bus archives the operational counterpart.
- A department lead may authorize routine task sequencing within the approved graph but may not fabricate owner consent.

## Mandatory source-reviewed hardening gates (H00–H14)

The source-verified, machine-readable **hardening_patches.v1.json** and detailed **09_RELIABILITY_AND_EXECUTION_PATCH_PLAN.md** extend this four-wave program without inventing more departments or mixing source ownership. Workgroup briefs and workgroups.json now explicitly assign each hardening patch. A wave is not accepted until its blocking hardening patches have independent reproducible test evidence and exact commit SHAs.

- Wave 0, WG-A: H00 known-defect regression inventory and H01 privileged owner/identity/approval contracts.
- Wave 1, WG-B: H02 authenticated intake; WG-C: H03 GitHub/DB publication saga and H04 disaster recovery; WG-H: H05 deterministic chaos/load harness (requires WG-C).
- Wave 2, WG-D: H06 original-assignment ACK fencing and H07 project resume capsules; WG-E: H08 verified shift executor admission/stop and H09 isolated source worktrees/leases (requires WG-D); WG-F: H10 artifact-byte/supply-chain proof; WG-G: H11 independently keyed Oversight and approval readback; WG-J: H12 auditor watchdog/statistics (requires WG-D and WG-E).
- Wave 3, WG-I: H13 isolated P0 corrective integration and H14 shadow/canary/rollback, with exact current main/runtime/Oversight compatibility checks.

**No production switch is authorized by approval of this patch plan.** The user must separately approve spending, credentials, branch writes, source-writer capacity, and deployment.

## V2 database/Drive authority migration prerequisite

Before Wave-3 cutover, obtain an explicit owner-approved Drive BL-00/05/07/08/10 baseline-change decision that prevents both the Drive live queue and PostgreSQL from accepting writable job state concurrently. Stage Drive changes cursor/revision projection and compare approvals in SHADOW mode. Do not auto-move source documents or rewrite accepted canon. GitHub repository-protection changes require an authorized admin and actual enforcement readback; source PRs cannot configure private branch rulesets.
