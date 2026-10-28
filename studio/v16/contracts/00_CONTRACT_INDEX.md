# The Nomad Studio V1.6 - frozen contract candidate, WG-A

**Status:** `IMPLEMENTED_LOCAL / PENDING_OWNER_ARCHITECT_APPROVAL / PENDING_INDEPENDENT_QA`.
**Compatibility baseline:** supplied `TheNomad_Studio-main.zip`, reported source main `8d0b9002e1fedababe72fceffe2a8d6f4bbb3fcd`, archive SHA256 `e46d625a08e49c2f3d003d1c440dabb41863f2c5e66b503d3538694abe9e89ea`. The reported ref is archival metadata, not an observed live remote HEAD. No V1 code or historical receipts are changed.

## Canonical schemas - revision 1

All JSON Schema documents in `studio/v16/schemas/` use draft 2020-12; each has an immutable `urn:nomad:studio:v16:<name>:1` `$id`. Resolve the shared common definitions from the bundled `common.v1.schema.json` in an **offline** registry. Do not treat a URI as a web endpoint. Every executable object has a `schema_id` constant; unsupported versions are `UNKNOWN_SCHEMA_VERSION` (no best-effort coercion). Unknown keys fail in every authority-carrying object. Do not promote a parsed JSON object to a capability, verified identity, accepted QA, or observed source fact.

| Interface | Schema(s) | Authoritative producer | Consumer and meaning |
|---|---|---|---|
| `AUTH-V16` | `auth-grant`, `auth-evaluate`, `auth-decision` | Independently authenticated owner/authority + gateway | WG-D, WG-E, WG-B, WG-F; **gateway** validates the grant and produces a decision, not a chat |
| `DB-CONTRACT` | `db-command`, `db-result`, `db-event` | WG-C transaction implementation | WG-F/G; typed atomic journal effects and ID-fenced attempts, no model launch |
| `RUN-V16` | `run-startup`, `run-receipt`, `run-resume` | Manually opened High chat; external verifiers separately | WG-B/F/H; access preflight, observed execution, compact restart |
| `PUBLISH-V16` | `publish-intent`, `publish-readback` | WG-D isolated gateway, WG-G provider adapters | Exact source or Drive CAS, independent QA, durable readback and no inline merge |
| Retired routes | `contracts/legacy-retirement.v1.json` | WG-A static migration specification | WG-B/G; historical W01-W05 labels become **archive only**; manual specialties are selected by an approved human job |

### Compatibility guarantees

* `job_id`, `worker_id`, `attempt_id`, `assignment_epoch`, `lease_generation`, `authority_revision` and immutable source SHA continue V1/V2 provenance. Distinguish a stable worker from each response attempt. A newer attempt never erases the former attempt or its immutable event.
* V1 `STARTED`, `HEARTBEAT`, `YIELDED`, `SUBMITTED`, `STOPPED` remain observations; new contract `CHECKPOINTED` maps to legacy `CHECKPOINT` without rewriting old data. `elapsed_observed_seconds` is observed time, never an inferred full session duration. Preserve V1 event IDs and transport IDs when adapting; create a fresh V1.6 event with a stored link and deterministic transform version/hash rather than rewriting evidence.
* No `scheduled`, `github_triggered_chat`, ChatGPT Work, model executor launch, or PR-labelled runnable AI task exists in positive contract vocabulary. Ordinary deterministic GitHub Actions may validate, queue archival events, or report.
* GitHub supplies approved scope, commit SHA and source/CI evidence; Google Drive supplies document revision and exported digest. Before owner-approved cutover, existing Studio runtime branches/Drive queues remain authoritative for **their existing respective flows**. After cutover, PostgreSQL owns runtime jobs/leases/events. Neither these schemas nor SQL deployment changes source, canon, business/spending, or oversight authority.

### Implementation ordering

WG-A must receive owner/architect approval and exact-subject independent QA before other groups **accept** the schemas. B/C/D/E/H may use local copies for parallel review, but no release changes owner policy by reading this directory. Contract breaking changes require a new schema ID/version and explicit migration, not silent optional-field additions.

### Acceptance artifacts

`tests/v16/contracts/test_contracts.py` validates all schemas and example/negative vectors against a fully offline registry, checks ownership/denial semantics through a deliberately **test-only** reference policy, and detects accidental automatic-chat dispatch. `fixtures/` is deterministic, contains zero credentials, and provides independently reproducible positive and negative inputs. A JSON Schema pass alone is only a parse pass. WG-D and WG-C must prove real enforcement and transaction behavior independently.
