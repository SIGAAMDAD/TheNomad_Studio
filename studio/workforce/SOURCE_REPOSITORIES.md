# Owner source repository routes — R5R

Source routes are enforced at the authenticated R5R promotion boundary.
Reviewed route contract: `src/studio/app/source_policy.py`; authenticated per-project source binding is required.

## Control plane and real source repositories

`SIGAAMDAD/TheNomad_Studio` is the production HUB. It owns intake, source
ownership records, role assignments, review and CI coordination, reporting,
authorizations, immutable Git references and receipts. It is NOT the source
repository for gameplay, rendering, framework, mods or GOAP implementation.

The owner's five current source routes are:

| Workstream | Source repository | Canonical development target | Language |
| --- | --- | --- | --- |
| `rendering_engine` | `SIGAAMDAD/Terrain3D` | `Terrain3D_NomadRenderer` | Approved per-job |
| `framework_engine` | `SIGAAMDAD/NomadFramework` | `develop` | Approved per-job |
| `core_gameplay` | `SIGAAMDAD/The_Nomad` | `Nomad3D` | Approved per-job |
| `mod_gameplay` | `SIGAAMDAD/NomadMain` | `main` | **C#** |
| `goap_core` | `SIGAAMDAD/TheNomad_GOAP` | `main` | C++17 / approved interop |

The `NomadFramework/develop` branch is the *logical main development
branch* for framework engine programming, even though GitHub's configured
repository default branch may be `main`. Do not substitute the default.

`NomadMain` is a separate private repository. The `main` branch contains
`NomadMain.csproj` and C# gameplay source under `Source/`.
Its repository and branch were verified at intake. Keep mod-dependent
gameplay implementations and associated feature changes there, rather
than in the reusable core-gameplay repository.

Rendering-engine programming belongs in the Terrain3D fork's active
`Terrain3D_NomadRenderer` branch. Framework-engine development belongs
in `NomadFramework/develop`. Gameplay code with reusable/non-mod-specific
ownership belongs in `The_Nomad/Nomad3D`. Mod-specific code belongs in
`NomadMain/main` and is authored in C#. GOAP remains a standalone library
in `TheNomad_GOAP/main`.

**Worker role alone does not determine source ownership.** A gameplay
programmer may be assigned either `core_gameplay` or `mod_gameplay`.
Studio Production Director must select an explicit workstream in the *owner-approved job spec*;
the same worker's GitHub repository access then follows that job.
No heuristic based on role name, default branch or file suffix is allowed.

## Exact source job contract

Example only, not admitted source work:

```json
{
  "id": "FEATURE-100-MOD-01",
  "kind": "code",
  "role": "rpg-gameplay-programmer",
  "workstream": "mod_gameplay",
  "language": "C#",
  "repository": "SIGAAMDAD/NomadMain",
  "branch": "main",
  "work_branch": "studio/FEATURE-100-MOD-01",
  "baseline_sha": "<VERIFY_REAL_SOURCE_SHA_BEFORE_JOB>",
  "owned_paths": ["Source/Items"],
  "acceptance": ["Compile NomadMain.csproj and run the specific behavior tests"]
}
```

The `workstream` and `repository` must agree with the reviewed
R5R source route policy. `mod_gameplay` additionally requires
`language: "C#"`. Missing or mismatched workstream, branch,
work branch or language makes an engineering job invalid.
`baseline_sha` must be read from the actual source branch and
verified at job admission; observed SHAs in the registry are not
automatically frozen baselines.

## Worker pull, commit, push and source PR rules

1. Studio Production Director admits an owner-approved scoped job with exact source repository,
   workstream, branch, baseline SHA, authorized paths and exclusivity.
2. assigned specialists fetch from THAT source repository. The work branch is
   `studio/<JOB_ID>` **inside that same source repository**.
3. The worker modifies only approved paths, commits/pushes only its
   work branch and opens/updates a source PR with base equal to the
   canonical development target. It must never push to source
   integration branches directly.
4. Source CI tests the exact source commit. Studio stores immutable
   source commit SHA, PR URL, CI run, Drive completion report and QA
   receipt. Studio receives no implementation source.
5. A registered specialist may continue another specialist's work only after an authenticated exclusive-lease handoff. The authorized integration owner promotes independently verified systems under a separate integration task; independent QA reviews exact source bytes without editing the producer's result.
6. Only the owner accepts and merges changes **inside the source repo**.

If a job crosses source boundaries, it must be split into child jobs
with separately pinned baselines, paths, reviews and explicit
dependencies. Never push or cherry-pick one repo's code into another
as an automatic substitute for a planned adapter.

For a new, authorized NomadMain C# job, the source Git flow is:

```bash
git clone https://github.com/SIGAAMDAD/NomadMain.git
cd NomadMain
git fetch origin main
git switch -c studio/FEATURE-100-MOD-01 <PINNED_MAIN_SHA>
# Edit only approved mod-specific C# paths, compile and test
git add Source/Items
git commit -m "feat(mod): approved mod behavior - FEATURE-100-MOD-01"
git push -u origin studio/FEATURE-100-MOD-01
```

This is illustrative only. Do not create/reset or force-push a work
branch without the source baseline, owner approval and source-write gate.

## Production dependencies

The Director records a dependency graph **per feature**, not a
globally assumed chain. For example a GOAP feature might contain:

```text
TheNomad_GOAP/main   (standalone library)
       |
       v
NomadFramework/develop   (framework integration where needed)
       |
       +------> The_Nomad/Nomad3D   (non-mod-dependent integration)
       |
       +------> NomadMain/main      (mod-specific C# behaviors when needed)
```

Rendering changes run in Terrain3D/Terrain3D_NomadRenderer, with
separate downstream integration jobs only where explicit dependencies
exist. The arrows above are an *example*, not permission to move
code or create any implicit feature dependencies.

## Architecture-change ownership

An implementation that needs an architectural change cannot silently
cross repositories or modify approved interfaces. Studio Production Director invokes the
Architecture Enforcer and creates a contributor-style **architecture
proposal PR in the repository that owns the affected contract**, using
`studio/arch/<JOB_ID>` as its dedicated proposal branch.
For example, a new reusable GOAP predicate belongs in TheNomad_GOAP,
not the Zurgut Hulk's NomadMain feature branch. No source implementation
resumes until the human owner has explicitly accepted the exact source
PR revision through a verified GitHub owner review and merge. See
`studio/workforce/ARCHITECTURE_ENFORCER.md`.

## Current safety and activation state

The active R5R trusted factory enforces `src/studio/app/source_policy.py` at the source-revision promotion boundary. Its provider must supply the project-specific `SourceTarget` plus authenticated repository ID and exact commit SHA; `validate_source_target(...)` rejects mismatched workstreams, branches, repository bindings and SHAs. For mod source jobs, `validate_source_job(...)` also requires `language: "C#"`.

The old JSON scheduler configuration and per-slot Work task automation no longer exist in `main`. Current source write permission and work admission must be established by authenticated project policy, grants and owner approval; no published prompt or stale JSON field can enable them. A source job remains blocked if its current baseline, exclusive ownership, CI, QA or acceptance evidence is absent. Browser workers run only during actual responses and use the existing GitHub relay; they are not automatically awakened by source PR activity.

Source repository CI and protections remain external to this Studio repository. The standalone engineering review packet template is retained pending verification of all external consumers, as documented in `docs/LEGACY_DECOMMISSION_STATUS.md`.
