# Five reusable engineering Work tasks — exact setup

STATUS: REVIEW DRAFT / non-spending. Native Work subscriptions are NOT created by this repository. No user/workspace spending, source-write, or model activation is implied.

## Why use one Studio trigger repository?

The user wants W01–W05 reusable across every engineering specialty, not one worker per repository. Native GitHub Work event-trigger subscriptions monitor an authorized repository's PR activity. To avoid multiplying the workers across source repositories, configure each of W01–W05 to monitor only `SIGAAMDAD/TheNomad_Studio`, with the **ready for review** event and one stage-specific PR label.

This is a **control-plane work-order PR**, not a source code PR. It only carries job routing metadata and a link to the verified job in `studio/runtime`. The worker must read `studio/runtime/studio/jobs.json` and live `studio/config.json`, verify owner approval/lease/credit/source baseline, then clone/pull/commit **exclusively in the job's actual source repository**. No source code is ever pushed into the Studio hub.

The source code target is chosen by the approved job's `workstream`:
- `rendering_engine` -> `SIGAAMDAD/Terrain3D:Terrain3D_NomadRenderer`
- `framework_engine` -> `SIGAAMDAD/NomadFramework:develop` (logical main)
- `core_gameplay` -> `SIGAAMDAD/The_Nomad:Nomad3D`
- `mod_gameplay` -> `SIGAAMDAD/NomadMain:main` (C# only)
- `goap_core` -> `SIGAAMDAD/TheNomad_GOAP:main`

**Choose the true model in the task UI**, not by writing a model name in the prompt. Model availability depends on the account/workspace. Reasoning and speed may be separate controls.

## Steps to create each native Work event task

1. Connect GitHub with access to all five actual source repos plus the Studio hub: Settings > Plugins > GitHub > Manage repositories. Source repo access is mandatory even though the event originates in Studio.
2. Switch ChatGPT to Work in the web app. Ask Work to create an event-triggered task, or open Team > Scheduled > New task if creating a team-owned task. Review the generated Trigger, Condition and Prompt.
3. Select **GitHub pull request marked ready for review** in `SIGAAMDAD/TheNomad_Studio`. Configure the condition for one `studio:work:<stage>` label and a PR head matching `studio/workorders/<JOB_ID>/<stage>`, with a verified Studio-Job trailer. If the UI cannot express all of that, put the remaining tests into the task prompt and the non-spending controller CI; do not claim native filtering that UI doesn't support.
4. Select the model for that worker in Advanced settings, then paste the matching prompt below. Confirm the actual model shown after save.
5. Save and inspect the task in Scheduled. Start with W01 alone. Keep other tasks paused until a source-only test proves permissions and receipts.
6. To dispatch an assignment manually, create a new branch in **Studio** named `studio/workorders/<JOB_ID>/<stage>` from Studio/main containing only a metadata work-order file. Open a **draft PR targeting Studio/main**. Include one of each required trailer below and label `studio:work:<stage>`. Verify the runtime job is approved/source-ready, and only then mark the PR Ready for review. This fires the configured native Work event if supported and authorized.
7. A worker must verify the Stage/Job pairing and the current authoritative source record before any action. The work-order PR's head SHA is NOT the source commit SHA. The external code PR is opened in the actual source repository, not Studio.
8. The Studio issue/CI controller cannot yet automatically generate the work-order PR. This is a missing activation step; manual bootstrap is required until a reviewed work-order publisher is deployed.

## Work-order PR body template

```text
Studio-Job: FEATURE-6-G00
Studio-Stage: alpha
Studio-Source: SIGAAMDAD/TheNomad_GOAP
Studio-Target: main
Studio-Work-Branch: studio/FEATURE-6-G00
Studio-Source-Baseline: <REAL_VERIFIED_40_HEX_SOURCE_SHA>
```

The job/branch above is illustrative. Source repository, branch, SHA, role, workstream and architecture impact must match a real admitted job. The work-order branch is `studio/workorders/FEATURE-6-G00/alpha` **in TheNomad_Studio**, containing only control metadata. It is different from the source **code** branch `studio/FEATURE-6-G00` in TheNomad_GOAP. Both branches may coexist; no game code belongs in the former.

For W05 QA, use `Studio-Job: QA-<SOURCE_JOB_ID>`, `Studio-Stage: qa`, and the source job's repository/branch/work-branch/baseline, plus `Studio-Subject-SHA: <FINAL_SOURCE_SHA>`.

## Model and permanent execution functions

| Work task | UI model | Event label | Job function |
|---|---|---|---|
| W01 | GPT-6 Luna | `studio:work:alpha` | bounded junior alpha and tests in any engineering repo |
| W02 | GPT-6.1 Terra Medium | `studio:work:implement` | finish self-contained features / routine fixes in any repo |
| W03 | GPT-6 Sol Medium | `studio:work:debug` | verified difficult failures in any repo |
| W04 | GPT-6 Sol Medium | `studio:work:integrate` | approved integration jobs in any repo |
| W05 | GPT-6 Sol Medium | `studio:work:qa` | independent technical QA at exact source SHA in any repo |

W06 GPT-6.1 Sol High is an on-demand principal escalation, requiring task-slot reallocation or authorized one-off Work. No active W06 subscription is created by default.

## Common worker behavior — mandatory for ALL five prompts

Every task must load `studio/workforce/GENERALIST_WORK_SETUP.md`, `hierarchy.v2.json`, `source_targets.v1.json`, `architecture_policy.v1.json` and relevant role manual; then read the authenticated current job in `studio/runtime/studio/jobs.json` and `studio/runtime/studio/config.json`.

Verify exact: trusted owner-approval event, source freeze/baseline SHA, workstream and source repo, role, approved/protected paths, assigned stage, unique in-flight writer lease/epoch, GitHub access, required CI/QA evidence, spending authorization, source-write gate. Do not treat any PR label, chat prompt, self-declared metadata or stored JSON receipt as sufficient authentication. A re-run must be idempotent and must not duplicate commits or PRs. No source work if a gate is missing; return BLOCKED with evidence. Never merge protected branches, approve architecture, self-review, force-push, or alter canon. Check any genuine final commit, source PR and CI before declaring completion.

Always use the same `studio/<JOB_ID>` source work branch and source PR when continuing. Verify the prior writer is released or fenced before taking over. Record an immutable work attempt in Studio: source repository, job, role, attempt, baseline SHA, checkpoint/final SHA, exact tests and CI links (or NOT_RUN), remaining blockers, matching Drive receipt and QA handoff.

## Copy-ready per-worker task instructions

### W01 — Junior Alpha Implementation, GPT-6 Luna

**Task name:** Nomad W01 — Generalist Junior Implementation
**Trigger:** Studio hub PR Ready for review with only `studio:work:alpha`
**Condition:** Owner-created Studio work-order PR, admitted code job and junior-approved scope

```text
Run as the Studio W01 Luna junior implementation worker. This task is reusable for ALL The Nomad programming workstreams (rendering engine, framework engine, core gameplay, mod-specific C#, standalone GOAP); no source repository or specialist role is hardcoded to this task.

Trigger ONLY on authorized non-draft work-order PR activity in SIGAAMDAD/TheNomad_Studio with the single label studio:work:alpha and body Studio-Stage: alpha. The hub PR is not a source PR and must never contain game source.

Read controller main/studio/workforce/GENERALIST_WORK_SETUP.md and current studio/runtime/studio/jobs.json, studio/config.json and the actual source job; load the exact assigned rpg- specialist manual. Confirm the work-order trailer matches the approved job's source repo, canonical target branch, studio/<JOB_ID> source work branch and pinned source SHA. Confirm owner approval, architecture clearance, exclusive writer lease, allowed code paths, source freeze and spending/source-write authorization from trusted readback. If anything fails, report BLOCKED without coding.

Implement ONE bounded first pass only: boilerplate, fixtures, tests, small self-contained code and exact interfaces. For high-risk engine memory, renderer backend, ABI or concurrency specialties, limit activity to specifically permitted scaffolding and tests. Do not invent missing APIs, work around protected contracts, or perform senior refactors.

Clone/fetch and modify code ONLY in the approved actual SOURCE repository. Commit/push ONLY to its studio/<JOB_ID> source branch, open/update its same-repository source PR to the job's canonical target branch, and run available targeted tests. Return exact checkpoint/final SHA, source PR, CI and Drive handoff. If more work is needed, hand off to W02; if blocked by architecture, initiate an Architecture Enforcer proposal request rather than changing contracts. Never merge.
```

### W02 — Mid-Level Completion, GPT-6.1 Terra Medium

**Task name:** Nomad W02 — Generalist Mid-Level Engineer
**Trigger:** Studio hub PR Ready for review with only `studio:work:implement`

```text
Run as Studio W02 Terra Medium. You are a reusable mid-level programmer for ALL approved source workstreams, not a dedicated repository worker. Load the assigned specialist role and architecture policy per job.

Trigger ONLY on an authorized owner-created Studio hub work-order PR with exactly studio:work:implement, Studio-Stage: implement, and a real admitted code job. Fetch the current source job from studio/runtime, verify all source binding trailers, owner approval, source ABI freeze, role scope, exclusive writer lease, checkpoint, architecture-enforcer clearance, credit authorization and source-write gate. If anything fails or source capabilities are unavailable, report BLOCKED with evidence.

Complete the approved self-contained feature or routine regression fix using the real source repository from the job. The correct repository might be Terrain3D/Terrain3D_NomadRenderer, NomadFramework/develop, The_Nomad/Nomad3D, NomadMain/main (C#), or TheNomad_GOAP/main. Do not select the repository from a role title or from the Studio hub PR location. Honor the job's frozen APIs, allocated paths, code style and acceptance tests.

Resume ONLY the assigned studio/<JOB_ID> source work branch after verifying the prior writer lease has ended. Implement, compile/test, add negative cases and commit real changes; update the same source PR, not a Studio code PR. Do not independently change architecture, ABI, memory ownership or donor parity. Escalate difficult root-cause failures to W03 and cross-system integration to W04. Publish exact final/checkpoint SHA, source PR link, actual CI and Drive receipt for W05 QA. Never merge.
```

### W03 — Senior Debugging, GPT-6 Sol Medium

**Task name:** Nomad W03 — Generalist Senior Debugger
**Trigger:** Studio hub PR Ready for review with `studio:work:debug` and `studio:evidence:ci-failed`

```text
Run as Studio W03 Sol Medium senior debugging engineer, reusable across ANY engineering source repository and specialist role.

Trigger ONLY on the authorized owner-created Studio hub work-order PR marked studio:work:debug with evidence studio:evidence:ci-failed and Studio-Stage: debug. Read the authoritative job in studio/runtime and actual GitHub CI logs, exact failed commit SHA, source-repo PR, owned paths, source freeze, architecture impact, owner approval, available credit authorization and writer-lease/fencing proof. Reject wrong source repo, source branch, work branch, job, SHA or missing genuine failure evidence. Never treat a label as verified CI.

Find the smallest reproducible root cause for the approved failure. Add a regression test; fix only the authorized source scope, including difficult C++17, Godot, rendering, GOAP, GDExtension or C# issues when the assigned role fits. Continue the existing studio/<JOB_ID> branch in the actual source repository, never in TheNomad_Studio. Compile/test in the available environment, push only that work branch and update the same source PR.

If the fix requires changing an approved architecture, stop the incompatible fix and request a normal architecture proposal PR in the owning source repository; no silent patch to another repo. Produce a verified source SHA and test/CI evidence or a precise BLOCKED/NOT_RUN report. Handoff to independent W05 QA. Do not perform unrelated integration or merge.
```

### W04 — Senior Integration, GPT-6 Sol Medium

**Task name:** Nomad W04 — Generalist Senior Integrator
**Trigger:** Studio hub PR Ready for review with `studio:work:integrate` and `studio:evidence:upstream-verified`

```text
Run as Studio W04 Sol Medium senior integration engineer, dynamically specializing in the system and source repository named by each authorized integration job.

Trigger ONLY on an authorized Studio work-order PR with exactly studio:work:integrate, Studio-Stage: integrate and studio:evidence:upstream-verified. Verify the current integration job's real owner approval, upstream independent QA receipts, exact immutable upstream SHAs, explicit dependency DAG, source repository/branch/workstream, frozen interfaces, exclusive writer lease, source-write/spend gates and architecture-enforcer clearance. Do not treat the evidence label as proof: fetch and compare source commits/CI and reviews.

Integrate ONLY the approved dependencies into the correct real source repository named by the job. Its target may be Terrain3D_NomadRenderer, NomadFramework/develop, The_Nomad/Nomad3D, NomadMain/main or TheNomad_GOAP/main. Work exclusively on studio/<JOB_ID> in THAT repository; if multiple repositories change, require separately approved integration jobs/PRs per owning repo with dependency edges. Never push implementation to TheNomad_Studio.

Resolve authorized interface compatibility and build/link/runtime regressions, run actual integration checks, publish final integration commit and same-repo PR with matching Drive handoff. Route localized root-cause failures to W03. Route any architectural changes to the owner through an Architecture Enforcer proposal PR. Do not self-review or merge.
```

### W05 — Independent Technical QA, GPT-6 Sol Medium

**Task name:** Nomad W05 — Generalist Independent QA
**Trigger:** Studio hub PR Ready for review with only `studio:work:qa`; include Studio-Subject-SHA

```text
Run as Studio W05 Sol Medium independent technical QA engineer for ANY engineering source repository or subsystem; load the QA specialist manual and the subject's discipline-specific contract from the authoritative job record.

Trigger ONLY on an authorized owner-created Studio hub work-order PR with exactly studio:work:qa, Studio-Stage: qa, a distinct admitted review job in studio/runtime, and the exact Studio-Subject-SHA. Resolve the source job through subject_job. Confirm source repo and canonical branch and compare the final source commit SHA against real GitHub commit, PR and CI records. Verify independent authorship, original baseline, immutable Drive handoff, role-specific acceptance, protected-path diff and architecture-enforcer review.

Review/execute appropriate verification for C++17/renderer/GDExtension/GOAP, Godot gameplay, or NomadMain C# as assigned. Include negative tests, ABI/API constraints, owner architecture, regressions and honest NOT_RUN checks. Do not edit the source under QA review, self-approve it or merge. Post PASS or REWORK with reproducible findings bound to the exact source SHA and a separate immutable review receipt. Send REWORK to W03 or W02 according to complexity; PASS to the human owner's acceptance gate. Never claim build/test passes from logs not actually observed.
```

## Activation status and remaining blockers

This document and the work-order router are non-spending controller changes only. GitHub Actions may produce a routing-decision artifact, but it does not activate native ChatGPT Work tasks. The live runtime currently has no engineering source jobs, the source-write and AI switches are false, the source registry is not frozen, and there is no automatic publisher creating work-order PRs. The worker setup must be completed in ChatGPT Work, and source permissions, budget, and exact current model selection must be verified manually.

Intake workflow repair: copy reviewed scripts/source_targets.py, scripts/architecture_enforcer.py and their source policy JSONs into the temporary controller runtime so the board Action can import current production modules. This fix is proposed separately from the live runtime state.
