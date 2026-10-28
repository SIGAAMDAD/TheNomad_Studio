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
S00 must select an explicit workstream in the *owner-approved job spec*;
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

## Manual High source work and independent publication

1. The owner/head prepares an approved, bounded job with explicit source repository, workstream, branch, baseline SHA, allowed paths, and independent QA recipient. Source registry metadata alone is not proof of an authenticated grant.
2. The user **manually opens** GPT-6 High with the source snapshot and role launcher. The worker operates in a local worktree, with no GitHub/Drive calls unless first requesting specific provider, resource/ref/path, READ/WRITE and purpose and receiving explicit consent.
3. Workers perform all feasible scoped edits, run local tests and export **one batched binary patch** plus manifest, test evidence and response attempt report card. No shared owner/admin connector publication; no automatic source pushes. A local commit is not a verified remote SHA.
4. After independent exact-patch QA, an independently authenticated owner/head may publish to a noncanonical source work branch through the actual verified gateway/provider and read back exact SHA. Canonical merge is separately owner-gated. QA remains source read-only and engineering integrators remain staging-only. WG-D/G/I own gateway/publisher/cutover deployment.
5. A new High response is a new `attempt_id`. Continue from `RUN-V16/RESUME/1`, comparing immutable source hash, epoch, lease and owned paths before any authorized external write; relay/ledger writes without readback are `PENDING`.

If a task spans multiple repositories, split into separately scoped source jobs and independent QA. Never move game code into the Studio control plane or auto-change branches to GitHub defaults.

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
cross repositories or modify approved interfaces. S00 invokes the
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
- S00 must acquire source baselines and actual build evidence before
  admitting source jobs. The canonical source registry is not frozen.
- Historical W01–W05 event labels are **archive-only**; no Work subscriptions or
  Studio hub PR events may launch ChatGPT. See GENERALIST_WORK_SETUP.md.
- WG-G will retire the old PR router and its CI entrypoints; until then,
  deterministic audit actions have zero authority to launch an AI response.
- The source-repo-specific commit/QA/owner-merge gates remain binding.
