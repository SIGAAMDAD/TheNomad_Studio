# WG-AT — Owner artifact inspector and exact decision queue

**P1 R10, S136–S138.** Standalone Studio extension built against the frozen A–S `contracts/v1.py` with no changes to frozen source. This is a read-only workspace projection and an **owner-intent forwarding facade**; a submitted intent is not a signed owner decision, a policy mutation, a provider effect, or a deployment receipt.

## API and installation boundary

`nomad_studio.creative_authority.console.api.build_component(deps: Mapping[str, object]) -> OwnerArtifactConsole`, with the original frozen `Component` methods:

- `query(QueryEnvelope(version="NOMAD/QUERY/1", domain="creative", operation="creative.inspect", payload={"project_id": ..., "view": ...}), VerifiedIdentity)`; `view` is `projects` (portfolio listing without project ID), `dashboard`, `tree`, `artifact` (requires `artifact_id`), `queue`, `compare` (requires `decision_id`), or `variants` (requires two distinct decision IDs). No read performs a write.
- `execute(CommandEnvelope(version="NOMAD/CMD/1", domain="creative", operation="creative.request_owner_decision" | "creative.approve_exact" | "creative.revert", ...), VerifiedIdentity)`; every write-shaped request becomes an immutable **intent submitted to an injected trusted gateway**, never a direct SQLite/repository/Drive call.

The original gateway does **not** register these operations in P1. P2 must add explicit authenticated/authorized routing if and when owner approval and integration validation permit it. The caller must supply a `VerifiedIdentity` established independently by the existing gateway, not client form state. The module checks owner read/write scopes, expiration and authority epoch but does not mint credentials or grant owner privileges.

### Injected ports (no sibling imports)

- `read_port.projects(identity: VerifiedIdentity) -> tuple[ApprovedProject, ...]`: independently authorized approved/paused/blocked project portfolio; unconfigured output is `UNSUPPORTED`.
- `read_port.workspace(project_id: str, identity: VerifiedIdentity) -> WorkspaceSnapshot`: independent fresh authoritative readback of approved projects, policy revision and digest, authority epoch, queue revision, revision-specific artifact sources and work/cost/blocked gate summaries. A missing, mismatched, cyclic or invalid snapshot fails closed.
- `read_port.comparison(project_id: str, decision_id: str, identity: VerifiedIdentity) -> ExactComparison`: independent source-bound diff, matching exact candidate digest, source base SHA, policy revision/digest, property before/after digests, and exact affected path set. The read-only compare can show `NOT_RUN` and `UNKNOWN`; exact approval requires `VERIFIED` and all impacts verified.
- `gateway.execute(command: CommandEnvelope, identity: VerifiedIdentity) -> Outcome`: injected owner-authority gateway, which must independently verify owner credentials, revision CAS, idempotency, policy/assignment/source and provider readback. **A gateway ACK is reported only as `OWNER_INTENT_SUBMITTED`, `effective_decision_verified=false`, `provider_readback=NOT_RUN`.** The console never claims to apply a lock, accept a candidate or revert a source.
- Optional `clock_us() -> int` for deterministic tests.

Without a read or gateway provider: `UNKNOWN/UNSUPPORTED` with `external_status=NOT_RUN`. Provider exceptions are `UNKNOWN/PROVIDER_UNKNOWN` or `UNKNOWN/GATEWAY_UNKNOWN`. No fallback local mutation or fabricated success is attempted.

## Project-neutral view model

`WorkspaceSnapshot` includes exact project ID, `approved`, policy revision/digest, authority epoch, queue revision, source manifest digest, milestone, active work, blocked gates, currency/minor-unit cost, artifacts, decisions and evidence status. Dashboard projections strip unapproved provider fields from work/gate summaries; private case data is never intentionally rendered. Any claimed runtime observation must have been supplied by the external source; `NOT_RUN` remains visible.

`ArtifactRecord` forms a configured **project → artifact → property** tree (`kind` and capability names are arbitrary strings). Parent references and cycles are validated. A `PropertyRecord` distinguishes `MAPPED`, `UNMAPPED` and `UNSUPPORTED`; missing precise binding yields `REQUEST_MAPPING`, not an editable pretend property. Providers implement project/skill inspectors outside this package. The included tests exercise CAD parts and editorial documents with the same kernel; there is no engine or domain editor inside Studio Python.

`DecisionRecord` includes feature, exact candidate, project, artifact/property, worker actor/job/assignment epoch, exact source base, current policy, before/after property digests, typed changed paths, risk class, evidence and status. The queue deterministically ranks irreversible, security, release block, critical path, creative and routine decisions. A feature+candidate group exposes **each decision and its diff**; it is **never a group approval** and never drops a high-risk item. `ExactComparison` is re-read from the inspection port and compared against the selected decision. A two-variant view fetches independently exact comparisons for both candidates in the same feature and never issues a winner/approval. No approving a different source hash, stale policy, hidden path or unknown impact.

