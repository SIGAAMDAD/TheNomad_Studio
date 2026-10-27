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

## Copy-ready per-worker task instructions — canonical V2

The **single authoritative full prompts** for W01, W02, W03, W04 and W05
are in [WORKER_PROMPTS_V2.md](WORKER_PROMPTS_V2.md). Copy the
appropriate complete `text` block from that file into the native Work
task's **Prompt** field. Do not use the older short task-creation
conversation prompts as execution prompts. These are task instructions,
not commands to create five new tasks if any already exist.

The V2 instructions explicitly enforce these details:

- W01–W05 can switch to any approved programming workstream on each job;
  only their seniority/stage stay constant. Read the exact authorized
  source repository, branch, frozen SHA and specialty manual from the
  current Studio runtime job, not from the hub work-order PR location.
- Event action must actually be `ready_for_review`. A native Work event
  filter that only supports PR scope/label is insufficient on its own.
  Non-matching or unverifiable events must return IGNORED without coding.
- Verify owner-initiated metadata-only work-order PR, correct PR branch,
  exactly one stage label, current trusted owner approval and job lease.
  The existing controller currently restricts work-order PR authors to
  SIGAAMDAD; a future bot publisher requires a separately approved
  authentication change in the router before deployment.
- For a genuinely new source job, create its absent
  `studio/<JOB_ID>` branch in the actual source repository from the
  exact approved baseline only after acquiring a valid source-writer
  lease. For a continuing job, inspect/reuse its existing branch,
  checkpoint and source PR. Never reset or force-push it.
- Verify source-write and spending gates independently. Labels, this
  document and a worker's own assertions are not approvals.
- W05 is source-read-only and must review the **actual source commit SHA**
  and independent CI evidence, not the Studio hub metadata PR SHA.
- The status checks `source_writes_enabled=false`,
  `auto_ai_enabled=false`, and reviewed
  `controls.auto_spend_enabled=false` remain blocking until the owner
  explicitly authorizes them in a reviewed deployment.

Native task creation and model selection remain separate user actions:
select Luna / Terra Medium / Sol Medium in actual Work task settings and
verify each saved model. Paused tasks remain paused until the owner
explicitly enables them. Work execution requires actual GitHub read/write
permissions across the target source repositories and the Studio hub.
