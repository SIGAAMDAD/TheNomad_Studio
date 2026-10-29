# WG-H — Role registry, specialist skills, manual launch prompts

**Contract:** Frozen `CONTRACT/REGISTRY/1`, `CONTRACT/LAUNCH/1`, `CONTRACT/ORG/1`; facade `nomad_studio.registry.api.build_component(deps: Mapping[str, object]) -> Component`. Pure Python standard library. No sister implementations imported. No automatic model launch.

## Verified catalogue data and version boundaries

`data/base_roster.json` is a pinned, clean data extraction of **174 canonical role IDs** and their exact department homes from the installed `rpg-studio-all-roles` skill files `references/roles_manifest.json` (research date 2026-10-26) and `references/departments.v4.json`. Display titles in the local snapshot are derived from IDs, not asserted as verbatim titles from the donor manifest. Exact role IDs and skill URI references are preserved. Its SHA-256 is `905e84bad5c6fb19133702fc5c6d53b8f497667e0ddf356624f1d5215ad263d9` (snapshot version `studio-v4-2026-10-26`).

`data/fmod_overlay.json` is the owner Drive `FMOD_ROLES_MANIFEST.json` from file ID `1qa5fZ9aZowZ8PIalhYWEATSQdfZFOP4P`, retrieved 2026-10-28. It defines **19 specialist identities and one router**, in the same existing 12 departments. File SHA-256 `a396ffe2e4511106b59f9933151b0a25c0e4c14db1079539416f0c8376da2607`; overlay version `fmod-family-v1-2026-10-28`. Each FMOD role preserves the manifest's standalone ZIP `sha256`, which is **archive** provenance and is distinct from the installed skill content digest. The original ZIP bytes were **not** independently fetched/verified in this workgroup, and manifest provenance is not a claim of installation or runtime activation.

Startup fails if either pinned local JSON file changes, counts change, canonical department head mappings break, duplicate role IDs appear, FMOD assignments leave the three existing homes, or FMOD reporting heads mismatch.

| Department | Head role |
|---|---|
| production | rpg-lead-producer |
| design | rpg-design-director |
| narrative | rpg-narrative-director |
| engineering | rpg-lead-engineer |
| visual_art | rpg-art-director |
| tech_art | rpg-technical-art-director |
| animation | rpg-animation-director |
| audio | rpg-audio-director |
| ux | rpg-ux-director |
| qa | rpg-qa-lead |
| publishing | rpg-release-manager |
| studio_ops | rpg-studio-operations-manager |

## Query/command operations

Each request uses the frozen `QueryEnvelope` (`NOMAD/QUERY/1`) or `CommandEnvelope` (`NOMAD/CMD/1`), exact `domain='registry'`, `operation='registry.<verb>'`. A `VerifiedIdentity` from WG-B is mandatory, unexpired, and needs `registry:read` for read operations or `registry:prompt` for prompt creation. `registry:prompt` is read-only text assembly: **not** ticket issue, job claim, registration, start/stop command, or actual model launch. No registry operation mutates Studio authority; `execute` is a read-only compatibility dispatch with the frozen command schema and nonempty idempotency key.

| Operation | Exact payload keys | Result | External seam |
|---|---|---|---|
| `registry.list_roles` | optional `department`, `overlay` (`base`/`fmod`/`all`), `role_id` | sorted records including URI, version, provenance, `count`, `next_cursor`; fixed limit 1–100 | none |
| `registry.resolve_skill` | `role_id` | verified single-skill URI, version, `sha256`, bytes count and provenance | `skill_pins`, `skill_loader` |
| `registry.prompt` | required `role_id`,`mode` (`START`/`RESUME`),`job_scope`; optional `checkpoint_ref`,`reasoning`,`wave` | deterministic manual-chat prompt string, sha256, public worker ID and UI effort `NOT_OBSERVED` | `slot_authorizer`, selected skill resolution, owner effort scope |
| `registry.validate` | `{ "action": "catalog" }` | counts (174/19+1/12), head map, source digests | none |
| `registry.validate` | `{ "action": "role", "role_id": "..." }` | home and department head | none |
| `registry.validate` | `{ "action": "skill", "role_id": "..." }` | same verified lazy skill resolution | selected skill resolver |
| `registry.validate` | `{ "action": "worker" }` with `QueryEnvelope.worker_id` | distinct `registered`, `active_shift`, `chat_attempt`, `actual_ui_effort` statuses | `fleet_observer`; missing => `UNKNOWN` |

Operations are closed-field; extra payload keys reject with `INVALID_SCHEMA`. Every catalogue query is bounded; `list_roles` cursors are decimal offsets into deterministic sorted snapshot results. A new catalogue deployment can invalidate prior cursors, so clients must restart pagination upon version change. Role snapshots are immutable for a component lifetime.

## Injected ports (integration contracts)

