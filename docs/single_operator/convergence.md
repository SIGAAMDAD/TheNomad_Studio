# WG-AI — Studio V1 project-neutral convergence (S103/S104/S105)

## Authority and deployment status

Implementation source is **local P1 only**, against the immutable verified A–S Studio baseline. This is a project-neutral control-plane component. It has **no game-domain imports, no source repository writer, no outbound message delivery, no automatic owner grant, and no live ChatGPT host integration**. All actual provider/worker/owner attestations require independently trusted composition in P2/P4. The isolated injected ports used by tests are **SIMULATED**, not real execution observations.

The component is instantiated via:

```python
from nomad_studio.single_operator.convergence import build_component
component = build_component({
    'store': original_singleton_state_store,
    'authority_epoch': trusted_epoch_reader,
    'clock_us': trusted_clock_us,
    'evidence_verifier': trusted_evidence_verifier,
    'session_verifier': trusted_session_verifier,
    'owner_verifier': trusted_owner_verifier,
})
# The single Studio V1 StateStore already owns all convergence tables.
```

`build_component(deps: Mapping[str,object]) -> Component` implements the frozen `CommandEnvelope`, `QueryEnvelope`, `VerifiedIdentity`, `Outcome`, and `DomainEvent` types in `nomad_studio.contracts.v1`, unchanged. The `convergence_*` namespace and seven tables are created through the one pinned Studio V1 initial schema, without any module-specific migration, wheel resource twin or historical upgrade. No gateway or root application registration is performed by WG-AI.

## Operation contracts (closed request payloads)

Each command uses `CommandEnvelope(version="NOMAD/CMD/1", domain="convergence", operation=..., request_id=..., idempotency_key=..., payload=...)`. Queries use `QueryEnvelope(version="NOMAD/QUERY/1", domain="convergence", operation=..., payload=...)`. The gateway must dispatch *exactly these six* versioned names after an explicit P2 authorization integration. An unknown payload field, missing required field, unrecognized enum, duplicate ID, revision conflict, or DAG cycle is denied. IDs are bounded to 160 safe ASCII characters; SHA-256 values are lowercase 64-hex; refs are bounded strings; JSON serialization rejects NaN and oversized inputs. Empty list of tasks is permitted. The aggregate persisted task limit is 800 per feature; dependency fan-in ≤128; discovery fanout ≤16; evidence-ref arrays ≤32 except discovery ≤50.

| Operation | Envelope | Mandatory payload and subtypes | Optional payload |
|---|---|---|---|
| `convergence.observe_issue` | execute | `action`, `project_id`, `feature_id` and one of three closed actions below | according to action |
| `convergence.review_packet` | execute | `project_id`, `feature_id`, `review_id`, `candidate_sha256`, `build_sha256`, `kind`, `result`, `observer_type`, `evidence_refs` | `owner_decision_ref` **only** for OWNER_ACCEPTANCE; then required |
| `convergence.plan_rework` | execute | `project_id`, `feature_id`, `proposal_id`, `review_id`, `reason`, `evidence_refs` | none |
| `convergence.propose_priority` | execute | `project_id`, `feature_id`, `proposal_id`, `priority`, `reason`, `evidence_refs` | none |
| `convergence.trace` | query | `project_id`, `feature_id` | `include_messages` boolean (default false) |
| `convergence.bottlenecks` | query | `project_id` | `feature_id` (otherwise bounded per-project aggregate) |

`observe_issue` action variants are mutually exclusive:

- **`snapshot`**: `issue_ref`, `artifact_ref`, `candidate_sha256`, `revision` (positive monotonically increasing integer), `author_id`, `proof_refs`, `tasks`, `capacity`; optional `build_sha256`. `tasks` is an array of closed records `{task_id, department_id, author_id, status, duration_units, dependencies, command_id}` with optional `{provider, lease_waiting, reviewer_waiting, tests_waiting}`. `status` is one of `PLANNED|ACTIVE|BLOCKED|DONE|REVIEW_PENDING|VERIFIED`. Required `capacity`: `{wip_limit,reviewer_capacity,integration_capacity}`; capacity values bounded 1–800, 0–800, 0–800. This snapshot must carry a verified evidence proof tied to its exact candidate SHA.
- **`discovery`**: `observation` exactly matching `crossfunctional_observation.v1.schema.json` reference: `{schema_version,observation_id,feature_id,source_subject_ref,subject_digest_sha256,reporter_worker_id,kind,impacted_domains,evidence_refs,proposed_resolution,authorization_state,current_status}`. Only `authorization_state=PROPOSAL_ONLY` and `current_status=OPEN` may be newly submitted. Requires independently verified worker/session and evidence. Creates one proposed follow-up per named impacted department (maximum 16), addressed to the first known accountable author, or `UNASSIGNED`. It never edits project scope or canon.
- **`message`**: `message` exactly `{message_id,from_worker_id,to_worker_id,message_kind,dependency_id,artifact_ref,subject_sha256}`. `message_kind` is `NEEDS_FROM` or `PROVIDES`; recipient must be an author from the registered feature task DAG and dependency must be a known task. The sender must match the authenticated, session-verified worker. Messages are durable **local records** with `delivered=false`, never remote dispatches or wake claims.

