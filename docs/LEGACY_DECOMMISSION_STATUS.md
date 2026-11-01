# Legacy decommission verification

## Source removal: complete

- The ten-slot S00-S09 scheduler, cron-driven GitHub workflows, JSON job/slot controller, and Work-era prompts were removed in [PR #36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36).
- The old intake skill, timed Apps Script source, disconnected JSON source policies and historical submission handoff files were removed in [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37).
- Both PRs are merged into `main`. Current durable worker, shift, command, receipt and source authority is R5R; see [Production pipeline](PRODUCTION_PIPELINE.md).

## External installations: not verified

Removal of repository source cannot by itself deactivate an installed Google Apps Script trigger, an installed copy of the removed ChatGPT intake skill, or a previously configured ChatGPT scheduled task. These must be independently inspected through their respective administration interfaces before declaring external decommission complete. No such external revocation was performed by the repository cleanup.

## Retained dependency

`integrations/github-actions/engineering-review.yml`, `scripts/engineering_packet.py`, and their tests remain in the repository. Their potential consumers in private/unavailable source repos were not all accessible. The old review packet is **not** an active Studio scheduling service and its removal is deferred until those repo-specific dependencies are verified and migrated.

Source workflow inventory observed during PR #37 planning:

- `SIGAAMDAD/Terrain3D`: eight workflow files; no `engineering-review.yml` in the listed workflow directory.
- `SIGAAMDAD/NomadFramework`: one workflow file; no `engineering-review.yml` in the listed workflow directory.
- `SIGAAMDAD/TheNomad_GOAP`: one workflow file; no `engineering-review.yml` in the listed workflow directory.
- `SIGAAMDAD/The_Nomad` and `SIGAAMDAD/NomadMain`: workflow directories inaccessible from the connected view; no absence claim made.

These observations are historical, not a continuous monitor of other repositories. Runtime acceptance F-20 remains separate from source cleanup and external trigger decommission.

## External decommission evidence contract

For each known legacy Google Apps Script deployment, record the actual script
project ID, installed trigger IDs, administrative actor, trigger listing and
post-revocation readback. For each old scheduled task or installed intake skill,
record exact installation identity, enabled status and its administrative removal
or disablement receipt. Keep an item `NOT_VERIFIED` until the relevant provider
confirms it; repository Git history is not proof that external schedulers stopped.

Likewise, remove the retained engineering-review helper/template only after
all five source repository owners confirm their downstream workflow consumers.
The repository connector can currently inspect the public Terrain3D,
NomadFramework and TheNomad_GOAP workflows; private The_Nomad and NomadMain
remain outside the verified source set.
