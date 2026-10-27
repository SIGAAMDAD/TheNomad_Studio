# Source repository ownership contract (Studio V2)

STATUS: REVIEW DRAFT. No paid workers, source writes, or cross-repo Work triggers activated.
Authoritative registry: studio/workforce/source_targets.v1.json

## Control plane versus source plane

SIGAAMDAD/TheNomad_Studio is a control-plane repository only. It may hold
approved work manifests, controller code, worker policies, routing rules,
immutable evidence pointers, QA reports and job receipts. No GOAP DLL source,
game code, GDExtension implementation, terrain source or raw code snapshot
belongs in the Studio repository. Controller maintenance PRs are allowed
because that is Studio infrastructure rather than game implementation.

Verified source repository and integration branch selections:
- GOAP source: SIGAAMDAD/TheNomad_GOAP — target main.
- Framework/GDExtension: SIGAAMDAD/NomadFramework — target develop.
- Active game: SIGAAMDAD/The_Nomad — target Nomad3D.
- Terrain fork: SIGAAMDAD/Terrain3D — target Terrain3D_NomadRenderer.

Do not default The_Nomad or Terrain3D work to main. No code may be written
in TheNomad_Studio for any of these source components.

TheNomad_GOAP currently has only README.md and LICENSE on main. Its
inspected head is 5258a0a05d4240cc772c225bdb1868c652b7a052.
This is source provenance only, NOT an owner-accepted compile/source freeze.
Its main branch was unprotected when inspected; configure PR-only source
merges and required CI checks before unattended source edits.

## Required source job fields

EXAMPLE ONLY (not an admitted production job):

    {
      "id": "FEATURE-6-G00",
      "kind": "code",
      "role": "rpg-ai-programmer",
      "repository": "SIGAAMDAD/TheNomad_GOAP",
      "branch": "main",
      "work_branch": "studio/FEATURE-6-G00",
      "baseline_sha": "<PIN_EXACT_VERIFIED_SOURCE_SHA>",
      "owned_paths": ["include", "src", "tests"]
    }

Branch means integration BASE, work_branch means the exclusive writable
branch inside the SAME source repository. Source baseline SHA must be
retrieved and verified at job intake; never copy an old SHA from planning.
No unapproved fork, default-branch fallback or cross-repo source push.

## Worker clone, commit and push sequence

1. Read the owner-approved Studio job, exact repo, target branch, pinned
   source SHA, writable paths, source freeze and exclusive writer lease.
2. Authenticate and clone the source repository from the job record.
3. Fetch and verify exact target SHA; verify branch ancestry and source
   lock, not merely the branch name.
4. Create work_branch studio/<JOB_ID> from approved SHA, or resume that
   exact existing work branch and checkpoint after a fenced handoff.
5. Commit implementation and tests only into work_branch IN SOURCE REPO.
6. Push only the work branch to origin IN SOURCE REPO. Never push to main,
   Nomad3D, develop, Terrain3D_NomadRenderer or a Studio hub code branch.
7. Open/update PR in same source repo, head=work_branch, base=job.branch.
   Include one PR body line: Studio-Job: <JOB_ID>.
8. CI runs against source commit in source repo. Studio stores SHA, URL,
   tested results, failure fingerprints and Drive handoff; never code.
9. W02 Terra can complete the feature on the same job branch after a safe
   lease handoff. W03 Sol debugging owns exact defect repair; W04 Sol
   integration owns separate approved integration jobs/branches; W05 Sol
   independently reviews the final SHA without touching source.
10. Owner reviews and merges accepted PR IN THE SOURCE REPOSITORY.

Illustrative GOAP bootstrap, NOT to execute before source freeze:

    git clone https://github.com/SIGAAMDAD/TheNomad_GOAP.git
    cd TheNomad_GOAP
    git fetch origin main
    git switch -c studio/FEATURE-6-G00 <PINNED_GOAP_SHA>
    # Edit only approved paths and run actual build/tests
    git add <OWNED_PATHS>
    git commit -m "feat(goap): initial implementation for FEATURE-6-G00"
    git push -u origin studio/FEATURE-6-G00

Do not re-create/reset an existing work branch or force-push. A worker
continuing an interrupted job must verify that its prior owner is done
or fenced and resume the same branch/PR from the verified checkpoint.

## Correct event triggers

Work native PR tasks must subscribe to the *actual source repository*,
e.g. SIGAAMDAD/TheNomad_GOAP for GOAP. They should not subscribe to
TheNomad_Studio and accidentally operate on its controller PRs.
A Work task must read the authoritative Studio runtime job on every event,
verify real owner approval, source SHA, source repo, work branch, CI status,
lease, source-write and spending gates.

The Studio repository's own PR GitHub Action is only a read-only audit.
It cannot observe a GOAP PR unless the event is explicitly delivered by
an authorized cross-repository mechanism. Do not pretend labels in Studio
automatically trigger Work in another repo.

For a NEW job before source PR exists, S00 must create a bounded source
work-order PR in the SOURCE repository (or owner manually creates one).
The bootstrap PR artifact, if necessary, stays IN THE SOURCE repository and
only points to the Studio job. Alternatively use a supported Work event
that genuinely exists for that source repo. Never stage implementation
source in Studio as a trigger workaround.

## GOAP dependency path

1. TheNomad_GOAP:main — standalone library, cooker, schemas, managed
   contracts, deterministic tests and build.
2. NomadFramework:develop — independent adapter integration, consuming
   exact verified GOAP revision; version/submodule linkage requires an
   approved design choice.
3. The_Nomad:Nomad3D — game-specific GOAP agents/Sortior Blader integration.

Each downstream job is separate, pinned to a distinct actual repository,
branch and commit. No game code is moved into Studio. The Sortior Blader
design document can be drafted earlier without manufacturing runtime code.

## Validation and outstanding gates

- source_targets.py verifies exact allowed repo/base and work_branch.
- production.py refuses wrong-repo source specs during admission.
- studio.py refuses active source jobs routed to Studio or bad branches.
- work_event_router.py rejects wrong source PR base/head/fork.
- The live runtime allowlist currently lacks TheNomad_GOAP/main, and needs
  a separately reviewed configuration update. Existing source_writes_enabled
  and auto_ai_enabled switches remain FALSE until source and owner gates.
- Source baseline and build evidence have not been accepted. No Work
  subscription or push permission is activated by this document.