* `skill_pins`: trusted immutable-at-construction mapping `role_id -> {"uri": exact URI, "version": exact catalog skill version, "sha256": hex digest of **actual content bytes**, "archive_sha256": only for FMOD roles}`. These are operator-published approved version pins, never pulled from worker payload. **No default fabricated digests** are bundled for the 174 role manuals; operator must enumerate verified content and pin it before any skill resolution or launch prompt. Missing pins fail closed.
* `skill_loader(uri: str) -> Mapping`: trusted connector/skill material adapter, returning exact `{uri, version, content: bytes|str, archive_sha256?}`. The component hashes real content and compares to the pinned `sha256`. For FMOD also compare separately against the role's pinned archive digest. Load **one selected role** per operation, not the full role library. No manual content is emitted in `resolve_skill` output, and no eager role-wide material loads occur.
* `slot_authorizer(identity, worker_id, role_id, job_scope) -> bool`: **WG-B/WG-G/WG-E/WG-O composed trusted authorization**. Must verify registered slot, role assignment, owner approval, current slot/assignment/control epoch, scope digest, active worker capability and job grant before returning the literal `True`. Any absent authorization => `STALE_ASSIGNMENT`, missing port => `NOT_CONFIGURED`.
* `fleet_observer(worker_id) -> Mapping`: trusted read-only authoritative projection from WG-G registered slots, WG-D active shift state, and WG-P authenticated host/connector observation. Expected separate values: `registered` + `registration_receipt`, `active_shift` + `shift_receipt`, and `chat_attempt` + `host_attempt_receipt`. Only positive booleans with independent nonempty evidence refs return `OBSERVED`; else `NOT_OBSERVED`. A prompt, user claim or estimated model UI effort must never populate the host attempt receipt. This module **does not** write or invent observational events.
* `now_us() -> int`: trusted service clock; absent falls back to Python wall clock. Issued `VerifiedIdentity.expires_at_us` is checked against it. Principal validity and current authority epoch must already be established by WG-B; this registry cannot authenticate transport or independently read revocation state.

The composition root supplies ports through the frozen facade. No unauthorized cross-module import or alternate database.

## Prompt contract

`registry.prompt` requires `QueryEnvelope.worker_id` (public ID), role, job scope with **exact** keys `job_id`, `assignment_epoch` (>0), `approved_paths` (nonempty, <=100), `scope_sha256` (64 lowercase hex characters), `source_ref` (nonempty). This module does not reinterpret/replace the upstream approved-scope digest. It includes an explicit `{{ONE-TIME-LAUNCH-REFERENCE}}` marker for the owner console to populate **outside the template**, using WG-B one-use ticket creation. It never issues a ticket or stores credentials. `RESUME` requires a nonempty checkpoint reference and both modes print response policy for multi-job shifts, trusted STOP/DRAIN/YIELD checks, real testing and evidence.

Every persistent worker requires at least `high`; `medium` is always denied. The frozen **greenfield one-wave** prompt always requires exactly `high`, even if an owner grant exists. For other later waves, `extra_high` requires authenticated `owner:reasoning` scope and explicit request; actual ChatGPT UI effort is always `NOT_OBSERVED` absent independent UI attestation, and the registry does not invent it. Source payload is JSON-encoded to preserve line structure, with newlines prohibited in IDs/path strings. Prompt output is hashed with SHA-256 on exact UTF-8 bytes. The same complete input + same skill bytes/pin always yields byte-identical output.

## Error and audit behavior

`DENIED INVALID_SCHEMA` for malformed requests/unknown fields, `DENIED INVALID_SCOPE` for unauthorized role/effort, `DENIED OWNER_APPROVAL_REQUIRED` for unapproved extra-high, `DENIED EXPIRED_GRANT`, `DENIED STALE_ASSIGNMENT` for slot mismatch. `BLOCKED SKILL_PIN_MISSING`, `BLOCKED NOT_CONFIGURED` for required absent adapters, `DENIED SKILL_PIN_INVALID`, `DENIED SKILL_VERSION_MISMATCH`, `DENIED SKILL_DIGEST_MISMATCH`, `DENIED SKILL_ARCHIVE_MISMATCH`; `BLOCKED SKILL_FETCH_FAILED` on tool outage; `BLOCKED PROVIDER_UNKNOWN` on exceptions. The registry has **no mutations**, so no SQL migrations and no registry-owned audit events; actual ticket/slot/shift/work events and receipts are owned and persisted by WG-B/G/D/E through WG-A. Failure never silently picks an adjacent role or stale material.

## Runtime limits and evidence

194 immutable catalog identities, maximum 100 roles per page, maximum 100 approved paths per prompt, maximum 512 characters per path, 128-character worker and job IDs, one skill material fetched on demand per skill or prompt request. Actual 200/500/800 concurrent workers, activated ChatGPT sessions, real relay, connector admission and authentic UI effort are **NOT_RUN** here. Local tests use deterministic port fakes and fault injection; they establish implementation behavior only.

## Merge instructions

Copy the three WG-H-owned source roots from the ZIP into the frozen seed / integration worktree without touching frozen `contracts/`, `pyproject.toml`, `app.py` or other workgroups. Register `build_component` in the integrator composition root with the trusted ports above. Before enabling live prompts, materialize and verify each selected role's installed/manual bytes, publish approved digest pins, configure WG-B launch-ticket + WG-O prompt assembly and WG-G/D/E slot/status ports; keep gateway privileges independent. WG-I must independently review the exact integrated source and the actual connector/host tests.
