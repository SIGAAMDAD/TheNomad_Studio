# WG-AH | V1 single operator authority, queue, unattended safety (S100-S102)

## Integration boundary

Source is limited to `nomad_studio.single_operator.ownercontrol`. Public facade:

```python
from nomad_studio.single_operator.ownercontrol import build_component
component = build_component(deps)
# The single Studio V1 StateStore already owns all ownercontrol tables.
```

`build_component(deps: Mapping[str, object]) -> Component` implements the *unchanged* frozen `contracts.v1` `CommandEnvelope`, `QueryEnvelope`, `Outcome`, `VerifiedIdentity`. It exposes `name='ownercontrol'`, the **six and only six** operation names below. These are proposed P2 registry names, not current v1 gateway authorization. No import of a sibling P1 implementation, no standalone database, no provider side effect. The `ownercontrol_*` tables and audit journal are initialized by the one pinned Studio V1 schema. No module migration loader, historical database upgrade or duplicate writer is used.

## Typed dependency ports (`deps`)

| Dependency | Proof signature / required shape | Missing/invalid behavior |
|---|---|---|
| `store` (or `state_store`) | Original `StateStore.read`, `.transact`; `StateTx.fetch_one`, `.execute`, `.append_event`; `state_actor_epochs` trusted live epoch row | `UNSUPPORTED/STATE_STORE_UNAVAILABLE`; ambiguous transaction is `UNKNOWN/STATE_STORE_UNKNOWN` |
| `clock_us` | trusted callable `() -> int` microseconds, increasing wall clock | default system clock; invalid result `DENIED/INVALID_SCHEMA` |
| `authority_epoch` | trusted callable `() -> int` current live epoch; match `VerifiedIdentity.current_authority_epoch` and transaction's `state_actor_epochs` | `UNSUPPORTED/AUTHORITY_PROVIDER_UNAVAILABLE`, `UNKNOWN/AUTHORITY_PROVIDER_UNKNOWN`, `DENIED/STALE_AUTHORITY_EPOCH` |
| `owner_verifier` | callable `(operation: str, target_id: str, target_digest: str, identity: VerifiedIdentity) -> OwnerProof` with exact operation, target, principal, epoch, signature reference, valid observation times, `verification_state='INDEPENDENTLY_VERIFIED'`, and queue CAS revision when applicable | `UNSUPPORTED/OWNER_VERIFIER_UNAVAILABLE`, `DENIED/OWNER_PROOF_NOT_VERIFIED` or `UNKNOWN/OWNER_VERIFIER_UNKNOWN` |
| `source_reader` | callable `(feature_id: str) -> Mapping` with exact `source_digest_sha256`, `build_digest_sha256`, `verification_state='INDEPENDENTLY_VERIFIED'`, `readback_ref`; installed source actor validates ref independently | owner `APPROVED` fails `UNSUPPORTED/SOURCE_READBACK_UNAVAILABLE` or `DENIED/SUBJECT_REVISION_STALE` |
| `review_author_reader` | callable `(subject_digest_sha256: str) -> Mapping` containing `subject_digest_sha256`, `author_principal_id`, `verification_state='INDEPENDENTLY_VERIFIED'` | AI review proposal `UNSUPPORTED/REVIEW_AUTHOR_UNVERIFIED` |
| `approval_policy_reader` | callable `(project_id,scope_digest,intent_digest,budget_policy_digest) -> ApprovalPolicyProof` with exact four-way match, `DecisionClass`, current epoch, independent proof ref | No `READY_FOR_DISPATCH`; store queue intent as `HELD_SAFETY` |
| `safety_provider` | callable `() -> SafetySignals` with bounded freshness (≤60 s), gateway/host/effects/grants/owner health, current authority epoch, observation reference and verification state `OBSERVED`/`INDEPENDENTLY_VERIFIED` | `safety_status` returns `UNSUPPORTED`, `preauthorized_ready=false`, `privileged_paused=true`; command retained `HELD_SAFETY` |
| `host_observer` | callable `() -> HostProof` from **independent, authenticated host instrumentation**, with session epoch, manual owner-launch receipt, observer signature ref, freshness and authority epoch matching `SafetySignals` | no live worker readiness: `live_host_verified=false`, `preauthorized_ready=false` |

`OwnerProof`, `HostProof`, `SafetySignals` are immutable frozen dataclasses defined in `model.py`. A test can inject an object shaped like one and exercise logic, but **SIMULATED is never real host/owner/provider proof**. Production integration must bind these to installed trusted validators; there is no bundled private signer, trusted owner key, ChatGPT wake/re-entry, signed host identity or automatic upgrade from JSON validity. `PROPOSED_CONTRACTS/*.schema.json` are `CANDIDATE-R10` test adapters only.

## Closed v1 envelope and operation contracts

