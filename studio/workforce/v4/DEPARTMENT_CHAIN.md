# V4 department organization and escalation

## Human-led command structure

`Human Lead Director -> Studio Production Director (coordination) -> Department Head (accountable outcome) -> Workstream Lead / Specialist (single author) -> independent reviewer -> head synthesis -> owner approval (when required)`.

An individual's skill is not a distinct autonomous employee. A department head invokes a role procedure in the same conversation or queues an addressed command for a registered ordinary ChatGPT browser conversation through the R5R authenticated coordinator. Record an executing shift and its verified receipts before claiming external work occurred. When a direct role is named by the owner, honor that entrypoint and involve heads only where cross-team coordination is needed.

## Canonical departments (one home for every role)

| Department ID | Head manual | Responsibility |
|---|---|---|
| `production` | `rpg-lead-producer` | scheduling, stage ownership, cross-team dependencies, delivery synthesis |
| `design` | `rpg-design-director` | quests, systems, combat, progression, balancing, encounters and world play |
| `narrative` | `rpg-narrative-director` | canon proposals, dialogue, stories, quest writing, character arcs and cinematics |
| `engineering` | `rpg-lead-engineer` | engine, framework, gameplay, rendering, GOAP, tools, builds, performance |
| `visual_art` | `rpg-art-director` | concepts, characters, environments, props and materials |
| `tech_art` | `rpg-technical-art-director` | VFX, procedural asset workflows, shaders/tech-art integration |
| `animation` | `rpg-animation-director` | rigs, motion, gameplay/cinematic animation and capture |
| `audio` | `rpg-audio-director` | sound, music, voice, implementation and mixing |
| `ux` | `rpg-ux-director` | UI, UX and player research |
| `qa` | `rpg-qa-lead` | independent creative, content, technical and performance verification |
| `publishing` | `rpg-release-manager` | release, localization, community/publishing and marketing coordination |
| `studio_ops` | `rpg-studio-operations-manager` | documentation, operations, vendor coordination, governance support |

The source `roles_manifest.json` has 12 legacy departments. V4 preserves all 174 roles but assigns six director-level roles to their operational departments using explicit, auditable `role_home_overrides`; all other roles follow the legacy mapping. The role's original discipline procedures remain intact.

## Workstream leads (task-level, not extra staff)

- **Quest line:** `rpg-quest-designer` as primary workstream lead under Design; `rpg-narrative-designer` and `rpg-game-writer` own narrative branches; `rpg-lore-writer`/`rpg-canon-continuity-keeper` check source facts; `rpg-quest-runtime-programmer` handles runtime implementation only after accepted interfaces; `rpg-content-validation-engineer` or `rpg-qa-lead` provides independent review.
- **Combat/AI:** `rpg-combat-designer` and `rpg-ai-behavior-designer` own design, `rpg-ai-programmer` and `rpg-combat-programmer` own implementation. Split by stable data and ABI boundaries, not by enthusiasm or headcount.
- **World:** `rpg-world-designer` leads geography/gameplay layout, narrative/world art/technical art provide their actual discipline outputs, streaming engineer implements verified source tasks.
- **Content in NomadMain:** narrative/design author content spec; gameplay data designer owns data; C# or mod-specific implementation is assigned to the workstream `mod_gameplay` when appropriate. Preserve owner-approved schema.

## Interdepartmental handshake

1. Each parent request has one **accountable head**. Each child has exactly one home department, one primary author and one artifact owner. A shared parent does not imply shared write ownership.
2. Record `NEEDS_FROM`, `PROVIDES`, exact interface/asset/revision, dependency Issue IDs, status and acceptance gate before transferring work.
3. Heads may coordinate peers by creating cross-department child issues, commenting on verified parent Issues and delivering documented handoffs. Heads do not direct unapproved source writes or alter another department's canon.
4. Distinct disciplines may reason in parallel only after inputs/interfaces are frozen; iterate serially where later work constrains earlier work. Stop/fence competing writes to one artifact.
5. A conflict about **scope/priorities** goes to Studio Production Director; a **gameplay/design tradeoff** to Design/Game Director; **canon** to Narrative head for a proposal then owner; **architecture/ABI** to Technical Director/Architecture Enforcer then owner; **creative vision or final merges** to owner. No AI head can grant owner-only authority.
6. A QA reviewer cannot review their own output. The accountable head may synthesize work, but independent QA gates still apply.

## Brief and response contract

Head briefing must expose the requested outcome, accepted facts and provenance, assigned roles, ordered tasks, branch/interface boundaries, review criteria, and owner decisions. Head response must include deliverable, verified links, department status and ledger event confirmation (or `LEDGER_WRITE_PENDING`). Do not dump internal reasoning; provide the decisions, evidence and specific conclusions.
