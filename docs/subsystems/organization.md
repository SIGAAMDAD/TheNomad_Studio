# WG-G — Studio organization, Production Director, and departments

**Contract:** `NOMAD/GREENFIELD/CONTRACT/ORG/1` implemented through frozen `Component` (`NOMAD/CMD/1`, `NOMAD/QUERY/1`). **Owned runtime:** `nomad_studio.organization.api`. **Persistence:** tables prefixed `org_`, exclusively managed through WG-A's `StateStore`. No legacy source, runtime, connectors, or direct SQL connection are used by this module.

## Composition and authoritative boundaries

```python
from nomad_studio.organization.api import build_component
organization = build_component({
    "state": state_store,             # WG-A StateStore; V1 schema initialized
    "owner_principal_id": owner_id,   # trusted owner configuration, NOT an incoming claim
    "trusted_issuer_ids": {issuer_id},# authenticated gateway's actual verified issuers
    "source": source_component,      # optional WG-F source Component query port
    "oversight": oversight_component,# optional WG-N opaque notice verification Component
})
```

The `clock_us` injectable time source exists for deterministic tests; production defaults to trusted host `time.time_ns() // 1000`. `build_component` consumes the single Stage V1 StateStore after its pinned initial schema has been installed on the fresh database. The `org_*` tables are included in the one SQL file packaged into the wheel; there is no per-module migration descriptor. Production must use a server-authenticated, currently valid `VerifiedIdentity` minted by the gateway. The module checks issuer allowlist, expiry, `authenticated_via`, scopes, owner principal equality, live stored leader identities and epochs; it cannot itself independently verify a network transport credential. Gateway must additionally fence revoked `current_authority_epoch` identities; passed `actor_claim`, `worker_id`, and issue comments are never used as authorization.

The owner is **not** a Production Director or department head. Initial roster has 12 vacant department slots plus one vacant director slot. An explicit owner/authorized appointment must populate each. Head role IDs match the frozen architecture:

| Department | Accountable head role |
| --- | --- |
| production | `rpg-lead-producer` |
| design | `rpg-design-director` |
| narrative | `rpg-narrative-director` |
| engineering | `rpg-lead-engineer` |
| visual_art | `rpg-art-director` |
| tech_art | `rpg-technical-art-director` |
| animation | `rpg-animation-director` |
| audio | `rpg-audio-director` |
| ux | `rpg-ux-director` |
| qa | `rpg-qa-lead` |
| publishing | `rpg-release-manager` |
| studio_ops | `rpg-studio-operations-manager` |

The director's required role is `studio-director` (organizational slot), while the final registered role identifier must be verified against WG-H's frozen 174+20 roster during integration. The module does not invent active chat workers or claim role catalog acceptance.

## Command operation/action contract

Every command must have `domain='organization'`, operation exactly as below, `version='NOMAD/CMD/1'`, a unique/valid request ID, a nonempty idempotency key, and the documented **closed** payload fields. Every accepted command writes one domain event and a same-transaction receipt. Replaying the same command with the same key returns its stored receipt; different command data/actor/authority epoch using the key returns `CONFLICT/IDEMPOTENCY_CONFLICT`.

| Operation | Payload action | Required fields besides `action` | Authority and outcome |
| --- | --- | --- | --- |
| `organization.intake` | `register` | `feature_id`, `issue_ref`, `revision_sha256`, `title`, `scope`; optional `director_epoch` | Verified GitHub issue revision via WG-F, then owner or current Production Director. Creates `REGISTERED` feature; **no approval** is inferred. |
| `organization.intake` | `approve` | `feature_id`, `scope_sha256`, `decision_ref` | Only authenticated human owner with `organization.owner`. Pins the exact scope and approved decision reference. |
| `organization.intake` | `escalate` | `escalation_id`, `feature_id`, `category`, `reason`, `department_id`, `leader_epoch` | Owner or current director/head of that department. `category` is `ARCHITECTURE` or `CANON`; stays `PENDING` until owner action. |
| `organization.intake` | `resolve_escalation` | `escalation_id`, `decision_ref` | Owner only; records an explicit resolution. This is a decision record, not an automatic canon write. |
| `organization.decompose` | `create` | `feature_id`, `leader_epoch`, `jobs` | Owner or current director. Feature must already be owner approved. Inserts bounded child jobs and acyclic cross-department dependencies atomically; **does not enqueue the scheduler**. |
| `organization.delegate` | `assign` | `job_id`, `leader_epoch`, `implementer_id`, `reviewer_id` | Current accountable department head, matching epoch. Requires distinct creator, implementer, reviewer, head identities. New assignment or epoch-fenced reassignment. |
| `organization.handoff` | `ack_source` or `ack_target` | `handoff_id`, `leader_epoch` | Source/target department head, respectively. Both sides must ACK with **current** head epochs and principals. |
| `organization.leader_status` | `mark_missing` | `slot_id`, `expected_epoch`, `reason_ref` | Owner or active scoped owner-issued delegation. Increments epoch; sets `MISSING`; does not change or delete any jobs. |
| `organization.leader_status` | `apply_notice` | `notice_ref`, `decision_code`, `evidence_sha256`, `feature_id` | Authenticated `organization.oversight_apply` and WG-N opaque verification. Only `FENCE_FEATURE` supported. Stores **only** decision/ref/digest; never case plaintext. |
| `organization.appoint` | `appoint_director` / `appoint_head` | `slot_id`, `expected_epoch`, `principal_id`, `decision_ref` | Owner directly; or scoped owner-issued authority for `appoint_head`. Compare-and-increment epoch. |
| `organization.appoint` | `appoint_deputy` | Above, optional Boolean `activate` | Owner or scoped delegation. `activate=true` allowed only after a trusted `MISSING` decision; changes status to `ACTING`, increments epoch. `false` nominates without creating an active worker. |
| `organization.appoint` | `grant_authority` | `grant_id`, `principal_id`, `department_id`, `operations`, `expires_at_us` | Owner only; actions restricted to `appoint_head`, `appoint_deputy`, `mark_missing` scoped to a single department, `director`, or explicitly `*`. |
| `organization.appoint` | `revoke_authority` | `grant_id` | Owner only; immediate durable fence for new uses. |

