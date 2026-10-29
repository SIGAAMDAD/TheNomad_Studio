# WG-B / `trust` — Identity Security (CONTRACT/AUTH/1, CONTRACT/LAUNCH/1)

**Status:** local implementation and local tests. No ChatGPT connector attestation, production principal database, production CA or independently verified issuer has been installed by this worker. A public `worker_id` is not a credential.

## Public integration surface

```python
from nomad_studio.trust.api import build_component
trust = build_component({
    'store': state_store,                # WG-A StateStore port, required
    'slot_authority': approved_slot,     # trusted callable, required
    'clock_us': clock_us,               # optional: defaults to time.time_ns()//1000
    'max_ticket_seconds': 900,          # optional, <= 3600
    'max_session_seconds': 3600,        # optional, <= 86400
})
```

Only WG-A applies SQL. `slot_authority(worker_id)` must return an authoritative immutable snapshot (or `None` for unavailable/unapproved):

```python
{
  'role_id': 'approved-role',
  'epoch': 1,
  'jobs': ['approved-job'],
  'scope_digest': '64-character lowercase sha256 of approved job scope',
  'scopes': ['relay.poll', 'relay.ack', 'shift.next'],
  'connector_credential_ids': ['verified-connector-credential-id'],
}
```

Production slot authority must derive these from approved WG-H registration / WG-E assignment / current authoritative SQL, not a prompt or self-attested worker statement. Credential ID is a trusted gateway identity registry key, not a bearer secret. Adapter is called outside SQL transactions for side-effect isolation. Slot resolver outage returns `BLOCKED/NOT_CONFIGURED` on issue/activation and session authentication; database revocation and epoch remain independently enforced. The integrator must make slot-state updates and trust-epoch fencing consistent in the single SQL authority.

`build_component(deps)` returns `Component`, implementing `execute(CommandEnvelope, VerifiedIdentity)` and `query(QueryEnvelope, VerifiedIdentity)`; its concrete type also exports `authenticate_session(session_token, verified_transport) -> AuthResult` and `authorize_operation(verified_identity, CommandEnvelope|QueryEnvelope) -> Outcome`. **Only gateway/composition root may mint or hand a VerifiedIdentity to this component**; do not wire a caller-supplied identity value from HTTP, GitHub, LLM text or relay as that object.

## Operations and exact payloads

All command envelopes use `version=NOMAD/CMD/1`, `domain=trust`, `operation=trust.<verb>`, `request_id`, nonempty idempotency key. Payload keys are closed:

| Operation | Payload keys | Required server-side authority | Successful response |
| --- | --- | --- | --- |
| `trust.issue_launch` | `worker_id`, `role_id`, `job_id`, `scope_digest`, `epoch` (int), `scopes` (list), `ttl_seconds` (int), `connector_credential_id` | mTLS `owner/*` with `trust:issue_launch`; matching registered role, job, epoch, scope digest, connector ID and worker-eligible scopes | one-time `nl1.<random-id>.<256-bit-CSPRNG-secret>`, `ticket_id`, expiry, `identity_attestation=UNATTESTED` |
| `trust.activate` | `ticket`, `worker_id`, `role_id`, `job_id`, `epoch` | independently verified mTLS/connector principal with `trust:activate` and exact bound credential ID; unexpired, unconsumed, same live assignment | `ns1.<random-id>.<256-bit-CSPRNG-secret>`, `session_id`, `shift_id`, job, worker, expiry, `identity_attestation=UNATTESTED` |
| `trust.revoke` | `worker_id`, `expected_epoch` (int) | mTLS `owner/*` with `trust:revoke` | bumps epoch, revokes every outstanding ticket/session |
| `trust.rotate` | `worker_id`, `expected_epoch` (int) | mTLS `owner/*` with `trust:rotate`; live trusted slot snapshot has next epoch | increments epoch, refreshes role and unrevokes with invalidation of old tickets/sessions |
| `trust.authorize` | Not a wire-accessible privilege oracle | denied | `DENIED/NOT_SUPPORTED`; gateway calls trusted in-process `authorize_operation` |
| `trust.status` | Query only, `NOMAD/QUERY/1` | mTLS actor with exact `trust:status` | reports local security-policy capability and `chat_identity=UNATTESTED` |

Never journal/log issued `ticket`, active `session`, raw certificate keys, owner credentials, provider tokens, source secrets, or Oversight plaintext. The two capability strings are returned exactly once, over mTLS, in successful issuance and activation responses (both `Cache-Control: no-store`). Issuance idempotency replay is `CONFLICT/IDEMPOTENCY_CONFLICT` without re-disclosing the original ticket. A lost capability must be reissued from a new owner-approved request; old grants can be revoked.

### Worker scope and privilege boundaries