Review `kind` is `BUILD|MACHINE_TEST|TECH_QA|CREATIVE_AI|HUMAN_REVIEW|OWNER_ACCEPTANCE` and `result` is `PASS|FAIL`. Accepted reviewer categories: BUILD/MACHINE_TEST=`MACHINE`, TECH_QA=`AI_REVIEWER|HUMAN_REVIEWER`, CREATIVE_AI=`AI_REVIEWER`, HUMAN_REVIEW=`HUMAN_REVIEWER`, OWNER_ACCEPTANCE=`HUMAN_OWNER`. Observer type must match independently verified evidence. A PASS requires the prior PASS sequence BUILD → MACHINE_TEST → TECH_QA → HUMAN_REVIEW → OWNER_ACCEPTANCE. AI creative review is supplemental, not human review. Genuine owner acceptance additionally requires a distinct owner authority proof bound to owner principal, authority epoch, decision ref, and digest of the exact candidate SHA plus build SHA. All reviews are preserved historically, but change of candidate/build digest invalidates old reviews for current acceptance. Subsequent FAIL supersedes earlier PASS for the same review kind; subsequent re-checks also invalidate later stages until fresh ordered evidence is recorded. Technical reviewers/owner cannot equal the registered source author ID.

`plan_rework` accepts only a current, FAIL review and routes a proposal to the original feature author. `propose_priority` only records a proposed priority (0–100) with proof. Neither action edits scheduler, external issue trackers, external source trees, creative canon, or project budgets.

## Port proof shapes and denial states

All ports are injected by the trusted composition root, invoked only outside the StateStore transaction. They are **verification/read ports**, never authorization by prompt. Their exact input/output contracts are:

1. **`authority_epoch() -> int`** must return the independently verified current authority generation. Every command and query requires matching `VerifiedIdentity.current_authority_epoch`, non-expired grant, nonempty issuer/credential/principal, acceptable verified transport, and the exact scope `convergence.<operation>`. `convergence.owner_acceptance` is an extra distinct scope for owner review. An exception is `UNSUPPORTED/AUTHORITY_UNSUPPORTED`; mismatches deny `STALE_AUTHORITY`.
2. **`evidence_verifier(request: Mapping) -> Mapping`** receives `{kind,subject_sha256,evidence_refs,principal_id,credential_id}`. Must return independently verified `{verified:true,kind:<exact>,subject_sha256:<exact>,evidence_refs:<exact>,principal_id:<exact>,receipt_ref:<nonempty>}`; reviews also require an exact `observer_type` (verified human vs AI vs machine); `TECH_QA` and `HUMAN_REVIEW` additionally require an independently attested `independent:true`. For a review, `subject_sha256` is the canonical SHA-256 digest of `{candidate_sha256,build_sha256}`; owner proofs bind the same pair. `SNAPSHOT`, `DISCOVERY`, `REWORK` and `PRIORITY` are acceptable verification kinds for proposal operations. Failed/missing provider produces `UNSUPPORTED/EVIDENCE_UNSUPPORTED` or `UNSUPPORTED/EVIDENCE_NOT_VERIFIED`.
3. **`session_verifier(request: Mapping) -> Mapping`** receives `{worker_id,shift_id,assignment_epoch,source_sha,principal_id,credential_id}`; all six fields must echo exactly as independently checked issuer observations. Must additionally return `{verified:true,host_observed:true,wakeability_verified:true}`. Absence, epoch mismatch, forged shift, guessed host/wakeability, stale source or invalid credential produces `UNSUPPORTED/SESSION_UNSUPPORTED` or `UNSUPPORTED/SESSION_NOT_VERIFIED`. A fixture returning true is only a simulated test, never a live host proof.
4. **`owner_verifier(request: Mapping) -> Mapping`** receives `{decision_ref,subject_sha256,principal_id,authority_epoch}`. Must verify these exact fields and return `{verified:true,issuer_type:"HUMAN_OWNER",decision:"APPROVED"}`. Missing or unverified output produces `UNSUPPORTED/OWNER_UNSUPPORTED` or `UNSUPPORTED/OWNER_NOT_VERIFIED`.

