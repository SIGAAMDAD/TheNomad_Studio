# The Nomad Studio — Event-Driven Worker Hierarchy V2

STATUS: REVIEW DRAFT. Work subscriptions are not installed, no paid calls are made.
Machine-readable policy: studio/workforce/hierarchy.v2.json (174 specialist roles).
Controller repository only: SIGAAMDAD/TheNomad_Studio. Game/framework/mod/rendering source lives in their own repositories.
Production Director: S00. Human owner retains architecture, canon, spending and merge authority.

## Reporting structure

HUMAN OWNER
  -> S00 Production Director (scheduled GPT-6 Instant)
     -> 174 discipline-specific roles, loaded for each approved job
        -> S01: gameplay, combat, systems, GOAP behavior design (scheduled Instant)
        -> S03/S04: lore, quests, dialogue and narrative content (scheduled Instant)
        -> S08: independent creative/content QA (scheduled Instant)
        -> W01: Junior Alpha Implementation (GPT-6 Luna)
        -> W02: Mid-Level Feature Completion (GPT-6.1 Terra Medium)
        -> W03: Senior Debugging (GPT-6 Sol Medium)
        -> W04: Senior Integration (GPT-6 Sol Medium)
        -> W05: Independent Technical QA (GPT-6 Sol Medium)
        -> W06: Principal Architecture Escalation (GPT-6.1 Sol High, on demand)
        -> GitHub Actions: builds, CI, schema and receipt validation (zero model tokens)
  -> HUMAN OWNER: final architecture, budget, release, merge, licensing and canon

The original 174 role manuals remain authoritative; W lanes are execution
functions, not additional professional disciplines or permanent assistants.