Required identity scopes depend on operation: every mutation requires `organization.write` or `organization.owner`. Leadership requires `organization.director` or `organization.head` **and** matching current principal and epoch; appointment grants require `organization.appoint` when not owner. Reads require `organization.read` or `organization.owner`. Oversight application additionally requires `organization.oversight_apply`.

### Feature and child-job payloads

`issue_ref`: `github:<owner>/<repo>#<positive-issue-number>`. `revision_sha256`: exact lower-case 64-hex external source revision digest. `scope`:

```json
{"paths":["src/game/feature.cpp"],"summary":"Feature summary","criteria":["Observable acceptance condition"],"scope_sha256":"<SHA256>"}
```

`scope_sha256` is SHA256 of compact, sorted-key, ASCII JSON of `{paths,summary,criteria}`, **excluding** the digest field. All paths are relative forward-slash file paths, no `..`, absolute paths, duplicate paths, or backslash paths. Jobs may only use exact paths from the approved feature set. Scope changes require new feature/owner decision; head cannot edit approved feature scope.

Each element of `jobs` is a **closed** object with `job_id`, `department_id`, `role_id`, `paths`, `criteria`, and `depends_on` (list of job IDs in the same feature, including earlier batches). The module rejects missing prerequisites, cycles, duplicates, out-of-scope paths, and plans exceeding `800` jobs / `3200` edges per feature. Max **100** new jobs per command, **100** paths per job, **100** dependencies per job, **50** criteria per job. Cross-department edges automatically create two-sided accountable `org_handoffs`; a head epoch change invalidates previous acknowledgements. Every stored job retains distinct `creator_id`, `implementer_id`, `reviewer_id`, `accountable_head_role`, `assignment_head_id`, and `assignment_head_epoch` fields.

## Query operation/action contract

Queries use `version='NOMAD/QUERY/1'`; caller scopes/issuer are checked. `limit` must be 1–100. `cursor` is exclusive (prior page `next_cursor`). No query mutates authority.

| Operation | Action | Payload | Data |
| --- | --- | --- | --- |
| `organization.leader_status` | `roster` | none | 12 canonical heads, director, owner ID, current appointments/status/epochs. |
| `organization.leader_status` | `recovery` | optional `slot_id` | Persisted job summaries with pagination, state counts, pending escalations, unacknowledged handoffs, missing slots, `running_workers_attested=false`. `slot_id` limits to department; director returns global view. |
| `organization.intake` | `feature` | `feature_id` | Immutable issue source ref/revision, scope digest and current owner approval state. |
| `organization.decompose` | `plan` or `candidates` | `feature_id` | Paginated child jobs, project edges, blocked reasons, source scope SHA. `candidates` filters candidates within each page. |
| `organization.handoff` | `list` | `feature_id` | Paginated cross-department handoffs with `ack_current` computed using live leadership epochs. |
| `organization.appoint` | `slot` | `slot_id` | Current principal/deputy, role, status, decision receipt, epoch. |

`plan`/`candidates` is a **local projection**, not scheduler admission: `scheduler_admitted=false` always. Jobs lacking owner approval, assignment, active accountable head, or current assignment epoch are blocked. For every prerequisite the module reports `DEPENDENCY_VERIFICATION_REQUIRED` until WG-E supplies **authoritative completed dependency evidence**; it never manufactures `READY` based solely on a handoff ACK or job-reported status. Integrator should submit exact manifest/edge pairs to WG-E; WG-E must independently perform DAG validation, lease/path checks, approved budgets and verified predecessor completion before creating executable work. Owner-approved features are not auto-launched as chats.