The original gateway, not this module or chat prose, creates `VerifiedIdentity`. Missing external provider does not prohibit local computation or unit tests: it only denies the affected command. Explicit tests exercise stale session epoch/wakeability, missing evidence port, forged owner, and wrong scopes. Real host/session/credential, signing, external runner, and connector integration remain **NOT_RUN**.

## Durable state, outcomes, idempotency and events

The module writes only to `convergence_*` tables through the one injected StateStore; if `transact_domain` is supported, the writer transaction is `convergence`-namespaced. No outbound side effect runs in a state transaction. Event kinds: `SNAPSHOT_REGISTERED`, `DISCOVERY_PROPOSED`, `ADDRESSED_MESSAGE_RECORDED`, `REVIEW_RECORDED`, `REWORK_PROPOSED`, `PRIORITY_PROPOSED`. The DomainEvent payload is hashed and linked to the original request and principal.

The closed command is hashed with operation, payload, principal, authority epoch, worker/session/assignment/source context. A previously committed `idempotency_key` with the same fingerprint returns the identical stored receipt and payload without refetching external proofs. Reuse for different input is `CONFLICT/IDEMPOTENCY_CONFLICT`. Exact revision checks and current subject/review checks execute in the atomic commit; stale writes are `CONFLICT`. Pending ambiguous StateStore exceptions return `UNKNOWN/STORE_OR_PROVIDER_UNKNOWN` and must be reconciled by replaying the same key. A denied command makes no durable change. Response state `ACCEPTED/OK` confirms the *local* record was written only; a proposed priority/rework is **not** scheduled or applied externally.

Distinct state indicators: `TASKS_REPORTED → CANDIDATE_BUILT → MACHINE_TESTED → TECH_QA_REVIEWED → CREATIVE_REVIEW_PENDING → OWNER_ACCEPTED` represent exact evidence/review progression per feature; missing stages remain present in `missing_verified_stages`. Task DONE is not evidence of a build. Human review requires verified HUMAN_REVIEWER evidence; accepted owner needs separately verified HUMAN_OWNER authority, even when worker counts are large.

Metrics from `trace` or `bottlenecks` include measured (snapshot-supplied) `active_wip`, `wip_limit`, `admission_open`, `wip_over_limit`, `reported_done`, `verified_tasks`, `blocked_tasks`, `blocked_provider_operations`, `lease_contention`, `test_backlog`, `reviewer_backlog`, `reviewer_capacity`, `reviewer_pressure`, `integration_capacity`, `estimated_critical_path_units`, `remaining_critical_path_units`, and `integration_throughput_ceiling`. DAG lengths use the supplied abstract `duration_units` with exact declared dependencies; they are **estimates from verified snapshots**, not actual wall-clock runtime measurements. Test fixtures at 200/500/800 workers assert WIP pressure, pipeline ceilings, and longest path without falsely accepting a feature. Existing declared limits measure a closed admission gate; an integrator/scheduler must enforce the gate on *new job admissions* using a separate authorized composition port (see integration requests).

## P2 integration requests

- Verify that the pinned V1 SQL schema is in the installed wheel, and that the StateStore initializes it on a fresh database. Do **not** replace/modify the frozen v1 contract or let this module open SQLite.
- Register exactly six operations in a new versioned gateway allowlist, map verified scopes/project authorization, configure callable authority/session/owner/evidence ports from authentic issuers and adapters. Reject ungranted access.
- Supply snapshots from real project-neutral artifact/build/QA sources and independent reviewer provenance, binding actual source/build revisions and current policy/epoch. Wire addressed messages to the existing relay only after explicit admitted delivery semantics, with idempotent ACK and transport session checks; local persistence does not claim delivery.
- Adapt proposal packets to owner decision queue and rework/scheduler adapters after real owner approval. Enforce admitted WIP with actual scheduler capacity, not with reported headcount. Verify private HR/creative cross-domain integration separately; no private signing or source integration is claimed by this WG.
- P3 independent regression, live host reentry/epoch/wakeability, external provider readback, true cross-role review, and 500/800 capacity tests are unrun outside these deterministic isolated fixtures.