An activated worker session is scoped to one `worker_id`, one `role_id`, one `job_id`, one `shift_id`, one assignment epoch, one trusted connector credential ID, an expiry and only approved operation scopes. Each request must supply **matching** worker/job/shift identifiers; mutating command additionally supplies matching `assignment_epoch`. Worker operation allowlist is defined as `_WORKER_OPS` in `trust/api.py`, including worker-scoped relay, shift, scheduler, read-only source verification, job artifact operations, knowledge resolution and `review.submit`. It intentionally excludes `review.verdict`, `review.merge_intent`, `publication.*`, `oversight.*`, `budget.*`, `archive.*`, `trust.*`, and `source.protect`.

mTLS principals are explicit independent classes in trusted certificate registry, e.g. `owner/*`, `qa/*`, `head/*`, `auditor/*`, `witness/*`, `oversight/*`, `publisher/*`. Each must hold the **exact** `<domain>:<verb>` server-issued scope; policy also enforces reserved role classes on sensitive commands. A QA reviewer cannot become a merge head or owner merely by supplying actor_claim. A response's self-reported PASS never implies authoritative QA acceptance. Independent source SHA/lease/QA/owner approval checks remain WG-F/E/I/J responsibilities, even after gateway permission succeeds.

A shared-account ChatGPT connector does **not** cryptographically identify a unique browser chat. With a shared credential, the launch capability is a short-lived bearer, bound to the credential and job but transferable between chats using that same credential until consumed. A stolen ticket from that same shared connector may win first activation; single-use prevents a *second* session, not first-use theft. This limitation is fundamental until WG-P demonstrates supported independent per-chat attestation. Privileged publication requires separate mTLS issuers and verification.

## Durable schema and events

The pinned Studio V1 initial schema creates:

| Table | Purpose |
| --- | --- |
| `trust_workers` | authorized local worker role, revocation bit and monotonic security epoch |
| `trust_launches` | one-use ticket identifier, verifier digest only, job/role/scope digest/epoch/connector binding, expiry, consumed flag |
| `trust_sessions` | opaque session identifier, verifier digest only, bound shift/job/role/epoch/connector/expiry/revocation |
| `trust_mutation_keys` | mutation request replay conflict fence for ticket issue, revoke and rotation |

Events appended atomically via `StateTx.append_event`: `LAUNCH_ISSUED`, `LAUNCH_CONSUMED`, `WORKER_REVOKED`, `CREDENTIAL_ROTATED`. All include trusted principal, time, request correlation, worker/job/epoch where meaningful, and redacted payload; no capability verifier or raw ticket in event. A crash/fault before event append rolls back the corresponding SQL effect. Atomic one-use activation, even under concurrent contenders, depends on the WG-A serialized `StateStore.transact` contract; tests use a `sqlite3` in-memory fake implementing the port, not an independent production SQL owner.

The installed Studio V1 wheel carries a single `state/schema_v1.sql` resource. There is no embedded migration mirror or historical upgrade path.

## Deny and failure behavior

- `OWNER_APPROVAL_REQUIRED`: missing owner privilege for issue/revoke/rotate or unauthorized reserved operation.
- `IDENTITY_UNVERIFIED` / `UNTRUSTED_CONNECTOR`: absent, mutated or wrong bound transport credential/token.
- `EXPIRED_GRANT`, `REVOKED_GRANT`, `STALE_ASSIGNMENT`: expired, replayed, revoked or changed epoch/job/role/scope.
- `INVALID_SCOPE` / `INVALID_SCHEMA`: inappropriate worker operation, envelope IDs, scope list or payload.
- `IDEMPOTENCY_CONFLICT`: duplicated owner mutation identifier; no secret replay.
- `NOT_CONFIGURED`: absent authoritative slot or unresolved port; fail closed.
- `PROVIDER_UNKNOWN`: gateway could not determine durable downstream disposition (never a fabricated success).

No network call executes within any SQLite mutation. Clock must be trusted server wall-clock in microseconds; operator should monitor skew. Default CSPRNG is Python `secrets` and verifier comparison is `hmac.compare_digest`. Ticket TTL max default 900 s; session max default 3600 s, bounded by verified connector principal expiry; configuration may reduce but not silently extend limits beyond hard caps.

## Integration and external acceptance

WG-A initializes the single pinned V1 schema; WG-G/H/E must provide approved worker/role/job/scope/epoch port; WG-P must install, protect and rotate real TLS keys/CA and connector issuer configuration; WG-B gateway must establish the authenticated identity; WG-F/I/J enforce independent authoritative source/QA/publication readback. A genuine browser-to-connector-to-gateway activation and High reasoning attestation are **NOT_RUN / NOT_VERIFIED**. Tests only exercise a real *local* mTLS socket and a fake trusted slot authority. No production identity or remote reachability is asserted.