All commands: `version='NOMAD/CMD/1'`, `domain='ownercontrol'`, `operation` exactly one name below, nonempty `request_id`, nonempty `idempotency_key`. All queries: `version='NOMAD/QUERY/1'`, same domain, `limit` 1–500, `cursor=null`, authenticated identity and exact per-operation payload. Unknown keys and types are denied. `query` never mutates state or invokes external writers.

| Operation | Execute payload (closed) | Query payload (closed) | Result |
|---|---|---|---|
| `ownercontrol.propose` | **required**: `decision_packet_id`, `decision_kind`, `policy_revision`, `authority_epoch`, `feature_id`, `priority`, `proposal_summary`, `evidence_refs`, `unknowns`, `alternatives`, `risk`, `subject_digest_sha256`, `source_digest_sha256`, `build_digest_sha256`, `requested_action`, `requested_authority`, `rollback_behavior`, `created_at_us`, `expires_at_us`, `review_kind`, `reviewer_principal_id`, `author_principal_id`, `recommended_default`. **optional**: `deadline_at_us`, `milestone_id`, `affected_features`. | optional `packet_id`, `feature_id` | Records `OWNER_REVIEW_REQUIRED` local packet. Proposal doesn't authorize anything. Duplicate content deduped even across packet IDs; stale feature source/build/policy supersedes previous pending or approved packets without erasing signed decision records. |
| `ownercontrol.queue` | Candidate intent **required**: `schema='NOMAD/OWNER-COMMAND/1-CANDIDATE-R10'`, `command_id`, `owner_auth_receipt_ref`, `intent_digest`, `expected_queue_revision`, `project_id`, `scope_digest`, `budget_policy_digest`, `expires_at_utc` (UTC). **optional**: `priority_class`, `required_approval_class`, `deadline_at_utc`. | optional `project_id`, `command_id` | Records local intent with monotonic queue revision. `READY_FOR_DISPATCH` is a local eligibility **hint**, never dispatch; `HELD_SAFETY`, `AWAITING_OWNER`, `EXPIRED` are fail-closed statuses. `owner_auth_receipt_ref` must equal trusted `OwnerProof.signature_ref`. |
| `ownercontrol.batch` | **required** `max_nonurgent` (integer 0–500), `expected_queue_revision` (integer ≥0), exact owner proof with `target_id='attention-quota'` | optional `feature_id`, `milestone_id` | Changes owner-set attention quota under CAS or reads risk-ranked grouped packets. HIGH and CRITICAL are shown regardless of quota and query limit; NORMAL/LOW bounded by quota. `nonurgent_deferred_by_quota` reports skipped count. |
| `ownercontrol.observe_decision` | **required** `packet_id`, `decision` (`APPROVED`/`REJECTED`/`DEFERRED`), `subject_digest_sha256`, `source_digest_sha256`, `build_digest_sha256`, `policy_revision`, `authority_epoch` | optional `packet_id`, `feature_id` | Requires current signed owner proof whose target digest is SHA-256 of canonical closed decision payload. `APPROVED` additionally requires fresh exact source/build readback; no approval from timeout/AI or old revision. A recorded decision row is immutable and replay returns same record. |
| `ownercontrol.safety_status` | **not supported** (query only) | optional `project_id` | Read-only `provider_status`, `preauthorized_ready`, `privileged_paused`, `owner_available`, `live_host_verified`, `reasons`. Unknown provider means `UNSUPPORTED`, not live `PASS`. |
| `ownercontrol.stop_scope` | **required** `scope_id` (`global` or project identifier), `action` (`STOP`/`RESUME`), `expected_revision` | optional `scope_id` | Exact owner proof and CAS per scope. STOP freezes matching eligible commands locally; RESUME never automatically makes held items ready. Actual watchdog/provider halt is a separate integration gate. |

**Decision kinds:** CREATIVE, ARCHITECTURE, CANON, MILESTONE, MERGE, RELEASE, BUDGET_EXCEPTION, RECOVERY, OTHER. **Typed decision classes:** `DecisionClass.AUTO`, `.DELEGATED`, `.OWNER_ONLY`. Creative, architecture, canon, milestone, merge, release, budget exception and recovery proposals force OWNER_ONLY. Queue non-owner classes require a trusted `ApprovalPolicyProof` binding intent, project, scope, budget, epoch and classification; the candidate payload's `required_approval_class` cannot elevate itself.  **Review kinds:** MACHINE_CHECK, AI_REVIEW, HUMAN_REVIEW, OWNER_DECISION. Proposal ingestion only accepts MACHINE_CHECK or independently source-author-verified AI_REVIEW; HUMAN_REVIEW and OWNER_DECISION labels in a model proposal are rejected `UNSUPPORTED/HUMAN_ATTESTATION_UNSUPPORTED`. Genuine HUMAN_REVIEW evidence requires another installed attestation channel; authenticated OWNER_DECISION occurs through `observe_decision`, not proposal text.

