# Production authority, independent QA and completion gates

## Authority matrix

| Gate | Producer may do | Independent reviewer must do | Human Lead Director controls |
|---|---|---|---|
| Creative brief/design | propose and iterate with stable IDs | check internal consistency, dependencies, constraints | accept major design direction and changes to canon |
| Quest/dialogue/world content | author actual content and schema | validate branches, lore, continuity, gameplay integration | accept final canon/published content |
| Code and engine | commit actual source to assigned source repository under approved lease | CI/test exact SHA and independent technical QA | approve architecture departures and final merge |
| Art/animation/audio | produce actual bytes, manifests and reproducible source | inspect actual deliverables, hashes and integration constraints | accept final creative direction/release |
| Release | draft manifests, scope, regression status | verify evidence and readiness | authorize publication and spend |

## Evidence ladder

`SCOPING -> READY -> IN_PROGRESS -> SUBMITTED -> VERIFIED -> OWNER_ACCEPTED -> FINALIZED` with `BLOCKED` and `REWORK` as explicit nonterminal paths. This is a derived work-item lifecycle; R5R service state is authoritative; projections cannot invent or override lifecycle transitions.

- `IN_PROGRESS` requires observed authored work or a checkpoint; a discussion alone cannot imply an external worker ran.
- `SUBMITTED` design/content requires real artifact URI with exact revision plus structured acceptance review input.
- `SUBMITTED` code requires verified final commit SHA **in the correct source repo**, matching Drive handoff and configured source-write approval. A checkpoint commit is not final.
- `VERIFIED` code requires actual CI/build evidence and an independent QA verdict for the **same exact SHA**. A review of a previous commit is stale after a new commit.
- `VERIFIED` creative content requires independent coherence/continuity review on the exact revision. `VERIFIED` produced assets additionally require actual bytes, file hashes and relevant import/render/listen checks; a concept brief is not a modeled asset.
- `OWNER_ACCEPTED` is a real human approval event, not a worker's decision or a generic Issue label comment; final merges, canon and release remain with the owner.
- `FINALIZED` requires the separate controller finalization evidence.

## Specific domain review requirements

**Quest:** stable IDs, all reachable states and endings, recovery from death/abandonment/reload/streaming, dialogue and world fact bindings, branch consequences, encounter pacing, runtime dependencies.

**Narrative:** source canon provenance, viewpoint and voice consistency, no invented canonical setting details without owner review, playable conditions and complete authored scenes where requested.

**Balance/system design:** explicit assumptions and formulas, tunables, negative/edge fixtures, deterministic simulations where feasible, constraints from enemy AI, weapons, progression and economy.

**Code:** build and test only in the authorized repo; compile and static analysis as available, correct source ownership and lease, actual performance measurements when claimed; architecture Enforcer on ABI/ownership change; integrations through integration reviewer and independent independent technical QA QA, never self-review.

**Assets:** hashes, file paths, export/import provenance and visual/audio review; do not represent placeholder descriptions as completed 3D models, animation, sound or footage.

## Reviewer independence and rework

The reviewer must be a different author/run, not the same skill acting twice and calling itself independent. If no independent worker is available, mark `QA_PENDING`, provide the exact review brief, and do not promote `VERIFIED`. `REWORK` stays on the same original job, branch/PR and child issue, with issue-linked finding IDs and exact failing evidence.

Architecture and canon owner approvals are separate from ordinary QA. A specialist may propose but cannot silently alter frozen interfaces or accepted world facts. `source_writes_enabled=false` in the live Studio config is authoritative until actually changed by an owner-approved action. No paid use or Work activation without authorization.
