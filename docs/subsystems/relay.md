# WG-C: GitHub relay and durable inbox

Contract: `NOMAD/CMD/1`, `NOMAD/QUERY/1`, `NOMAD/RECEIPT/1`; module contract `relay`. Source owns only `nomad_studio.relay`. No V1/V16 or second database authority.

## Facade and composition

```python
from nomad_studio.relay.api import build_component
from nomad_studio.relay.github_http import GitHubIssueCommentAdapter

# A fresh Studio V1 StateStore already has all relay tables.
# Actual identity and authorization adapters must be supplied by the trusted gateway.
github = GitHubIssueCommentAdapter(token=secret_from_deployment,
        targets={"slot-1": {"owner": "private-org", "repo": "private-relay", "issue_number": 9}})
relay = build_component({
    "store": shared_state_store, "github": github,
    "webhook_secret": github_webhook_secret_bytes,
    "authorize_worker": verify_current_worker_grant,
    "authorize_sender": verify_approved_sender_scope,
    "verify_processing_receipt": verify_durable_shift_or_job_receipt,
})
```

Studio V1 initializes the `relay_*` tables through the single canonical `schema_v1.sql` at database creation. No migration API or embedded SQL twin is used. WG-C never calls `sqlite3` in its source implementation or opens a second database. `build_component(deps)` requires the existing StateStore; it never creates a host, HTTP listener, scheduler, job, or launcher. All callbacks are required to fail closed and are invoked before entering SQL transactions. `VerifiedIdentity` is accepted only from the trusted gateway, never reconstructed from a GitHub comment. The backend MUST refresh identity/assignment/role epoch at every authorized call; no worker ID alone authenticates anything.

Injected dependencies:

| Key | Required for | Interface and trust conditions |
| --- | --- | --- |
| `store` | all operations | frozen `StateStore`, Studio V1 initial schema already installed; serialized durable transaction/event journal |
| `authorize_worker` | worker poll/ACK | callable `(VerifiedIdentity, worker_id: str, epoch: int, operation: str) -> bool`; real live session/job/assignment check; exactly `True` permits |
| `authorize_sender` | local enqueue | callable `(VerifiedIdentity, recipient: str, epoch: int, kind: str) -> bool`; verify approved job/control authority; exactly `True` permits |
| `verify_processing_receipt` | ACK | callable `(VerifiedIdentity, worker_id, epoch, message_id, receipt_id) -> bool`; must look up independently **durably persisted** job/shift processing record, not trust worker prose |
| `webhook_secret` | webhook ingest | secret bytes, 16+ bytes, supplied by deployment secret manager; never in GitHub, launch prompts or SQL |
| `github` | dispatch/readback | trusted provider object with methods below; optional until configured; absent returns `NOT_RUN`/`NOT_CONFIGURED` for external actions |
| `clock_us` | tests or host | trusted UTC microsecond callable, defaults to `time.time_ns()//1000` |
| `max_backlog` | queue | default 256 per recipient/epoch, hard range 1..10000 |
| `visibility_us` | redelivery | default 30,000,000 (30 s), range 1,000..3,600,000,000 |
| `max_deliveries` | poison message | default 12, range 1..1000 |
| `dispatch_lease_us` | external caller | default 30,000,000, range 1,000..3,600,000,000 |

## Frozen facade operations and closed payloads

All command envelopes use `domain="relay"`, `version="NOMAD/CMD/1"`, nonempty `idempotency_key`, and properly scoped gateway-issued identity. Payloads reject unknown keys and duplicate batch job IDs. No raw credentials are ever accepted.