All engineering jobs must pin explicit workstream, repository, target branch,
work branch, implementation language and verified baseline SHA in the actual
source repository. The owner-approved workstream selects the target:
rendering_engine -> Terrain3D/Terrain3D_NomadRenderer;
framework_engine -> NomadFramework/develop (logical main development branch);
core_gameplay -> The_Nomad/Nomad3D;
mod_gameplay -> NomadMain/main (C#);
goap_core -> TheNomad_GOAP/main. Every W01–W05
worker must clone/pull/push against that exact source repo, not the Studio
hub. The Studio receives only job status and immutable commit/QA receipts.
A source-work order PR is created in the source repo before a native PR
event can activate an implementation worker.

## Live task capacity proposal, NOT applied

Five active scheduled chat slots: S00, S01, S03, S04 and S08.
Five active event-driven Work tasks: W01, W02, W03, W04, W05.
W06 requires reallocating one active Work slot or explicit one-off Work.
Dormant scheduled slots S02/S05/S06/S07/S09 remain reusable. S01 dynamically
covers S02's systems-design role when appropriate. The configuration is a
proposal to fit ten active identities and is not implemented in Scheduled.
Actual plan capacity, subscription conditions and model picker must be read
back before enabling paid events.

## Mandatory Architecture Enforcer

Every engineering job declares architecture_impact (none/change) and
requires an Architecture Enforcer review against its frozen source baseline.
Architecture Enforcer is a duty in the existing S00/W05 workflow, not an
eleventh active task or a new autonomous architecture authority.

**If a worker needs an explicit architectural change, stop the incompatible
implementation, record BLOCKED_ARCHITECTURE and open a normal draft
architectural proposal PR in the owning SOURCE repository.** The proposal
uses `studio/arch/<JOB_ID>` and the canonical target branch, not a Studio
hub branch or an unrelated implementation branch. The PR must explain the
contract change, evidence, alternatives, ABI, migrations, tests and rollback.
W05 independently reviews it; W06 provides optional Sol High analysis
only on approved escalation. Only the human owner can approve/merge it.
Workers must read back authentic owner APPROVED review on the exact head SHA
and owner merge before resuming from the newly accepted source baseline.
A PR label, generated review or cached JSON record does not authorize a
change. See `studio/workforce/ARCHITECTURE_ENFORCER.md`.

## Full engineering production sequence

OWNER_APPROVED -> S00 SOURCE_SCOPING
-> W01 LUNA_ALPHA (bounded first pass, tests, checkpoint)
-> GitHub Actions DETERMINISTIC_CI
-> if FAIL_ROUTINE or PASS_INCOMPLETE: W02 TERRA_IMPLEMENT_OR_REWORK
-> if FAIL_COMPLEX: W03 SOL_DEBUG
-> GitHub Actions DETERMINISTIC_CI after every change
-> if complete/QA verified: S00 INTEGRATION_READY
-> W04 SOL_INTEGRATE (approved systems and independent source ownership)
-> GitHub Actions INTEGRATION_CI
-> if FAIL: W03 SOL_DEBUG -> new CI
-> if PASS: W05 SOL_INDEPENDENT_QA (exact final SHA)
-> HUMAN_OWNER final review/merge.

W02 may finish a self-contained feature after W01 scaffolds it; W03 diagnoses
complex reproducible defects and W04 is the separate subsystem integrator.
W05 never edits reviewed source and does not self-approve. W06 is an
owner-directed escalation for complex architecture and concurrency conflicts.

## Luna, Terra and Sol engineering scope

Luna is the junior production layer. Initial target: 60-75% of eligible
routine alpha jobs, not 60-75% of all engineering and not a measured success
rate. Of 50 engineering professional roles, 27 allow Luna bounded alpha code
under frozen contracts; 23 high-risk specialties only allow junior boilerplate
and test generation.

Terra is the mid-level layer. Terra finishes conventional code paths:
C# managed bindings, JSON data importers, editor workflows, routine gameplay
state machines, feature tests and local refactors, plus straightforward CI
failure fixes. Prefer Terra over Sol whenever scope is self-contained and
compilation/negative tests provide a reliable check. For one of the 23
high-risk roles, Terra is restricted to an explicitly reviewed boilerplate
scope; actual hot-path algorithms go to Sol.

Sol Medium owns difficult root-cause debugging (W03), separately owns
multi-system integration (W04), and independently reviews technical work
(W05). GPT-6.1 Sol High is a scarce W06 escalation, never an automatic
per-commit default.

The monetary model relationship for GPT-6.1 Terra is about half the
uncached input/output token rate of GPT-6 Sol and 10x the GPT-6 Luna rate,
subject to current Work/credit rate cards and actual token usage. All
three can require different token quantities for the same result.

## Native Work events versus GitHub Actions

Supported native Work automation is GitHub pull-request activity. Create
event-triggered Work tasks using ChatGPT Work mode with the connected GitHub
app, choose repository, trigger, Condition, Prompt and the actual model.
A GitHub Actions file DOES NOT create a Work event subscription.
Event availability and exact trigger choices depend on the connected app.
The present tool cannot create these native Work subscriptions.

GitHub Actions can independently process Issues, PRs, CI and GitHub labels.
The reviewed Studio PR router is non-spending. It emits routing evidence
for Studio-repository PR events but does not launch AI.

The Work subscriptions must monitor pull-request activity in the ACTUAL
source repository for their assigned job, not the Studio controller:
SIGAAMDAD/TheNomad_GOAP:main (standalone GOAP);
SIGAAMDAD/NomadFramework:develop (framework engine);
SIGAAMDAD/The_Nomad:Nomad3D (core/non-mod-dependent gameplay);
SIGAAMDAD/NomadMain:main (mod-specific C# gameplay); and
SIGAAMDAD/Terrain3D:Terrain3D_NomadRenderer (rendering engine).
Each source job uses its own studio/<JOB_ID> work branch IN THAT REPO.
TheNomad_Studio is a control-plane hub for manifests and receipts only.
Never commit real game/GOAP/GDExtension/Terrain/NomadMain code to the Studio hub.
Do not assume the native Work UI can subscribe to multiple repositories
in one task; reconfigure or separately authorize when changing systems.
See studio/workforce/SOURCE_REPOSITORIES.md and source_targets.v1.json.
The Studio PR routing GitHub Action by itself cannot observe source PR
events in other repositories; native Work must watch the source repo or
an owner-authorized cross-repo bridge must be installed.

Routing labels: studio:work:alpha (W01), studio:work:implement (W02),
studio:work:debug (W03), studio:work:integrate (W04), studio:work:qa (W05),
studio:work:escalate (W06).
One stage label only. Require a PR body line: Studio-Job: JOB-ID.
High-risk junior work requires studio:scope:boilerplate.
CI evidence label: studio:evidence:ci-failed.
Dependency proof label: studio:evidence:upstream-verified.
These labels are routing hints, not authorization: read real CI, owner-approval
event, job state, source SHA and lease every time.

## Native event Work prompt — W01 Luna Junior Implementation

Trigger: suitable GitHub PR opened/updated/ready-for-review.
Condition: authorized alpha stage, matching admitted job and source SHA.
Instruction:
Read only the exact approved job, current source PR diff, professional role manual,
frozen contracts, explicit workstream, implementation language, owned paths and prior checkpoint. Verify trusted owner
approval, source SHA, exclusive lease, source-writes gate and spend approval
from the authoritative Studio runtime. On any failed gate, record NO_WORK;
do not write source. Implement one bounded alpha increment, including
positive/negative tests. Run targeted builds where available. Commit the
actual source change to the existing job branch with Studio-Job and attempt
trailers; publish exact SHA, test output and receipt. Preserve framework
ownership and never merge. Do not paste giant code dumps in the chat.

## Native event Work prompt — W02 Terra Medium Mid-Level Engineer

Trigger: verified and authorized feature implementation PR stage, either
after a junior checkpoint or directly for a source-ready conventional
feature. Condition: reviewed scope/contract, owner approval, exact source
SHA, source-write permission, exclusive lease and approved credit budget.
Instruction:
> Inspect only the approved feature brief, previous Luna checkpoint if
> present, current source SHA, relevant code and the frozen contract.
> Complete self-contained implementation and routine bug fixes without
> crossing an ABI or architectural boundary. Add positive/negative
> tests and run targeted CI. Commit real source to the same job branch,
> publish immutable receipt and leave independent QA for W05. Escalate
> complex root-cause bugs to W03 and integration to W04. No merge.

## Native event Work prompt — W03 Sol Medium Senior Debugger

Trigger: verified failed CI at exact SHA or independent REWORK report.
Condition: explicit debug ticket and exclusive source-writer lease.
Instruction:
> Read exact failing commit, minimized logs, tests and impacted symbols.
> Reproduce the failure, find root cause and repair only its approved
> scope. Add regression fixture and re-run CI. Use the same job/branch
> and publish a new SHA and immutable receipt. Architectural changes
> are blocked pending owner review. Do not integrate or merge.

## Native event Work prompt — W04 Sol Medium Senior Integrator

Trigger: integration-stage PR with separately verified upstream jobs.
Condition: source ownership, upstream QA receipts and ABI frozen.
Instruction:
> Read verified upstream commits and approved interfaces, integrate
> into the authorized branch, run links/builds and cross-module
> regressions, and publish integration SHA and matching Drive handoff.
> Return localized defects to W03. Do not silently redesign donor
> or host frameworks and never merge protected branches.

## Native event Work prompt — W05 Sol Medium Independent Technical QA

Trigger: review-stage PR at exact final SHA. Condition: author and
reviewer are distinct; subject commit and CI proof read back.
Instruction:
> Independently inspect exact final SHA, source diff, build evidence,
> architecture and negative tests. Produce PASS/REWORK report tied to
> the immutable SHA with NOT_RUN checks and reproducible findings.
> Never modify the reviewed code or accept the owner's merge gate.

## W06 GPT-6.1 Sol High Principal Escalation

On-demand, not a permanently active Work task. Analyze only an explicitly
owner-escalated architecture/ABI/concurrency/memory problem using real
source and prior failed tests. Produce a minimal technical decision
and bounded patch recommendation; independent QA and owner approval remain.

## Spending and approval gates

Keep existing studio/config.json: auto_ai_enabled=false and
source_writes_enabled=false until separately authorized.
Keep hierarchy.v2.json controls.auto_spend_enabled=false and
controls.work_event_triggers_installed=false in this reviewed draft.
Architecture proposal PRs are not an exception to owner review, protected
source ownership, native GitHub permissions or credit authorization.
No automated credit usage, no model selection via prompt alone,
no Astra by default, no Ultrafast, no unattended auto-merge.
The Work model must be chosen in the event task UI; the checked-in label
does not change the model selected for an already-created scheduled task.

## Verification and activation

Deterministic checks:
python scripts/work_event_router.py validate
python -m unittest discover -s tests -v

Read-only PR event audit workflow:
.github/workflows/studio-event-route.yml

After owner review, separately configure the five Work subscriptions
in Work mode against the current ACTUAL source repository and explicitly
approve a credit budget. For GOAP use SIGAAMDAD/TheNomad_GOAP, never
SIGAAMDAD/TheNomad_Studio as the code worker PR trigger. Verify event triggers
and model selection in Scheduled. Run a non-spending PR routing pilot,
then a single owner-authorized junior Luna code pilot, followed by CI,
senior integration and independent QA. Record actual credit cost, rework
count, final source SHA and accepted deliverable. Do not claim deployment
before the Work subscriptions, exact event triggers and execution receipts
are read back.
