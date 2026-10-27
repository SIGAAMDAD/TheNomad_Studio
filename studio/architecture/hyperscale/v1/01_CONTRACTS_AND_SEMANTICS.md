# Coordination V2 contracts, state transitions and trust boundaries
Status: proposed v2 contracts. Existing V1 schemas and GitHub history are frozen compatibility inputs; do not rename/delete V1 messages.

## Entity identifiers
- logical_worker_id: stable human-approved identifier, unique in the Studio tenant; never reuse retired IDs.
- worker_attempt_id: fresh ID for one user-initiated, scheduled or explicit externally launched response. A fresh attempt is not proof that a prior one stopped.
- job_id: existing approved Studio work-order identity; parent FEATURE-N and source-workstream links stay authoritative.
- assignment_epoch: monotonically increasing per job/worker pairing; stale epochs cannot ACK or write on behalf of a new assignee.
- authority_revision: immutable GitHub approved registry/job receipt ID, used to compare the projector's cached permissions.
- source_revision: owning repository + full commit SHA, not a mutable branch alias.
- event_id: producer-unique globally stable UUIDv7 or equivalent; transport_id: provider plus raw delivery ID; both must be deduplicated separately.
- batch_id: partition, monotonic sequence, schema version and content digest; archive identity is independent of GitHub commit ID.
- correlation_id / causation_id: workflow/request trace + immediate parent event; include a depth limit and loop detection.

## Immutable envelope example (NON-AUTHORITATIVE illustrative data)
~~~json
{
  "schema_version": 2,
  "event_id": "018f0000-example-not-a-real-uuid",
  "transport": {"kind": "github_issue_comment", "provider_delivery_id": "example-only"},
  "source": {"repo": "SIGAAMDAD/TheNomad_Studio", "issue_number": 6},
  "work": {"job_id": "FEATURE-6-G00-01", "assignment_epoch": 7,
           "worker_id": "CW-ENG-0001", "attempt_id": "attempt-example"},
  "route": {"partition_key": "FEATURE-6-G00-01",
            "recipient_job_id": "QA-FEATURE-6-G00-01",
            "recipient_worker_id": "CW-QA-0004"},
  "kind": "HANDOFF_READY",
  "causation_id": null,
  "correlation_id": "request-example",
  "auth": {"transport_verified": true, "worker_identity_verified": false,
           "authority_revision": "github:approved-registry-SHA"},
  "evidence": {"source_repo": "SIGAAMDAD/TheNomad_GOAP",
               "source_sha": null, "ci": "NOT_RUN", "qa": "NOT_RUN"},
  "payload": {"summary": "Bounded neutral example", "content_ref": null,
              "content_sha256": null},
  "received_at": "2026-10-27T00:00:00Z"
}
~~~
All example IDs and timestamp are fictional. Implementation MUST use a real schema and test fixtures, including strict unknown-field rejection for authority-carrying events. Never put private case content, personal secrets, credentials or long diffs into summaries.

## V1.0.0 lifecycle compatibility
Merged Studio 1.0.0 includes STARTED, HEARTBEAT, YIELDED, SUBMITTED and STOPPED; these carry unique attempt_id and assignment_epoch, with ledger_event_id, elapsed_seconds and resume_required for applicable checkpoints and terminal responses. V2 MUST retain their semantics and model claims as observations until separately attested. The existing 100-synthetic-worker routing/ACK/ledger fixture is a baseline regression, NOT 100 live concurrent ChatGPT executions.

## Distinct state machines
**Delivery state:** RECEIVED -> ACCEPTED -> PROJECTED -> AVAILABLE -> ACKED. Other terminal outcomes: REJECTED (invalid), EXPIRED (policy-defined TTL), DEAD_LETTER (repeated transport failure), SUPERSEDED (compatible later message). The immutable event remains archived across transitions. ASSIGNED/READY_FOR_NEXT_ACTIVATION is not the same as EXECUTING.

**Work state:** PROPOSED -> OWNER_APPROVED -> READY -> LEASED -> CHECKPOINTED/SUBMITTED -> CI_VERIFIED -> INDEPENDENT_QA -> OWNER_ACCEPTED. Other outcomes: BLOCKED, REWORK, PAUSED, CANCELLED. Existing Studio runtime state names prevail in the compatibility adapter; this is a *semantic* target model, not permission to change the existing enum in place.

