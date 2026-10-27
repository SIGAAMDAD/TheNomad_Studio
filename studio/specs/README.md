# Feature decomposition contracts

A producer/Director creates `FEATURE-N.json` only after reading the owner's issue and actual relevant technical/creative baselines.

- Every spec is linked to its existing parent GitHub Issue.
- The owner grants `studio:approved` on the issue (actor verified through issue events).
- Job IDs are stable, role/manual explicit, source baseline and owned paths exact. The dispatcher never invents roles, source commits, canon or deliverable revisions.
- The deterministic admission tool will create one QA job per productive job, and convert intra-feature dependencies to verified producer QA dependencies.
- An unapproved or malformed spec is not admitted; code work remains BLOCKED until the separate source gate is authorized.
- Never overwrite an active job or change an existing approved scope by resubmitting a spec. Write a reviewed amendment instead.

See `docs/PRODUCTION_PIPELINE.md` for complete flow.


## Engineering source identity and branch contract

Studio is intake/control/QA only. An implementation or integration job
must name a real allowed source repository and its exact owner-selected
integration target branch. Required fields are:
workstream (approved source responsibility), repository, branch (target),
work_branch (studio/<JOB_ID> created in the same repository), baseline_sha
(live pinned source SHA), owned_paths, protected_paths, QA role, frozen
contract and acceptance tests. For mod_gameplay, language must be "C#".
See studio/workforce/source_targets.v1.json for the current authorized
mapping. Approved owner workstream graph:
rendering_engine -> SIGAAMDAD/Terrain3D:Terrain3D_NomadRenderer;
framework_engine -> SIGAAMDAD/NomadFramework:develop (logical main);
core_gameplay -> SIGAAMDAD/The_Nomad:Nomad3D (not mod dependent);
mod_gameplay -> SIGAAMDAD/NomadMain:main in C#;
goap_core -> SIGAAMDAD/TheNomad_GOAP:main.
Select by the work owned by the job, never by the worker's title alone.
Do not put game source in TheNomad_Studio or substitute a GitHub default
branch for an owner-selected development branch.

The Studio runtime config currently needs GOAP/main added to its allowed
source-repository list before code is made READY. Keep source-write and
paid Work gates disabled until separately authorized. The job's source
work branch and PR must live in the actual source repository. CI and
source SHA must come from there; Studio stores receipts only.

See `studio/workforce/SOURCE_REPOSITORIES.md` for the exact source ownership graph, example mod-specific C# source job, and cross-repository dependency requirements.


## Architectural changes: required contributor proposal pull request

All new `code` or `integration` spec entries must set
`"architecture_impact": "none"` or `"change"`. An omitted impact is
invalid, not silently treated as none.

If the task needs to change approved architecture/ABI/public interfaces,
classify as change and stop incompatible implementation. The Architecture
Enforcer (S00 triage, W05 independent review, W06 on-demand principal)
requires a **real pull request in the owning SOURCE repository** using
`studio/arch/<JOB_ID>`, targeting the explicit approved integration branch.
The proposal must state before/after contracts, owner evidence, alternatives,
affected repositories, migration and rollback. Do not create it in Studio.

The original source job remains BLOCKED until owner approval/merge of that
source PR at the exact reviewed proposal SHA has been independently read
back from GitHub; then rescope/re-pin the job from the new source baseline.
Do not mark code complete from a proposal or allow workers to self-approve.
See `studio/workforce/ARCHITECTURE_ENFORCER.md`.
