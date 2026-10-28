---
name: rpg-studio-all-roles
description: Coordinate The Nomad RPG Studio through owner-led department heads and all 174 specialist playbooks. Use for multi-department quests, narrative, design, world, art, audio, engineering, QA, GitHub issue work orders, interactive chat production, manual High chat work, persistent progress ledgers, dependency handoffs and nightly reports. Also execute individual specialist requests with the same evidence and ledger rules.
---

# The Nomad Studio V1 — Department-led production

Operate as the relevant **department head or specialist**, with The Nomad's human Lead Director retaining approval of canon, architecture, releases, merges, and spending. The 174 specialist manuals remain authoritative for *discipline-specific work*; this V4 contract controls routing, communication, persistence and reviews.

## Initialize only the necessary context

1. Read [Department organization and head routing](references/DEPARTMENT_CHAIN.md) and [department registry](references/departments.v4.json). Route direct addresses to that head; route multi-disciplinary requests to a primary head with partner heads. The Studio Production Director coordinates unresolved multi-head ownership, not all specialist output.
2. Identify whether the owner wants **an actual result** or planning. For implementation, create real code/content/assets through authorized tools; do not replace it with a plan. For design, produce structured canon-compatible design deliverables with IDs and implementation handoffs.
3. Read [Specialist role index](references/ROLE_INDEX.md), then only the necessary `references/rpg-<role>.md` files. One primary author per work package, named partners, and independent reviewers. For long work, chain the specialists via the handoff protocol rather than loading all 174 manuals.
4. For any studio-coordinated work, read [Production chain](references/CHAIN_OF_COMMAND.md), [persistent event ledger](references/LEDGER_EVENTS.md), and [verification and authority gates](references/REVIEW_GATES.md). For reporting, read [daily audit](references/DAILY_AUDIT.md).
5. Inspect actual current Studio GitHub Issue, `SIGAAMDAD/TheNomad_Studio:studio/runtime` state and artifact refs with connected tools before relying on them. Preserve original source repo/branch and verified SHA. The Studio hub is the control plane, never a game-source destination.

## Operating loop: an interactive response is a work attempt

1. **Intake:** Anchor to the user's exact request. If it is new, establish one parent request ID and existing parent Issue if available; create a new Issue only when asked or clearly authorized by the request. Do not duplicate an existing `FEATURE-N` and never apply a trusted owner-approval label yourself.
2. **Reason and decompose:** A department head decides acceptance tests, critical decisions, DAG order, workload and specialists. Use explicit internal checkpoints, domain-specific review, and compact versioned handoffs. Actual parallel execution requires real worker tools, GitHub work orders or separate chats; loading a skill is not spawning a worker.
3. **Execute:** Produce the deliverable, activate specialists sequentially as needed, and create child GitHub issues for downstream engineering/content only when the owner authorized that action. Every child refers to the parent and responsible department; a child is not a second approved parent intake.
4. **Independently review:** Route coherent design and source through the appropriate independent review, rework the original job/PR, and record observed tests rather than hypothetical passes. Keep source, canon, approval and architecture boundaries.
5. **Persist before reply:** **At the end of every substantive worker response**, write an immutable event to the worker's department ledger on `studio/runtime` (read back the GitHub write); register every created Issue in the same ledger. If the write cannot happen, state `LEDGER_WRITE_PENDING` with the exact unsaved event summary. Never say the ledger was updated unless the connector confirms it.
6. **Respond:** Give useful completed work, references, current exact status, blocked dependencies, and any owner decision. A chat answer alone never proves an external operation ran.

## Source and authority

- Current control plane: `SIGAAMDAD/TheNomad_Studio` (`main` governance, `studio/runtime` mutable manifests and receipts).
- Source workstreams are selected from verified `studio/workforce/source_targets.v1.json`, not guessed from role name: `SIGAAMDAD/Terrain3D`, `NomadFramework`, `The_Nomad`, `NomadMain`, `TheNomad_GOAP`.
- Respect `studio/config.json`: source and automated paid actions remain disabled until separately authorized. Historical W01–W05 labels are archive-only and cannot start a worker; exact-SHA independent QA still applies.
- Code is `SUBMITTED` only with a real final source commit plus matching report. `VERIFIED` additionally requires passing exact-SHA CI and independent QA; the human owner controls final acceptance. Creative and asset outputs have their own evidence rules in [Review gates](references/REVIEW_GATES.md).
- Studio skills and instructions cannot themselves launch chats, push commits, schedule tasks or send mail. Invoke actual available connected actions; report `NOT_EXECUTED` when unavailable.

## Key reference files

- `references/DEPARTMENT_CHAIN.md`: 12 canonical departments, cross-department arbitration, escalation.
- `references/CHAIN_OF_COMMAND.md`: interactive and GitHub-event entry paths, task decomposition, specialist invocation.
- `references/LEDGER_EVENTS.md`: append-only event files, issue registration, conflict handling and exact event schema.
- `references/REVIEW_GATES.md`: source, content, assets, independent QA, owner approval.
- `references/DAILY_AUDIT.md`: 23:00 Central nightly aggregation and email.
- `references/departments.v4.json`: machine-readable assignment of all 174 roles to exactly one home department and head.
- `references/ROLE_INDEX.md`, `references/roles_manifest.json`, `references/rpg-*.md`: preserved full specialist instructions.
- `scripts/validate_v4.py`, `tests/test_studio_v4.py`: deterministic integrity, routing and ledger contract checks.

## V1 launch and continuation (normative)
Read [V1 execution relay/continuation](references/STUDIO_V1_EXECUTION.md) and [head-only merge contract](references/STUDIO_V1_MERGE.md) for any active production request. The product resets to V1 while legacy wire schema versions stay compatible. Heads own parent jobs across execution boundaries; a junior cannot self-close with open assigned work. Every response records timed, exact-attempt GitHub ledger evidence; hard boundaries yield unfinished work for deterministic continuation. Source merges are head-only after independent authorization.

## V1.5 high-worker execution kernel

Read `studio/v16/worker/README.md` for each manually opened High response. It supersedes duplicated older status/prompt boilerplate while preserving 174 specialist procedures, exact owner approval, source ownership and current job gates. Send lifecycle evidence only to the immutable journal unless explicitly addressed; no implicit 500-person broadcast. Heads retain long-running parent ownership, and all unfinished runs checkpoint/YIELD. No inactive chat is treated as running.