## Owner intent formats

All three command forms require `project_id`, `expected_policy_revision`, `expected_policy_digest`, `expected_authority_epoch`, `expected_queue_revision`, `decision_id` and a valid `CommandEnvelope` request ID/idempotency key. **The authoritative workspace snapshot and decision determine** actor principal, job, worker actor reference, assignment epoch, candidate digest, source SHA, current policy and allowed changed path set; client payloads cannot rewrite them.

- `creative.request_owner_decision`: `action=LOCK | REVIEW | BOUND | DELEGATE`, `artifact_id`, `expected_artifact_revision`, optional `property_key`; `BOUND`/`DELEGATE` require a constrained typed `condition` compatible with the reference rule condition fields. `LOCK`/`REVIEW` reject unsolicited conditions. This produces an expected-revision request, not a local policy entry.
- `creative.approve_exact`: `exact_candidate_digest` must equal the *current* queued candidate and the independent comparison must be complete and verified. The forwarded intent includes the exact digest, item, source, policy, impact list and evidence reference; never a free-form “accept all.”
- `creative.revert`: `artifact_id`, `target_revision`, `target_digest` must identify a prior recorded `ArtifactVersion` with an exact source SHA, not the current revision. The forwarded intent binds current and target exact digests; no source rollback is executed locally.

The UI can show submitted requests, then retrieve new authoritative readback in a subsequent query. A live effective-policy change, issued owner-decision cryptographic receipt, provider CAS or release acceptance can only be reported by trusted integrated systems, not inferred from this console's return code. The `owner:creative:read`, `owner:creative:write` scope labels are P1 integration proposals; they are not automatically admitted roles.

## Contract and safety notes

- Source authority precedence: original frozen `contracts/v1.py` → matching HR V13 reference DTOs → candidate R10 extensions (not authoritative). Local typed DTOs are intentionally not a new frozen schema; P2 must map them to source snapshots, registry, owner policy, impact inspector, authentication and audit receipts.
- `creative.request_owner_decision`, `creative.approve_exact`, `creative.inspect`, `creative.revert` are proposed extension operation names only. `LOCK`, `REVIEW`, `BOUND`, `DELEGATE`, `COMPARE`, `REVERT` are UI intents/views, not new privileged dispatch operations.
- Stale UI session/replay after revision change, wrong authority epoch, malformed conditional constraint, unapproved project, unknown property, mismatched source/hashes, gateway failure and absent port are denied or marked unknown before any gateway dispatch.
- The authoritative gateway must enforce *cross-process* replay protection, idempotency uniqueness, exact-owner signing, policy CAS, source/decision binding and separate provider permissions. Local facade validation is defense in depth, not a substitute for that server.
- The module has no network client, SQL connector, Godot/FMOD interface, private HR case access, or game implementation. Queries and dry-runs have no implicit external write.

## Integration change requests (P2 ownership)

1. **ICR-AT-01**: Wire trusted project membership and creative read/intent scopes through the existing gateway; reject unverified identities and enforce authoritative owner-only signature/receipt and idempotency. Do not register mutations until the independent gateway owner/auth tests pass.
2. **ICR-AT-02**: Map AR policy snapshots, AS independent impacts, AV generic bindings, AH source manifests, O owner workspace and B budgets into authenticated read-only `WorkspaceSnapshot`/`ExactComparison` adapters. Require exact policy+artifact revisions, known source SHA and revision-specific path observations. Missing providers return `UNSUPPORTED` / `NOT_RUN`.
3. **ICR-AT-03**: Integrate separate immutable decision event/audit readbacks and exact revert/proposal gateway commands. Include provider permission deny, changed-source replay, revoked grant, stale UI, cross-project read, concurrent CAS and original regression tests in P3/P4.
4. **ICR-AT-04**: Provide a real front-end/workspace shell with keyboard navigation, search, filters and viewport/editor deep links via external inspector plugins. This module supplies serializable view models; it is not a shipped web GUI.

## Local verification vs external acceptance

The isolated tests are genuine `unittest` tests with deterministic injected ports; they cannot establish real owner identity, production access control, source-provider readback or live frontend behavior. No private repository was accessed or mutated. Real GitHub/Drive provider denial, host skill routing, P2 registration, end-to-end approvals, source/build QA and P4 release are **NOT_RUN / NOT_VERIFIED** in this P1 artifact.
