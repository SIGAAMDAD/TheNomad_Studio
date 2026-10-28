> **Historical only:** V1.6 replaces automated AI worker and Work-subscription guidance with `studio/v16/worker/README.md` (manual user-opened High chats). Findings and prior observations remain unchanged as dated historical records.

# Studio 1.0.0 — Repository and Execution Audit

**Observed:** October 27, 2026. **Controller repository:** `SIGAAMDAD/TheNomad_Studio`.

## Source snapshot and state

- Reviewed `main` at `aa684e03dc465f9e049f5ac615e1285890f8d72a`, prior to this proposed V1 consolidation.
- Studio V1 code was merged into `main` via PR #24 at `49c3a5bba445f1dd927b930283ffdeb3bbbb8b52` on 2026-10-27T20:07:45Z; original reviewed branch commit `fb994dd8d8d8ae7b0ca3a57b39ac19da2629f989`.
- Mutable production `studio/runtime` at `fe84a3aa0ded5375189c45e93c749a3247a8bacb` and independent communications data `studio/comms` at `f94b33a9c1239635cd654811d26dbcf9b3cab2b1`; **do not** attempt to rebase these data-plane branches onto `main`.
- 12 home departments and 174 role procedures are mapped. Launch pack contains 174 stand-alone skill packages and 190 individual launcher prompts (174 specialist; S00–S09; W01–W06).
- Current live `studio/runtime/studio/chat_workers.json` has **zero enrolled logical chat workers**.
- Approved GOAP feature `FEATURE-6` has one source implementation job `BLOCKED` on source freeze, with downstream QA job. The Sortior behavior Issue #7 is unapproved `INBOX`. Source writes and automatic AI spending remain disabled; the separately owner-authorized non-spending pilot auto_dispatch_enabled flag is already true.
- Legacy GOAP baseline draft PR #16 was closed as superseded. It was never merged. Verified source repo `SIGAAMDAD/TheNomad_GOAP:main` remains at `258a2623aa5de9d422be02c59855861570ede4ac` with a successful CI run `40474509032`. This actual source is preserved.
- Historical `feature/studio-inbox-evidence-v1` was safely fast-forwarded to `main` (ancestor-only). Diverged obsolete GOAP draft branch is preserved for history, not force-rewritten.
- Source branch-head audit recorded in `studio/v1/SOURCE_HEAD_AUDIT.json`; only Terrain3D had changed since the historical baseline, now observed `0fcf892d670db228a8b61cae1447bff38a6687a3`. Existing per-job frozen baselines remain subject to live verification.

## Implemented and verified in reviewed CI

- GitHub Issue-comment transport validates approved job, role/epoch and message schema; normalized events are archived on `studio/comms`.
- Recipient inbox projections, ACK correlation, duplicate safety and assignment filtering are exercised by deterministic tests.
- Lifecycle reports now include STARTED, HEARTBEAT, CHECKPOINT, YIELDED, SUBMITTED and STOPPED with attempt identity, ledger reference and measured-duration field.
- The V1 health audit checks missing STARTED/progress, stale worker heartbeats, stale yielded work, missing response ledger, missing checkpoint time, unsatisfied ACK and open TODO on SUBMITTED.
- The simulated 100-worker registry test exercises message archive, recipient inbox, ACK, department ledger correlation and unobserved-worker detection. **Simulation only:** it does not instantiate 100 live ChatGPT tasks or prove autonomous wakeups.
- All controller, broker, worker-routing, pulse and skill checks passed at tested V1 PR source SHA `fb994dd8d8d8ae7b0ca3a57b39ac19da2629f989`; a post-merge control-validation run also passed at `49c3a5bba445f1dd927b930283ffdeb3bbbb8b52`.

## Deployment blockers (remain open)

1. **Human-enforced head merge authorization:** distinct authenticated department-head GitHub identities, branch rules, required checks and QA approval are not installed or verified. An attempt to convert PR #24 to draft received GitHub FORBIDDEN. **The repo owner subsequently merged PR #24 through GitHub before distinct head identity/branch rules were verified.** Head-only merge protection remains a deployment blocker for future merges; do not claim V1 currently enforces the restriction.
2. **No live worker enrollment:** `studio/runtime/studio/chat_workers.json` is empty. Live Issue→broker→worker inbox→ACK→department ledger has not been exercised with separate actual chat workers.
3. **S00 access unavailable:** the runtime registry contains an S00 scheduled-task ID, but the current task connection cannot access it. Its actual schedule and effective director instructions could not be verified or updated.
4. **W01–W05 Work tasks:** their actual prompts were updated with the new lifecycle/continuation instructions and each was confirmed disabled. Native GitHub-triggered execution remains untested. W06 remains on-demand.
5. **Source CI evidence publisher:** standalone cross-repository publisher credentials and authenticated source-workflow dispatch were not observed active. No exact-source implementation/QA/merge end-to-end test has run under the new policy.
6. **11 PM Central email:** deterministic daily report creation is configured; connected Gmail recipient, scheduled S00 execution and actual sent-message evidence are not verified. No email delivery claim is justified.
7. **No active game-source writes:** source_writes_enabled=false and auto_ai_enabled=false; auto_dispatch_enabled=true was owner-enabled earlier for a non-spending pilot. No production game-source commits were performed during this audit.

## Release conclusion

V1 controller code is merged and its reviewed tests passed; skill packaging is ready for a controlled pilot. **Fully autonomous launch readiness remains blocked** until the seven deployment gates above have verified run evidence. GitHub Action tests validate code invariants and simulated relay behavior, not real dormant-chat execution.
