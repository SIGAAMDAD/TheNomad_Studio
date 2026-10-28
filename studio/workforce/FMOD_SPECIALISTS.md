# FMOD Specialist Skill Family (additive overlay)

Drive library: https://drive.google.com/drive/folders/1n1FwgC5jdJ5cOJodAZR0SyIQXU8RV3r3
FMOD packages: https://drive.google.com/drive/folders/1XmUua69COtttScX9H7TA2C-hXSbdd-3Y
FMOD complete transport bundle: https://drive.google.com/file/d/1OCxOTGnvb_pR05aiTzXMgGwOQ_Tdmfw2/view

## Registration

Read `studio/workforce/fmod_roles.v1.json` as an **additive 20-skill family**: 19 specialist roles and one FMOD router. Preserve the canonical original 174-role roster and its existing schema until a separate approved migration. For any FMOD task, first select the FMOD router, then the primary specialist role, then independently assigned reviewer. The original Studio architecture/workforce policies remain authoritative.

## Division of responsibility

- **Audio engineering** (9 specialists): audio asset production, recording, mixing/mastering, spatial acoustics, adaptive score, VO, FMOD Studio authoring and format prep. Accountable head: `rpg-audio-director`.
- **Audio programming** (9 specialists): FMOD Studio/Core SDK, C++ DSP/codec/output plugins, Godot/FMOD integration, bank builds, scripting and runtime profiling. Accountable head: `rpg-lead-engineer`.
- **Independent QA**: `fmod-audio-qa-analyst` under `rpg-qa-lead`.
- **Router**: `fmod-specialist-router` coordinates only; it is not a replacement department head.

## Operational flow

1. Inspect the currently approved Studio job, its parent Issue, source commit/revision, project/SDK version, owned paths and artifacts.
2. Load one exact specialist skill ZIP from Drive/installed skills; keep single-artifact write ownership.
3. Create an explicit event/bank/parameter contract when transferring work from audio engineering to programming.
4. Execute only the authorized asset or code work; report performed/NOT_RUN build, audition and test evidence.
5. Hand off exact Git SHA/asset hashes to independent QA and post a department ledger checkpoint on the Studio runtime branch when authorized.

## Example handoff

The sound designer creates an authored weapon cue. The FMOD Studio event author creates `event:/weapons/rifle/fire` and defines required parameter IDs/ranges. The FMOD Studio API programmer integrates runtime events in Godot. The independent audio QA analyst checks bank contents, repeated triggering, stop/release behavior, mix balance, latency and actual game playback.

The event name is an illustrative example, not an approved live project identifier.

## Scope and permissions

This branch contains registration documentation only, not activation of Work jobs or modifications to runtime owner approval gates. A Drive ZIP is a portable skill; it is not automatically installed in every ChatGPT worker. Existing Studio merge, QA, runtime and authority rules continue to apply.
