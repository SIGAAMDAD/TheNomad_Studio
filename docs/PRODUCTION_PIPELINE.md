# The Nomad Studio V3 — Production Pipeline

## Authority

- GitHub Issues / Projects V2: feature intake, owner scope, priority and final approval.
- `SIGAAMDAD/TheNomad_Studio:studio/runtime`: one authoritative machine job queue, feature states, assignments and immutable receipts.
- Original Google Drive documents/revisions: accepted game design, narrative, art references and canon.
- Actual repositories and exact Git SHAs: source proof for game, framework and Terrain3D.
- S00 Production Director supervises the specialist worker contract; S01–S09 execute it. The catalog of 174 professions is loaded *on demand*, not spun up as 174 simultaneous tasks.

## From owner request to execution

1. You create an Issue in the Studio repository or an Issue card on the connected GitHub Project. You can also invoke the packaged `nomad-studio-intake` Skill from ChatGPT to create the same issue, check for duplicates and prepare an exact feature plan. Board drafts are discoverable but cannot execute until converted to GitHub Issues.
2. The GitHub Action at minutes :07/:22/:37/:52 (every 15 minutes, not a ChatGPT run) scans Issues and, if separately configured, Project V2 item fields. It writes one `FEATURE-N` record into `studio/features.json`. The scan is idempotent.
3. S00 scopes the request. You apply `studio:approved` to the Issue as its trusted owner. The scanner validates the actual GitHub label-change event actor before authorizing admission. A card being in the Ready column alone is **not** approval.
4. S00 creates `studio/specs/FEATURE-N.json` in the runtime branch. Each child has exact role/manual, discipline, bounded brief, dependencies, unique owned/protected paths, acceptance, and a source SHA if it involves code. The deterministic controller creates one independent QA child per producer, automatically rewiring intra-feature dependencies to require **verified QA** before downstream production.
5. Admission refuses malformed source pins, duplicate job IDs and missing scoped work. Code/integration jobs begin BLOCKED until owner-approved source freeze and toolchain/CI proof. No fabricated SHA is allowed.
6. The dispatcher selects eligible READY jobs and writes an exclusive slot/epoch reservation. The current bootstrap permits at most two concurrent mutable writers and one independent reviewer. Idle workers remain paused. The scheduled Chat worker wakes independently and reads only its slot/job/manual.
7. The worker produces a **real deliverable**: Git branch/commit, Drive revision, actual art/animation/audio file or other supported artifact. Each run writes an immutable CHECKPOINTED, BLOCKED or SUBMITTED JSON receipt with exact links, tests actually run and next action. A checkpoint preserves ownership; catchers require safe fencing.
8. After submission, QA reads the exact input revision, produces a separate report and a structured PASS/REWORK receipt. The controller checks that the reviewer is independent and that `review_result.subject_ref` equals the producer artifact.
9. For a GitHub artifact, the controller checks the actual commit/path through the GitHub API. For a Drive revision, S00 must perform real Drive readback and record exact `readback_evidence`. Without proof, the job remains REVIEW; no worker can claim VERIFIED from chat text alone.
10. A REWORK verdict reopens the same producer job with the recorded findings, keeps previous checkpoint history, and requires QA of the new revision. PASS with readbacks marks a creative deliverable VERIFIED. Code additionally requires final source SHA, matching Drive report, and passing CI at that same SHA.
11. Only after **all** required producer and QA child jobs are VERIFIED does the parent enter READY_FOR_OWNER. You apply `studio:accepted` as a trusted owner. The feature becomes OWNER_ACCEPTED.
12. No protected source merge, canon publication, asset release or licensing acceptance is automatic. Authorized integration/publication is performed and independently read back; its immutable `finalization_evidence` plus trusted `studio:finalized` owner label advances to FINALIZED. The optional project mirror updates its status only with a Projects-scoped token.

## Departments

| Department | Actual deliverables |
| --- | --- |
| Gameplay and systems design | Combat/GOAP/quest/level rules, fixtures, balance and game-state contracts |
| Narrative and worldbuilding | Canon proposals, lore, dialogue, branching scenes, stable IDs |
| Visual art, VFX, UI | Concept art, real meshes/materials/effects, UI artifacts if the worker has an enabled creation tool |
| Animation and cinematic | Actual rigs/clips/scenes or explicit capability blockers |
| Audio | Music/SFX/voices, FMOD integrations or explicit capability blockers |
| Engineering | C++17 GDExtension, C#, GDScript, engine/terrain patches and test builds |
| Infrastructure | AST and static analysis, source tracking, reproducible builds and CI |
| Independent QA | Art/narrative/design coherence, technical tests, integration and performance findings |

A role name is not proof that the worker has a GPU, audio authoring software, 3D asset tools or an installed compiler. Tasks lacking a real execution capability must return BLOCKED rather than a stand-in description.

## Schedule and cost

- **Every 15 minutes:** GitHub repository issue/optional Project scan and deterministic reconciliation.
- **Every hour:** S00 Director :00 CT; S01–S09 staggered at :06, :12, :18, :24, :30, :36, :42, :48 and :54 respectively. These are targets, not guarantees of exact start or completion.
- **Daily:** read-only department metrics published as GitHub Action artifacts. Any email is a separate authorized Director step.
- Ordinary production uses standard Instant scheduled Chat with connected GitHub/Drive tools. Stronger reasoning is concentrated on QA and high-risk integration where enabled; the controller cannot change a task's model via its prompt. Deterministic validation uses zero OpenAI API tokens.

## Source of truth and no-duplicate rule

The board is the public work index *within the workspace*, not a competing runtime queue. One parent Issue number corresponds to `FEATURE-N`; child jobs live under that feature ID. Changes to an Issue after scoping are flagged, not blindly applied to in-progress work. A repeated request must link its existing Issue/PR, not start a second feature.

Existing Sortior work from the Drive queue requires **a verified, non-destructive migration** of its exact revision history and existing QA findings. This controller does not silently import or overwrite those legacy documents.

## Live acceptance proof

Before declaring the Studio autonomously operational, require an actual board Issue readback, owner-labeled approval, feature spec admission, saved producer artifact, immutable receipt, distinct reviewer artifact, QA PASS or automatic REWORK, and owner acceptance/finalization. A green controller unit test is *not* proof of Godot compilation, a real asset, or a scheduled GPT worker run.

See `docs/OWNER_SETUP.md` and `studio/WORKER_CONTRACT.md` for activation and per-worker evidence formats.

## Optional Drive-origin request path

integrations/apps-script/DriveIntake.gs provides a separate 15-minute owner-controlled folder scanner. A Drive file named [STUDIO] ... becomes a deduplicated GitHub Issue with its original link, but remains INBOX until the trusted owner approves it. This script is not active without user installation and GitHub credentials; it does not poll all of Drive by default.