| Operation | Payload | Scope | Result/authority |
| --- | --- | --- | --- |
| `relay.ingest` assignment | `{"source":"assignment", "worker_id":"slot", "epoch":2, "active":true}` | `relay.assign` | SQL epoch binding update; stale pending messages dead-lettered; only owner/trusted scheduler grant issuer |
| `relay.ingest` local | `{"source":"local", "recipient":"slot", "epoch":2, "kind":"PROMPT_BATCH", "body":{"items":[{"job_id":"j1","prompt_ref":"github:...","source_sha":"<64 lowercase hex>"}]}, "ttl_seconds":3600, "causation_id":"optional"}` | `relay.send` and `authorize_sender` | 1..32 jobs/message, max 32768 canonical JSON bytes; exact worker/epoch; audit event + message/immutable digest; `CONTROL` additionally needs `relay.control` |
| `relay.ingest` control | local, kind=`CONTROL`, body=`{"action":"STOP"\|"DRAIN","control_epoch":1,"reason":"optional"}` | `relay.send`, `relay.control`, sender authz | typed durable control; delivered next **poll**; never interrupts a running ChatGPT model or idle web chat |
| `relay.ingest` GitHub webhook | `{"source":"github_webhook", "delivery_id":"...", "event_type":"issue_comment", "signature":"sha256=<hex>", "body_base64":"..."}` | `relay.ingest.webhook` | accepts only full raw-byte verified HMAC SHA-256, bounded 262144 bytes, provider delivery ID, exact repo/resource reference; creates **observation only**, not authenticated worker command |
| `relay.ingest` readback | `{"source":"github_readback", "external_ref":"github:owner/repo:issue_comment:123", "expected_sha256":"optional lowercase SHA-256"}` | `relay.ingest.readback` | fetch exact resource via trusted GitHub REST port; readback absence/timeout UNKNOWN, optional expected digest mismatch CONFLICT, dedup resource revision into observation |
| `relay.poll` | `QueryEnvelope(version="NOMAD/QUERY/1", worker_id="slot", payload={"assignment_epoch":2}, cursor="2:10", limit=100)` | `relay.poll` plus `authorize_worker` | addressed recipient-only page, control first, bounded indexed eligible unacked messages, server cursor advances, timestamped visibility lease; `cursor` is informational/validated, never used to silently skip unacked messages |
| `relay.ack` | `{"message_id":"...", "body_sha256":"...", "processed_receipt_id":"..."}`; envelope includes `worker_id` and `assignment_epoch` | `relay.ack`, live grant, verified durable processing receipt | accepted only after DELIVERED and current epoch; sets ACKED. **ACK is not QA PASS, completion, or worker role elevation** |
| `relay.retry` | `{"message_id":"..."}` | `relay.dispatch` | reserve dispatch in SQL; GitHub lookup/publish/readback outside transaction; VERIFIED only on exact digest + address + provider revision; otherwise BACKOFF/UNKNOWN/CONFLICT |
| `relay.dead_letter` | `{"message_id":"...", "reason":"..."}` | `relay.manage` | operator cancellation into durable dead-letter state, no delete |

Closed command/query versions reject major changes. Required `idempotency_key` collisions with a different command digest return `CONFLICT/IDEMPOTENCY_CONFLICT`. Duplicate GitHub delivery ID and digest preserve the first event; digest collision with changed content conflicts. Reordering of distinct provider IDs does not alter recipient ordering. `NOMAD/RECEIPT/1` is the carrier content version, not an assertion that GitHub content has authenticated a worker.

## GitHub issue-comment carrier port

Optional `GitHubIssueCommentAdapter` uses only stdlib `urllib`, GitHub REST over pinned `https://api.github.com`, deployment-injected short-lived installation token, static private owner/repo/issue target per worker, and bounded pages. Each `relay.retry` calls:

- `github.lookup(anchor: str, recipient: str, epoch: int) -> {state: FOUND|ABSENT|RATE_LIMIT|RETRYABLE|UNKNOWN, content_sha256?, external_ref?, revision?, definitive_absence?, idempotent_create?}`
- `github.publish(anchor: str, content: str, recipient: str, epoch: int) -> {state: CREATED|RATE_LIMIT|RETRYABLE|UNKNOWN}`
- `github.read_event(external_ref: str) -> {state: FOUND|UNKNOWN, raw: bytes, delivery_id: str, event_type: str, external_ref: str, revision: str}`

Carrier body is canonical JSON containing the exact anchor, recipient, assignment epoch, kind, body digest, and body. `lookup` matches all address fields. On exact matching readback with immutable content digest and revision, the SQL external status becomes VERIFIED. The provider's POST `CREATED` response is **not** proof. A timeout after POST leaves UNKNOWN; retries check readback before doing anything else. Each attempted external POST is fenced by durable `PUBLISH_INTENT`. Generic REST issue-comment creation has no server-enforced idempotency, therefore the supplied adapter advertises `definitive_absence=false` and `idempotent_create=false`: a possibly successful timed-out POST is never automatically recreated. If an external provider *does* guarantee definitive absence and atomic idempotent create, adapter may explicitly declare both booleans to permit automatic retry. A readback mismatch yields CONFLICT. Provider 429 backoff resets the write-attempt marker only on a reliable refused-write response. Provider 5xx is ambiguous. Network/provider calls do not run under WG-A write transactions.

The adapter does **not** expose a webhook server. WG-P installs a trusted HTTPS ingress and supplies raw payload, `X-Hub-Signature-256`, `X-GitHub-Delivery` and `X-GitHub-Event` as the constrained `relay.ingest` command. Webhook transport HMAC does not authenticate authors of arbitrary GitHub comments, including PR comments. No comment can directly command a STOP/QA/PASS/source merge. Signed webhook events and REST readbacks create `EXTERNAL_OBSERVED` journal entries; a separately authorized scheduler/director must decide what to do with them.

