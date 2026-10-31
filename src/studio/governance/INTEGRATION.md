# WG-A Governance — Canon, owner decisions, policy, grants

## Source boundary

Module `studio.governance.service.Governance` owns the 14 WG-A operator commands declared by frozen `CONTRACTS/ports.pyi`: `listBibles`, `createBible`, `stageBible`, `diffBible`, `publishBible`, `getCanon`, `requestDecision`, `resolveDecision`, `listDecisions`, `getProjectPolicy`, `setProjectPolicy`, `listProjectGrants`, `grantProjectAccess`, `revokeProjectAccess`. Two additional internal operations provide exact publication effect preparation and pinned context compilation. These must be called through authenticated WG-J/J+M API plumbing, not from worker relay claims. The approval use and publish state transition must be written atomically by WG-C.

This worker deliberately does **not** create `CONTRACTS/`, `schema/`, `src/studio/storage/`, `src/studio/integrations/`, or `src/studio/api/` code. `memory.py` is a deterministic test reference, not an APSW production implementation. There are no model API clients, background browser sessions, CI agents, shell execution, or GitHub write paths here.

## WG-C persistence mapping (necessary for runtime integration)

C must expose an atomic project-scoped `GovernanceTransactions.project(project_id)` domain transaction. It may use its actual frozen `ScopedTransaction.begin/execute_named/select_named/commit/rollback` interface to build a project-scoped projection with CAS. Treat the returned `ProjectState` as one transaction-owned **business projection**, not as permission for worker SQL. Important projection-to-table mapping:

| ProjectState field | Frozen SQL state | Notes |
| --- | --- | --- |
| `bibles` | `bibles` | Composite project/bible keys |
| `intents` | `bible_publication_intents` | Staged/verified/committed states; blob FK |
| `editions` | `bible_editions`, `bible_clauses`, `canon_facts` | Immutable edition and published-clause/fact material; reconstruct manifest from verified content-addressed bytes |
| `active` | `active_bible_editions` | One active project-level Design/Technical pair; delete superseded same-kind active pointer in the same transaction |
| `snapshots` | `canon_snapshots` | Immutable pair plus policy digest and manifest digest |
| `proposals` | `decision_proposals` | Revision, exact subject/effect/snapshot, pending/approved/rejected; on revoke the proposal remains approved |
| `decisions` | `owner_decisions` | Verified human-only identity from J; `state=revoked` via permitted update |
| `consumed_decisions`, `effects` | `approval_consumptions`, `effect_intents` | Insert effect intent and approval consumption before immutable Bible edition; enforce exact matching and uniqueness |
| `policy` | `project_policies`, `project_policy_rules` | CAS revision, replace rule set under new policy id and active row |
| `grants` | `project_grants` | Require actual principal FK, never trust model-supplied owner identity |
| `requests` | `idempotency` | Project/owner/request scoped, request body digest and finished result for replay |
| `events` | `audit_events` and C's durable effect/outbox mechanism | Persist transactionally; read calls must emit no new event |

**Performance contract:** WG-C must back project access with bounded named lookups and dirty-row mutation rather than reloading all historical editions, decisions, or project events. A lazy, transaction-backed projection or per-command optimized repository is appropriate; do not treat the reference adapter's deep-copy behavior as production guidance. SQL trigger gates and canonical SQL constraints are still mandatory. Tables owned by other domains (`blobs`, `project_blobs`, `principals`, other subject records) are read through bounded scoped services, never directly mutated by WG-A.

## WG-G blob readback and document integration

`VerifiedBlobSource.read_verified(project_id, digest, purpose)` must verify **physical bytes**, exact SHA-256, `blobs.durability='verified'`, the project/purpose association (`project_blobs`), and the configured provider's successful readback. Stage content with purpose `bible-content` and manifest with `bible-manifest`. A user or browser assertion that a digest was uploaded does not count. A mutable Drive draft or a remote timeout does not publish an edition or become the active authority. The actual Drive authoring/sync and durable blob upload code belongs to G; live provider tests are not represented by memory fixtures.

`stageBible` assumes G/C has already uploaded and verified the two blob digests. It stores a verified intent. `publishBible` re-reads both verified blobs and consumes the exact owner approval before inserting the edition. Active pointers and Canon snapshot must change atomically. The standalone `publishBible` response contains `snapshot_id=''` until **both** kinds have a published active edition, because the frozen `canon_snapshots` table requires both. `getCanon` rejects an incomplete pair. WG-M/J must handle this existing frozen wire-response semantic explicitly; replacing the empty value with a fictitious snapshot is prohibited.

Manifest format is UTF-8 JSON with `format: nomad.bible.manifest.v1`, `clauses` array and `facts` array. Each clause requires unique `clause_key`, `classification`, literal `text`, `scope`, `source` (`document_id`, `revision`, integer `start`, integer `end`) and may include `status` and impact IDs. Facts require unique `fact_id`, `subject_key`, `predicate`, `value`, `evidence.source` and optional explicit status. Extracted clauses default to `proposed`; facts default to `draft`. Only explicitly `verified` entries appear in compiled context. Publishing immutable bytes does **not** rewrite proposed content to verified.

## WG-J owner proof and authorization

`TrustedIdentity.authenticate(actor)` must validate **server-bound** credentials, subject and issuer, returning a trusted `Principal` with human/service/worker type. `allowed_project_ids` only narrows scope; it never grants access. Additional grants are read per-project. Owner decisions, project policies and grant changes require a verified `human` principal with the `owner` project grant. Owner flags, browser role strings, GitHub commit-author strings and purported owner fields in request JSON are ignored.

Owner proposal approval/rejection uses the exact effect digest and proposal revision. A consumed owner decision cannot be used for a different effect or revoked after its effect. `authorize_effect` can be used by other trusted domain services only inside the **same physical C transaction** as effect intent creation, and the external effect itself remains with its separately authorized executor. A caller must never execute an effect solely because `authorize_effect` returned a value in an isolated memory fixture.

`SubjectCatalog` is the read-only cross-domain subject existence check for `feature`, `task`, `cabal`, `artifact`, `release`, and J principal records. `ImpactCatalog` supplies D's pinned task-clause references. If unavailable, a guessed nonlocal subject fails closed; other WG-A functionality continues.

## Pending checks (NOT_RUN)

- WG-C APSW scoped named-command integration, real transaction/CAS, FK, trigger, audit outbox and durability tests.
- WG-G real content-addressed blob/Google Drive physical readback and recovery tests.
- WG-J production authenticated principals and spoofing tests with server-bound session credentials.
- WG-D pinned task impact index and WG-M/J HTTP wiring, exact cross-service effect intent semantics.
- WG-L independent Tier B/QA and live Tier C GitHub/browser/Drive pilot.

Local evidence: `python -m unittest discover -s tests/unit/a -p 'test_*.py' -v`, executed against the reference store; do not substitute this for any of the pending checks.
