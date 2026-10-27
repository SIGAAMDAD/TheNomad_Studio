# ChatGPT scheduled task - Studio bindings

One S00 Production Director supervises up to nine reusable S01-S09 workers. GitHub stores the jobs, ChatGPT tasks wake on their own hourly recurrence, and GitHub Actions applies immutable result receipts. GitHub events do not immediately wake a normal scheduled Chat task.

## Branch contracts

- Reviewed controller and contracts: SIGAAMDAD/TheNomad_Studio:main
- Mutable job state and receipts: SIGAAMDAD/TheNomad_Studio:studio/runtime
- Approved source branches: The_Nomad:Nomad3D, NomadFramework:develop, Terrain3D:Terrain3D_NomadRenderer

## Reused task identities

| Slot | Existing saved task | CT minute | Default capability |
|---|---|---|---|
| S00 | Studio Production Director V1 | :00 | Director |
| S01 | Studio Flexible Specialist A | :06 | Gameplay design |
| S02 | Studio Flexible Specialist B | :12 | Systems design |
| S03 | Lore & Worldbuilding Writer | :18 | Narrative |
| S04 | Gazetteer Context Test | :24 | Dialogue / quests |
| S05 | Nomad Production Director (legacy repurposed) | :30 | Art / UX |
| S06 | Studio World Streaming Programmer | :36 | Engineering analysis |
| S07 | Gameplay Programmer | :42 | Implementation |
| S08 | Studio Rendering Programmer (repurposed) | :48 | Independent creative QA |
| S09 | Studio Patch & Regression Engineer | :54 | Technical QA |

The role is dynamic and determined by each eligible job, not the historic name. AI Programmer, old monitor tasks, and Drive pilots remain paused and are not included in the ten active-slot roster.

## Owner handoff / activation

1. Merge the director/QA correction PR when its checks pass. Until merge, its new pilot role file and S00 Director contract are available on the review branch only.
2. Match scheduled task IDs in studio/runtime/studio/slots.json to actual ChatGPT task dashboard readbacks. Task management APIs cannot change reasoning effort; change models only in the UI if available.
3. Keep source_writes_enabled and auto_ai_enabled false. Turn on auto_dispatch_enabled in the runtime config only after the task bindings and pilot are configured.
4. Enable only S00, S01 and S08 for the first infrastructure pilot. S01 writes an exact commit-specific handshake and SUBMITTED receipt. GitHub reconciliation then enables assignment for the S08 independent QA job; S08 writes its own report/receipt.
5. The Director examines exact evidence, verifies or escalates the pilot, and pauses unused specialists. Then move to approved actual production tasks, starting with two writers and one reviewer.
6. Engineering remains frozen until exact source SHAs, ABI/parity, CI, tests and real compilation pipeline exist. Green Studio controller CI is not Godot compilation.

## Operational caveats

- GitHub Actions is deterministic control logic, not GPT inference or a ChatGPT launcher.
- Scheduled tasks must actually have GitHub connector write access; verify with a real pilot readback. Scheduled execution and exact start times are not guaranteed.
- The Director does not execute the worker contract. It audits, assigns, and manages capacity. Workers produce artifacts and immutable receipt commits.
- Do not force-resume a job after a missing receipt; another writer may still own it.
- No paid model fallback, API key, default-branch auto-merge, or canon approvals are enabled.