**Executor observation:** REGISTERED, ASSIGNED, RESPONSE_STARTED_OBSERVED, RESPONSE_CHECKPOINT_OBSERVED, INACTIVE_UNKNOWN, EXPIRED_HEARTBEAT. A worker's own status claims remain assertions unless a trusted executor produces an attested run receipt. Do not infer live-chat execution from source commits or inactivity.

## Ordering and acknowledgment
- Per-job ordering by durable accepted sequence; no global total order. Cross-job handoffs carry exact upstream revision and explicit dependency edge.
- Fan-out is one delivery row per recipient/assignment epoch, dedup key (event_id, recipient_worker_id, assignment_epoch).
- ACK is durable only when the eligible consumer emits a typed acknowledgment referencing the original event_id in the **same target assignment epoch**. ACK means processed/seen, never owner approval or QA PASS.
- On reassignment, unconsumed deliveries remain in the durable queue and are re-evaluated against a new assignment epoch; old ACKs never clear a new worker's inbox.
- Queue visibility timeouts are not source leases. Long external API work must use a separately approved execution host/heartbeat; manual ChatGPT chats only checkpoint when they actually run.
- A late or repeated event is replayed idempotently with a new delivery attempt ID but the same source event ID; side effects require unique constraints and compare-and-swap version checks.
- Priority lanes can accelerate urgent audit/blocked events but may not reorder authority-sensitive events within the same job. Avoid starvation through weighted quotas and explicit age limits.

## Source-write concurrency lease
Lease key: (owning_repo, protected resource/path-range, architectural interface revision). Lease record: job_id, authorized worker_id, assignment epoch, lease_generation (fencing token), source baseline SHA, expiry, signed/verified permission receipt. Intersecting protected paths cannot have simultaneous exclusive writers. Branches are separate until integration; integrators hold an exclusive integration lease. The source pipeline rejects stale lease generations and post-approval SHA changes. A lease does not itself authorize edits while source_writes_enabled=false. With a shared owner GitHub token, worker-ID authorization cannot be cryptographically enforced at GitHub, so privileged source writes require scoped identities and independent source CI/policy checks before scaling.

## Database transaction sketch
~~~text
BEGIN;
  INSERT received_events(provider, source_id, event_id, partition_key, data)
    ... ON CONFLICT DO NOTHING;
  IF inserted AND policy_snapshot_is_valid:
    INSERT outbox(event_id, destination_partition, state='PENDING');
    INSERT immutable_event_audit(...);
  COMMIT;
outbox_publisher publishes with event_id as deduplication key;
consumer:
  BEGIN;
    INSERT consumption_effects(event_id, projector_id, version) ON CONFLICT DO NOTHING;
    IF first:
      update recipient projection and checkpoint atomically;
    COMMIT;
  ACK queue message after commit.
~~~
A consumer crash between database commit and queue ACK yields redelivery with no duplicate effect. A crash before commit yields normal reprocessing. Queue-provider deduplication windows are *not* the primary idempotency guarantee.

## Authorization tiers
- Public-to-Studio observation: Issue comment, build metadata, untrusted worker prose; may be ingested/archive-only.
- Job-state changes: require validated job ID, assignment epoch, owner-approved policy and actual work evidence.
- Source write / lease mutation / CI verdict: requires exact repo SHA, current ownership, independent issuer and source-validated evidence.
- Owner-controlled: canon, architecture acceptance, spending, merge policy and permanent access restrictions. A separately authenticated accountable department head may execute a fully gated merge when owner authorization and independent QA are verified, as in Studio 1.0.0.
- Independent Oversight: isolated encrypted intake and minimally signed, scoped enforcement notices; production sees no case description or reporter identity.

## Schema evolution and revocation
Event v1 -> v2 adapter preserves original transport identity and exact event ID linkage. Reject unsupported major versions, explicitly record transformation version and hash. Never translate a v1 worker's claimed identity into an authenticated v2 source. Revoke leaked credentials, increment assignment/lease generation, invalidate affected projections, fence stale writers, and replay from a trusted earlier cursor without deleting the evidence chain.