## Events, durability and replay

The Studio V1 initial schema creates `ownercontrol_packets`, append-only `ownercontrol_decisions`, `ownercontrol_queue_meta`, `ownercontrol_commands`, `ownercontrol_stops`; events are append-only in the original `state_events` ledger with `domain='ownercontrol'`:

- `DECISION_PROPOSED` — subject packet ID, packet digest, declared evidence class;
- `OWNER_DECISION_OBSERVED` — owner proof ref, decision enum, exact subject-bound digest;
- `OWNER_COMMAND_QUEUED` — command ID, queue revision, intent digest, proof ref, local eligibility;
- `OWNER_ATTENTION_QUOTA` — owner-set quota and version;
- `SCOPE_STOP` / `SCOPE_RESUME` — scope revision and proof ref.

Events contain *references and hashes*, not private keys, private HR case plaintext or source contents. Exact packet IDs and semantic fingerprints deduplicate proposals; two different packets for the same semantic proposal share the existing record while actionable. `command_id` + `(principal_id,idempotency_key)` protect queue replays and reject changed payloads. Queue CAS protects concurrent writers. Replays by the same still-current authenticated principal return the persisted outcome without another reservation or provider call. On any unknown store commit, caller must read `queue`/`propose` and retry with **the same** idempotency key; a new key may duplicate work. Decision rows are immutable, exact evidence/signature replays match, conflicting updates return `CONFLICT/DECISION_IMMUTABLE`. Expiration or `DEFERRED` requires a new packet; no silent promotion or timeout acceptance. An epoch change makes old commands ineligible when queried; all privileged consumers must recheck current source/policy/epoch at execution time.

## Unattended behavior and break glass

Only server-verified, time-bounded and owner-preauthorized **AUTO/DELEGATED** queue intents may be locally marked `READY_FOR_DISPATCH` when gateway and host are healthy, effects are KNOWN, grants ACTIVE, host observer bound to the exact session and epoch, the exact approval policy is independently verified, and no scope STOP. Queue queries re-evaluate live safety instead of trusting a historical `READY_FOR_DISPATCH` state. The owner may be unavailable; the healthy, independently preauthorized reversible queue can continue through **other integrated shift/runtime adapters** without the command chat remaining open. `OWNER_ONLY` remains `AWAITING_OWNER`; no approval is inferred from an ignored packet, an AI vote or an active job. New privileged side effects must be fenced during uncertain effects, missing grant, gateway/host loss or scope STOP. Completed artifacts, command intents, pending decisions and audits persist in the shared store. This component does **not** create or wake ChatGPT sessions or dispatch jobs.

Break glass: authenticate over the independently installed owner gateway, verify current authority epoch and exact scope, record a signed owner `stop_scope(STOP)` command, drain/fence external executors through independently authenticated orchestration, inspect outstanding unknown effects and reconcile readback before any verified owner `RESUME`. **No shared prompt recovery phrase or model-issued owner credential is defined.** A fake manual launch receipt or changed session epoch fails closed. The real host binding and live multi-job re-entry tests remain P4 `NOT_RUN` until observed by an external host provider.

## Error semantics, integration requests and verification

`DENIED`: malformed/expired identity, wrong scope/epoch, invalid payload, human/AI review forgery, unsigned owner proof, stale subject; `CONFLICT`: changed idempotency, immutable decision, stale optimistic revision; `UNSUPPORTED`: absent owner/source/review/safety authority port; `UNKNOWN`: unavailable provider or ambiguous StateStore result. `ACCEPTED` means **only local record acceptance**. No outgoing GitHub/Drive/issue/PR/ledger, game-domain or production host capability is implemented.

**P2 integration changes requested:** register versioned `ownercontrol.*` operations in gateway with per-operation exact policy; verify the pinned initial schema through the existing StateStore; connect real gateway epoch/owner signature/author/source/approval-policy/host/safety readback ports; register control-plane receiver for command skill (sole conversation ingestion), asynchronous director/worker shift dispatch, independent current-epoch/source recheck on every actual job/effect/merge/release and watchdog STOP enforcement; ensure canonical crew and creative policy nodes are owner-controlled. No sibling source or root files were edited here.

**Local verification:** `PYTHONPATH=src python -m unittest discover -s tests/single_operator/ownercontrol -p 'test_*.py' -v` uses real original SQLite StateStore with deterministic injected **SIMULATED** ports; host/owner/service integration is `NOT_RUN`. `PYTHONPATH=src python -m unittest discover -s tests/contracts -p 'test_*.py' -v`; `python -m compileall -q src/nomad_studio`; cold-built wheel resource check. Never report any mock `OwnerProof` or `HostProof` as production owner/host verification.
