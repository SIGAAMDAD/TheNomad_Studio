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
repository, branch (target), work_branch (studio/<JOB_ID> created in the
same repository), baseline_sha (live pinned source SHA), owned_paths,
protected_paths, QA role, frozen contract and acceptance tests.
See studio/workforce/source_targets.v1.json for the current authorized
mapping. GOAP source is SIGAAMDAD/TheNomad_GOAP:main; framework adapters
belong to SIGAAMDAD/NomadFramework:develop; game integration belongs to
SIGAAMDAD/The_Nomad:Nomad3D. Do not put native source in TheNomad_Studio
or substitute another repository's default branch.

The Studio runtime config currently needs GOAP/main added to its allowed
source-repository list before code is made READY. Keep source-write and
paid Work gates disabled until separately authorized. The job's source
work branch and PR must live in the actual source repository. CI and
source SHA must come from there; Studio stores receipts only.
