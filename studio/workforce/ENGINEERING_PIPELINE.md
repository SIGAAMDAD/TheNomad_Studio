# Engineering CI → compact Sol review → integration → source PR

STATUS: REVIEWABLE PIPELINE. Does not change live dispatch, live source-write flags, scheduled task models, source CI installations or owner merge policy.

## Source mapping (existing authority)

Controller: SIGAAMDAD/TheNomad_Studio (main policy; studio/runtime live state).

- `framework_engine`: SIGAAMDAD/NomadFramework, base `develop`
- `rendering_engine`: SIGAAMDAD/Terrain3D, base `Terrain3D_NomadRenderer`
- `core_gameplay`: SIGAAMDAD/The_Nomad, base `Nomad3D`
- `mod_gameplay`: SIGAAMDAD/NomadMain, base `main` (C#)
- `goap_core`: SIGAAMDAD/TheNomad_GOAP, base `main`

Always resolve the **fresh exact commit SHA** at job admission. The historical branch SHAs in source_targets.v1.json are not source-freeze approvals.

## Worker lifecycle

1. S00 dispatches a source-grounded job and frozen owned/protected paths to E01–E04 (Luna Medium), up to the current live writer cap.
2. Each implements on `studio/<JOB_ID>` inside the actual source repo, commits checkpoints/final candidates, and publishes a Drive handoff at the actual SHA. No producer PR.
3. GitHub Actions runs the repository's real build/test suite and generates a review packet for that exact SHA using `scripts/engineering_packet.py`. The repo must install the workflow in its **own** `.github/workflows` to receive source push triggers. The optional workflow template is at `integrations/github-actions/engineering-review.yml`.
4. CI writes the small artifacts listed below. Formatting is presented as a diff artifact or check failure, **never an unreviewed automated source mutation**. Clang-based AST index and Clang/Tidy SARIF are emitted only when installed compiler databases/tools are available; C# uses Roslyn analyzers and dotnet build when supported.
5. E05 (Sol Medium/High) owns ALL diagnostic/correctness fixes and writes repair commits to the same branch under an exclusive lease. Each changed SHA invalidates prior approval. CI repeats.
6. E07 performs **independent** technical QA on the E05-final SHA and writes immutable exact-SHA approval/rework.
7. E06 integrates approved jobs in DAG order on `studio/integrate-<FEATURE_ID>` in each actual source repo, runs cumulative CI, obtains renewed independent QA on the integrated SHA, and **then opens/updates one PR** targeting that repo's canonical development branch. Reopened reviews retain their same feature/job history.
8. Human owner reviews and merges the PR. No automatic merge.

## Compact context packet

One `INDEX.md` (normally readable in a few hundred tokens) plus `INDEX.json`, `CHECKS.json`, `FINDINGS.json`, `AST_INDEX.json`, `MANIFEST.json` and `FULL_DIFF.patch`. The reviewer first reads the index and only the relevant detailed patch/symbols.

- `INDEX.json`: exact baseline/head SHAs, digest of full diff, file changes, line deltas, numbers of failed/incomplete checks and new static findings.
- `FINDINGS.json`: failed/incomplete checks and deduplicated SARIF finding IDs, compared to the baseline SARIF if supplied.
- `CHECKS.json`: actual test/compile statuses and exact head SHA, including NOT_RUN for missing toolchains.
- `AST_INDEX.json`: externally computed symbol/translation-unit metadata if available, otherwise NOT_RUN; never feed a full AST JSON/XML dump into the primary review context.
- `FULL_DIFF.patch`: authoritative detailed change evidence; reviewer opens specific hunks on demand.
- `MANIFEST.json`: SHA256 for each packet file (except itself).

Store additional raw AST, compile_commands.json, SARIF, compiler logs, crash outputs and tests in separate uploaded workflow artifacts. Do not print full compiler logs or ASTs into the primary review prompt. If a required tool is unavailable, mark the gate NOT_RUN and block approval until it can run on an authorized build environment.

## Reviewer outcome contract

E05 records finding ID, severity, source path:line, diagnosis, exact fix commit, regression command and CI run URL. Review is SHA-bound. E07 signs separate QA. E06 publishes PR only after exact integrated SHA checks and E07 QA. Every workflow must fail closed when job/lease/branch/source registry or evidence disagrees.

## Deployment boundary

The reusable workflow file in this repository is **a source-repository template**, not an installed workflow in the other five repositories. CI jobs requiring native SDKs, Windows GPU, Godot builds, FMOD or custom toolchains must be provided by the source repository's verified runner. This project does not create paid Work tasks or change GitHub repository branch protections automatically.