## Persistence and audit

WG-A must apply the SQL migration exactly once and verify its checksum. Tables: `org_features`, `org_jobs`, `org_edges`, `org_leaders`, `org_handoffs`, `org_authority_grants`, `org_escalations`, `org_notices`, `org_requests`. All accepted changes and `DomainEvent` journal writes happen within one `StateStore.transact()`. The frozen event `domain='organization'` and `kind='organization.<operation-verb>.<action>'` includes actor, trusted server timestamp, request correlation, idempotency causation, payload SHA256 and result receipt. No provider call executes inside transactions.

Two-phase resilience: the external `source.resolve` / `oversight.verify_notice` query happens before the SQL write; a provider outage returns `UNKNOWN/PROVIDER_UNKNOWN` or `BLOCKED/NOT_CONFIGURED`. A missing SQL ACK after commit is `UNKNOWN`; the next identical request recovers the exact persisted receipt. Crash before commit rolls back the mutation, receipt, and event in the same transaction.

## External integration seams and explicit NOT_RUN gates

1. **WG-F GitHub readback:** Send `QueryEnvelope(domain='source', operation='source.resolve', payload={'kind':'github_issue','ref':issue_ref,'expected_revision_sha256':revision})`. Require an independent, trusted `Outcome(state='ACCEPTED', payload={'kind':'GITHUB_ISSUE_READBACK','ref':issue_ref,'revision_sha256':revision,'verified':True})`. This WG-G-owned adapter response shape is an **INTEGRATION_CHANGE_REQUEST** if WG-F does not expose these exact fields. A worker-supplied SHA and GitHub Issue label/comment never count as proof. Gate: **NOT_RUN** real GitHub authenticated readback.
2. **WG-N Oversight:** Send `oversight.verify_notice` with `notice_ref`, `decision_code`, `evidence_sha256`. Require a narrow trusted successful result returning exactly those fields and `verified=True`. Reject revoked, mismatched, missing, unverified notices. Only opaque enforcement decision stored. The response shape may require integrator adapter. Gate: **NOT_RUN** real independent oversight verification.
3. **WG-A StateStore:** One shared SQLite authority initializes `schema_v1.sql` and injects its single-writer state port. Validate a built wheel and fresh database; source/ownership/QA authority remain separately verified.

4. **WG-B trusted gateway:** Must mint VerifiedIdentity, scope/epoch authentication and owner credential based on independently verified transport, not a prompt. Gate: **NOT_RUN** real ChatGPT gateway identity, no connector/credential or host supplied.
5. **WG-H role registry:** Must validate selected worker `role_id` and head roster against a pinned 174+20 data snapshot. Current module checks canonical department/head strings, without asserting imported skill/role inventory. Gate: **NOT_RUN** full external roster validation.
6. **WG-E scheduler:** Consume only owner-approved, explicit bounded DAG and currently authorized handoffs/assignments. Scheduler controls jobs READY/LEASED/RUNNING, guarded predecessor evidence, lease exclusivity and real shift admission. Gate: **NOT_RUN** integrated queue admission and 500/800 stream stress.
7. **WG-I review/merge:** Before any department-head merge, requery `organization.appoint`/`leader_status` for current head principal, scope, epoch; require independent QA/source evidence and fresh merge authorization. WG-G cannot grant merge permissions. Gate: **NOT_RUN** merged review/merge authority.

The `source`/`oversight` port shapes above are WG-G's explicit integration proposals; the frozen shared `CommandEnvelope`, `QueryEnvelope`, `Outcome`, `Component` and state protocols were not changed.

## Failure rules and capacity

Closed payloads reject unknown fields (`INVALID_SCHEMA`). Missing approval yields `OWNER_APPROVAL_REQUIRED`; wrong head grant `INVALID_SCOPE`; old leadership epoch `STALE_ASSIGNMENT`; absent leader `REVOKED_GRANT`; provider timeout `PROVIDER_UNKNOWN`; source mismatch `SOURCE_SHA_MISMATCH`; duplicate request with changed content `IDEMPOTENCY_CONFLICT`; oversized graph `BACKPRESSURE`; unauthorized self-review `SELF_REVIEW`; nonexistent source port `NOT_CONFIGURED`. A trusted operator's `mark_missing` records an observation/decision; it never means host-observed death. Recovery surfaces exact existing job records, fences privileges, then awaits a genuine new owner/delegation appointment epoch. No automatic model/worker launches, source pushes, budget/canon approvals or secret material are implemented.
