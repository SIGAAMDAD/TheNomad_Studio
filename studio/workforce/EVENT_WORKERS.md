# The Nomad Studio — Event-Driven Worker Hierarchy V2

STATUS: REVIEW DRAFT. Work subscriptions are not installed, no paid calls are made.
Machine-readable policy: studio/workforce/hierarchy.v2.json (174 specialist roles).
Authoritative code repo: SIGAAMDAD/TheNomad_Studio.
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

## Live task capacity proposal, NOT applied

Five active scheduled chat slots: S00, S01, S03, S04 and S08.
Five active event-driven Work tasks: W01, W02, W03, W04, W05.
W06 requires reallocating one active Work slot or explicit one-off Work.
Dormant scheduled slots S02/S05/S06/S07/S09 remain reusable. S01 dynamically
covers S02's systems-design role when appropriate. The configuration is a
proposal to fit ten active identities and is not implemented in Scheduled.
Actual plan capacity, subscription conditions and model picker must be read
back before enabling paid events.

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

The Work subscriptions should monitor authorized pull request activity in:
SIGAAMDAD/NomadFramework, SIGAAMDAD/The_Nomad, TheNomad_Studio, as permitted
in each subscription's UI. Events in other repositories are not magically
forwarded by the Studio workflow.

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
Read only the exact approved job, current PR diff, professional role manual,
frozen contracts, owned paths and prior checkpoint. Verify trusted owner
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

After owner review, separately configure the five Work subscriptions in
Work mode and explicitly approve a credit budget. Verify event triggers
and model selection in Scheduled. Run a non-spending PR routing pilot,
then a single owner-authorized junior Luna code pilot, followed by CI,
senior integration and independent QA. Record actual credit cost, rework
count, final source SHA and accepted deliverable. Do not claim deployment
before the Work subscriptions, exact event triggers and execution receipts
are read back.
