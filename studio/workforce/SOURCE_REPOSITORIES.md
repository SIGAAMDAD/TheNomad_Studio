# Source repository ownership graph — Studio V3

STATUS: OWNER-DEFINED ROUTING DRAFT. No paid workers or source writes activated.
Authoritative structured map: `studio/workforce/source_targets.v1.json`.

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
source_targets registry. `mod_gameplay` additionally requires
`language: "C#"`. Missing or mismatched workstream, branch,
work branch or language makes an engineering job invalid.
`baseline_sha` must be read from the actual source branch and
verified at job admission; observed SHAs in the registry are not
automatically frozen baselines.

## Worker pull, commit, push and source PR rules

1. Studio Production Director admits an owner-approved scoped job with exact source repository,
   workstream, branch, baseline SHA, authorized paths and exclusivity.
2. W01–W04 fetch from THAT source repository. The work branch is
   `studio/<JOB_ID>` **inside that same source repository**.
3. The worker modifies only approved paths, commits/pushes only its
   work branch and opens/updates a source PR with base equal to the
   canonical development target. It must never push to source
   integration branches directly.
4. Source CI tests the exact source commit. Studio stores immutable
   source commit SHA, PR URL, CI run, Drive completion report and QA
   receipt. Studio receives no implementation source.
5. W02 can continue W01's branch after an exclusive-lease handoff;
   W03 performs difficult debugging; W04 integrates independently
   verified systems under a separate integration job; W05 reviews
   the exact source revision without editing it.
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

- The reviewed `main` controller allowlist can name all five
  source targets. `studio/runtime/studio/config.json` must be
  reconciled separately; do not treat this draft as live dispatch.
- `source_writes_enabled=false` and `auto_ai_enabled=false`
  remain in effect. Work spending/credentials are separately gated.
- Studio Production Director must acquire source baselines and actual build evidence before
  admitting source jobs. The canonical source registry is not frozen.
- The reusable W01-W05 native Work tasks may subscribe to *Studio hub
  work-order PR* activity as a control-plane signal. These PRs contain
  metadata only; their exact source repository, baseline SHA and source
  branch are validated against the approved job. All source code commits,
  source CI and implementation PRs remain exclusively in the actual source
  repositories. See GENERALIST_WORK_SETUP.md.
- The reviewed Studio PR routing GitHub Action is only a non-spending
  audit; it does not itself start native Work executions.
- The source-repo-specific commit/QA/owner-merge gates remain binding.
