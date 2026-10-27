# The Nomad Studio — Event-Driven Worker Hierarchy V2

STATUS: REVIEW DRAFT. Work subscriptions are not installed, no paid calls are made.
Machine-readable policy: studio/workforce/hierarchy.v2.json (174 specialist roles).
Authoritative code repo: SIGAAMDAD/TheNomad_Studio.
Production Director: S00. Human owner retains architecture, canon, spending and merge authority.

## Reporting structure

HUMAN OWNER
  -> S00 Production Director (scheduled GPT-6 Instant)
     -> Department leads (dynamically loaded from the 174-role library)
        -> S01/S02: gameplay, combat, systems and AI design (scheduled Instant)
        -> S03/S04: narrative, lore, dialogue and quests (scheduled Instant)
        -> S08: independent creative/content QA (scheduled Instant)
        -> W01: Junior Alpha Implementation Engineer (GPT-6 Luna)
        -> W02: Senior Debugging Engineer (GPT-6 Sol Medium)
        -> W03: Senior Integration Engineer (GPT-6 Sol Medium)
        -> W04: Independent Technical QA Engineer (GPT-6 Sol Medium)
        -> W05: Principal Architecture and Integration (GPT-6.1 Sol High, on demand only)
        -> GitHub Actions: builds, tests, schemas and receipt validation (no model)
  -> HUMAN OWNER: final acceptance, merge, canon, license, credits

The original 174 role manuals remain source-of-truth for profession/discipline.
The W slots are model/execution lanes, not 174 additional persistent agents.

## Live task capacity proposal, NOT applied

Six active scheduled chat slots: S00, S01, S02, S03, S04, S08.
Four active Work event tasks: W01, W02, W03, W04.
W05 requires reallocating an active slot or explicit one-off Work authorization.
S05/S06/S07/S09 remain recoverable dormant slots. The account task limit must
be checked in Scheduled before any event subscriptions are created.

## Full engineering production sequence

OWNER_APPROVED -> S00 SOURCE_SCOPING
-> W01 LUNA_ALPHA (bounded draft and reproducible tests)
-> GitHub Actions DETERMINISTIC_CI
-> if FAIL: W02 SOL_DEBUG (minimal repair; same job/branch; new SHA)
-> if PASS: S00 checks producer and design QA dependencies
-> W03 SOL_INTEGRATE (cross-system integration; independent source ownership)
-> GitHub Actions INTEGRATION_CI
-> if FAIL: W02 SOL_DEBUG -> fresh CI
-> if PASS: W04 SOL_INDEPENDENT_QA (exact final commit, PASS or REWORK)
-> HUMAN_OWNER final review/merge.

Sol DEBUG and Sol INTEGRATE are separate authorities with disjoint responsibilities.
A debugging engineer only fixes documented failures. An integration engineer only
stitches separately completed, approved systems and runs integration regressions.
W04 never edits its reviewed subject.

## Luna scope policy

Target 60–75% of eligible routine alpha implementation jobs for Luna.
This is an allocation goal, NOT an observed quality/performance statistic.
Of the 50 engineering specialist roles, 27 permit bounded Luna alpha coding
once a contract is frozen; 23 high-risk roles permit Luna boilerplate and
test scaffolding only. These numbers are in hierarchy.v2.json.
Examples: C# interface bindings, fixture generation, editor data schemas,
build/test scaffolds, bounded gameplay code and repetitive native APIs.
Algorithmic GOAP search, cross-thread memory, rendering backend, hot-path
allocator or volatile ABI changes require senior implementation/review.
No per-frame allocation, source rewrite, architecture change or branch merge
is ever approved merely because Luna produced compilable code.

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

Routing labels: studio:work:alpha (W01), studio:work:debug (W02),
studio:work:integrate (W03), studio:work:qa (W04), studio:work:escalate (W05).
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

## Native event Work prompt — W02 Sol Medium Senior Debugger

Trigger: PR changed/comment/review with a verified CI failure or REWORK.
Condition: current job, confirmed failure against exact SHA, exclusive lease.
Instruction:
Read only the failing SHA, relevant diff, exact test logs, symbols/AST and
existing checkpoint. Reproduce, diagnose root cause and apply the smallest
authorized repair. Add targeted regression fixture and re-run CI. Submit
a new immutable commit and receipt on the SAME job branch. Never expand
scope into architecture redesign or unrelated cleanup; escalate if ABI
or ownership decisions are missing. Do not merge.

## Native event Work prompt — W03 Sol Medium Senior Integrator

Trigger: approved integration-stage PR ready/review event.
Condition: all upstream jobs, independent QA and source SHAs verified.
Instruction:
Verify exact upstream commits, reviewed ABI, ownership/lease, QA receipts
and approved dependency order. Stitch only authorized subsystems. Run full
target build, cross-module linking and regression tests. Submit the
integration branch's final commit SHA and matching Drive report. If a
contained bug appears, dispatch the exact defect back to W02 instead of
silently reengineering it. Require owner decision for interface changes.
Do not merge or self-accept.

## Native event Work prompt — W04 Sol Medium Independent Technical QA

Trigger: PR ready-for-review/review event, matching QA job.
Condition: subject final SHA and build evidence exist, different source author.
Instruction:
Review the exact final SHA, source diff, frozen architecture and CI run
against that SHA. Run available negative tests, verify resource ownership,
ABI, error propagation and regressions. Publish PASS/REWORK with evidence,
all NOT_RUN items and actionable findings. Never edit reviewed source.
Do not mark owner acceptance or merge.

## W05 Sol 6.1 High Principal Escalation

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

After owner review, separately configure the four Work subscriptions in
Work mode and explicitly approve a credit budget. Verify event triggers
and model selection in Scheduled. Run a non-spending PR routing pilot,
then a single owner-authorized junior Luna code pilot, followed by CI,
senior integration and independent QA. Record actual credit cost, rework
count, final source SHA and accepted deliverable. Do not claim deployment
before the Work subscriptions, exact event triggers and execution receipts
are read back.