## Durable schema and events

`relay_workers` is the WG-C local routing epoch mirror; it does not supersede WG-B/E. `relay_cursors` stores per-worker/epoch high-water sequence; `relay_messages` stores canonical body, SHA-256, recipient/epoch, sequence, ACK receipt, visibility, expiry, dead-letter reason, immutable external anchor, publication intent, revision and retry state. `relay_request_keys` holds idempotent accepted outcomes; `relay_external_events` holds `(provider,delivery_id)` dedup with SHA-256, actual external reference, version/revision. Unique indexes enforce message id, external anchor, delivery ID, command key; `relay_scan(recipient_worker_id,assignment_epoch,status,available_at_us,sequence)` bounds addressed inbox reads. All mutation and delivery effects call `StateTx.append_event(DomainEvent)` in the same transaction. Events: `ASSIGNMENT`, `ADDRESSED`, `DELIVERED`, `ACKED`, `EXPIRED`, `DEAD_LETTER`, `EXTERNAL_OBSERVED`, `DISPATCH_RESERVED`, `PUBLISH_INTENT`, `DISPATCH_VERIFIED|UNKNOWN|BACKOFF|CONFLICT`. Event payload is redacted to identifiers/digests (never full prompt/secret). Events have principal, correlation ID, assignment epoch and digest. Old messages are retained after reassignment but are never delivered to the new epoch.

Delivery states: `ADDRESSED → DELIVERED → ACKED`, plus `EXPIRED`/`DEAD_LETTER`. Visibility expiry permits repeated `DELIVERED` without skipping it via cursor. Expired messages are reconciled when that worker is polled or receives a new enqueue; bounded poison messages dead-letter after max deliveries. Saturated queues return BACKPRESSURE for ordinary prompts. STOP/DRAIN can preempt the oldest pending ordinary prompt into an immutable dead letter; control messages sort first even under a saturated backlog. No broadcast fanout is performed. A process crash before event commit rolls back the whole SQL mutation.

## Fail-closed outcomes and runtime limits

- `IDENTITY_UNVERIFIED` or `INVALID_SCOPE`: invalid worker scope, missing live authz, cross-recipient/epoch access, missing verified processing receipt; `STALE_ASSIGNMENT`: SQL epoch mismatch; `STALE_LEASE`: ACK before delivery or after terminal state.
- `INVALID_SCHEMA`, `MALFORMED_REQUEST`: unknown fields/versions, bad refs or more than 32 jobs, overlong bodies; `IDEMPOTENCY_CONFLICT`: changed payload with reused command/delivery key.
- `BACKPRESSURE`: per-worker full (controls preempt), active dispatch lease, GitHub 429/5xx with bounded retry.
- `PROVIDER_UNKNOWN`: provider timeout, no authoritative readback, ambiguous write; `DOCUMENT_CONFLICT`: readback digest mismatch/epoch changed; `NOT_CONFIGURED/NOT_RUN`: missing live provider or connector; `STATE_UNAVAILABLE/BLOCKED`: transaction/journal failure.

## Integration seams / blocked external gates

WG-A: verify the pinned V1 initial schema and guarantee `StateStore` serializable mutation/event commit semantics. WG-B: trusted `VerifiedIdentity` creation, scopes, revocation, live `authorize_worker` binding and issuer check; never accept identity reconstructed by the worker. WG-E: emit monotonic `assignment` epoch update with owner/issuer authority before allowing a new job, and bind sender permissions to approved DAG/lease. WG-D: provide independently durable shift/checkpoint receipt verification, poll controls at tool boundaries, ACK after transactionally persisted processing, not after reading a prompt. WG-G: select appropriate addressed prompt batch and causal refs. WG-J/P: provision private issue carrier mapping, short-lived GitHub token, GitHub App scopes, TLS webhook secret delivery, retry scheduler and real GitHub REST readback; recover UNKNOWN without fabricating proof. WG-M: inspect stale delivered messages, external UNKNOWN and dead letters. WG-Q: independently test concurrency, provider chaos, 500/800 active-capable streams on final merged source and current credentials.

**NOT_RUN** without live provider/host/ChatGPT connector: production GitHub App token/scopes, real signed webhook over TLS, real GitHub comments and revision readback, ChatGPT standard web-chat polling from installed connector, human High setting attestation, 500 actually admitted ChatGPT responses, source/QA/release publication, full merged WG-A/B/D/E/P interoperability. Unit tests include real local on-disk SQLite and mocked port fault injection, not production certification.
