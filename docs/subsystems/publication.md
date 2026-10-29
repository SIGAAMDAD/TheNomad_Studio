# WG-J — Publication and recovery (`NOMAD/CMD/1`)

## Composition

`nomad_studio.publication.api.build_component(deps: Mapping[str, object]) -> Component`
exports `execute(CommandEnvelope, VerifiedIdentity)` and `query(QueryEnvelope, VerifiedIdentity)`.
`load_migrations() -> tuple[Migration, ...]` returns `publication` migrations with
SHA-256 content checksums. The integration root registers those with **WG-A's**
sole SQLite owner before serving requests. WG-J opens no database connections.

Required dependencies (injected by the integration root):

| Key | Behavior / trust requirement |
| --- | --- |
| `store` | Frozen `StateStore`, with SQLite migrations installed and journal `StateTx.append_event` |
| `authorization` | A trusted **Component** facade implementing `query(QueryEnvelope(domain="trust", operation="trust.authorize", payload={"resource":"publication", "operation":..., "bindings":..., "bindings_sha256":...}), identity) -> Outcome(state="ACCEPTED", code="AUTHORIZED")` ONLY after live gateway epoch, owner/head authorization, source SHA/path protection, active lease/assignment, separate exact-subject QA PASS and approval checks. Missing/failing authority blocks writes. |
| `clock_us` | Trusted wall-clock microseconds; default standard-library time |
| `github_transport` | Injected credential-isolated transport (see below) |
| `drive_transport` | Injected credential-isolated transport (see below) |
| `artifact_reader` | Immutable artifact port: `read(artifact_ref) -> bytes` |
| `quotas` | Required per provider: `{'github':{'max_per_window':N,'window_us':N}, 'drive':{...}}`; persisted fixed-window quotas |
| `max_artifact_bytes` | Max Drive readback/publish payload, default 16 MiB |
| `max_attempts` | Safe CAS attempts before BLOCKED, default 8 |
| `backoff_us` | Base delay for rate limiting, default 1,000,000 µs |

The component requires exact operation scope (`publication.prepare`, `.dispatch`,
`.readback`, `.reconcile`, `.conflict`, or `.query`). Privileged recovery across
other principals additionally requires scope `publication.recover`; the authorization Component
must still independently authorize the action. `VerifiedIdentity` must be
minted by WG-B through a trusted transport; constructing one from a prompt
claim is forbidden. No credentials, grant secrets or provider tokens are
persisted in the publication table.

## Closed command schemas

Envelope `domain='publication'`, `version='NOMAD/CMD/1'`, `operation` as fully
qualified `publication.<verb>`, unique nonempty `idempotency_key`. For
`prepare`, the envelope `job_id`, `assignment_epoch`, `source_sha` must exactly
match `payload.bindings`. Every command has a nonempty `request_id`.

`publication.prepare` payload (all fields required, unknown fields rejected):

```json
{
  "provider": "github",
  "action": "update_ref",
  "effect_key": "effect-unique-1",
  "artifact_ref": "immutable-manifest-1",
  "artifact_sha256": "<64 lowercase hexadecimal characters>",
  "artifact_size": 123,
  "expected_revision": "<previous immutable git OID or Drive revision>",
  "target": {
    "repository": "owner/repository", "ref": "refs/heads/feature",
    "proposed_revision": "<new git OID>", "manifest_path": "release/manifest.json"
  },
  "bindings": {
    "job_id": "job-1", "assignment_epoch": 5,
    "source_sha": "<40 or 64 hex git OID>",
    "source_path": "src/file.cpp", "lease_id": "lease-1",
    "lease_epoch": 7, "qa_receipt_id": "qa-1",
    "qa_subject_sha": "<same git OID as source_sha>",
    "approval_ref": "owner-or-head-approval-reference"
  }
}
```

`target` choices, **exactly**:

| `provider` / `action` | Required `target` fields | Checks |
| --- | --- | --- |
| `github` / `update_ref` | `repository`, `ref`, `proposed_revision`, `manifest_path` | Expected ref SHA and new commit OID; immutable manifest bytes and commit effect marker; GitHub atomic expected-OID CAS; never force |
| `github` / `create_pr` | `repository`, `head_ref`, `base_ref`, `head_revision`, `manifest_path`, `title`, `body` | `expected_revision == head_revision`; head ref SHA, immutable manifest, commit effect marker; PR body contains derived stable effect marker; readback uniqueness |
| `drive` / `replace_file` | `file_id`, `parent_id`, `mime_type` | Immutable local byte digest/size; parent scope; atomic remote revision CAS; exported remote bytes and metadata checked twice |

The `artifact_sha256` always names bytes, **not** a Git commit OID.
It is calculated with `hashlib.sha256(actual_bytes)`. GitHub commit artifacts
must already exist in GitHub and be readable at the indicated manifest path.
GitHub commit effect marker must be extracted from immutable commit metadata
(never trusted from a mutable PR title). Drive effect marker is metadata
set together with its content in the conditional write.

`publication.dispatch`, `.readback`, `.reconcile` payloads contain only
`{"intent_id":"<opaque UUID>"}`. `publication.conflict` requires that plus
`"reason"` (a sanitized identifier), and requires a separately privileged
operator grant. It dead-letters rather than clearing evidence; it cannot
replace a VERIFIED intent.

`publication.status` and `publication.pending` are `NOMAD/QUERY/1` queries;
`status` accepts optional `{ "intent_id": "..." }`, `pending` accepts `{}`.
`limit` is in `[1,100]`; `cursor` is the last `intent_id` from `next_cursor`.
Results are restricted by principal unless recovery scope and authorization permit
operator inspection. `pending` lists PREPARED, DISPATCHING,
PENDING_UNKNOWN, and EXTERNALLY_OBSERVED for bounded recovery scanning.

## State machine and durable effects

1. `prepare` validates/fingerprints all binding fields; reserves one intent
   under unique `idempotency_key` **and** `effect_key` in a single SQLite
   transaction, appending PREPARED audit event. Different content for a
   prior idempotency/effect key yields `IDEMPOTENCY_CONFLICT`.
2. `dispatch` rechecks live authority, atomically debits the provider quota,
   reserves the intent with a unique claim token and appends DISPATCHING.
   Network calls occur **after the transaction commits**.
3. Provider known conflicts/forbidden/rate limits are persisted as
   CONFLICT, BLOCKED or PREPARED-with-backoff; a timeout/crash is retained as
   PENDING_UNKNOWN or DISPATCHING. A provider write acknowledgement is
   recorded as EXTERNALLY_OBSERVED, **not verified**.
4. `readback`/`reconcile` obtains a fresh remote read **outside the SQL
   transaction**, then transitions in a separate transaction: APPLIED with
   expected revision, marker and byte digest -> VERIFIED; divergent revision,
   marker, parent or bytes -> CONFLICT and dead-letter; indeterminate read ->
   PENDING_UNKNOWN. A proven unchanged revision with atomic CAS semantics may
   reset update_ref/replace_file to PREPARED. An absent PR index never permits
   retry of an ambiguous PR creation.
5. Caller can enumerate pending intents with `publication.pending`, dispatch
   PREPARED or reconcile ambiguous items. No service auto-launches ChatGPT
   workers, pushes branches, force-updates a ref, or merges a canonical PR.

Every durable status mutation records an append-only `DomainEvent` with
principal, correlation, job and revision evidence. Audit append is in the
same store transaction as status. Snapshot and network intent are never a
single distributed atomic transaction: the outbox is an explicit saga.

### Module tables

`publication_intents`: opaque intent, idempotency/effect uniqueness,
request fingerprint, principal, provider/action, immutable canonical JSON
payload, current state, attempt count, next eligible time, exclusive claim,
observed revision, failure code, and timestamps. `publication_quota`: SQL
serialized fixed-window quota usage by provider. `publication_dead_letters`:
immutable reason and created time per terminal conflict. Indexes cover
recovery state/due and principal. Required SQLite `foreign_keys=ON` belongs
to WG-A. The migration is `migrations/001_outbox.sql`.

### Transport ports (not actual network clients)

`github_transport` must expose:

- `read_ref(repo, ref) -> OID|None`
- `compare_and_swap_ref(repo, ref, before_oid, after_oid, force=False)`:
  **atomic** expected-ref operation, not a REST read/patch sequence.
  GitHub GraphQL `updateRefs` supports `RefUpdate.beforeOid`, `afterOid`,
  `force`; `updateRef` alone does **not** expose the same compare argument.
- `read_commit_artifact(repo, oid, path) -> bytes` and
  `read_commit_effect_key(repo, oid) -> str` from immutable Git data
- `find_pull_requests(repo, head_ref, base_ref) -> Sequence[dict]` with
  exhaustive relevant PR enumeration and fields `id`, `head_revision`,
  `body`; `create_pull_request(repo, head_ref, base_ref, title, body)`.

`drive_transport` must expose:

- `atomic_revision_cas is True` ONLY if the actual implementation proves
  atomic expected-revision conditional mutation (including simultaneous
  writers); no read-then-unconditional-overwrite substitute.
- `read_file(file_id) -> {'revision', 'parents', 'effect_key'}`
- `replace_if_revision(file_id, expected, bytes, parent_id, mime, effect_key)`
- `export_file(file_id, mime_type) -> bytes` of exact requested revision
  format, with a second matching metadata read.

**Deployment blocker:** The public Drive v3 `files.update` reference documents
PATCH/update endpoints, but the exact atomic conditional revision feature
required by this port has not been established in this workgroup. Until a
real adapter demonstrates it, the Drive transport must set
`atomic_revision_cas=False`; Drive writes fail closed with `NOT_SUPPORTED`.
An alternative integration design may use immutable new file IDs and an
independently serialized pointer protocol, but must be approved by the owner.
No Drive OAuth credential or connector behavior was exercised here.

Injected transports are the only credential holders, and MUST supply proper
HTTP timeouts, rate responses, pagination, scopes and immutable readbacks.
Provider adapters discard provider receipts for verification. Their typed
`ProviderConflict`, `ProviderForbidden`, and `ProviderRateLimited` exceptions
must be raised only when the transport can prove the effect was rejected;
all ambiguous failures must surface as unknown. `429` and `403` error
classification and actual provider HTTP semantics remain external gates.

## Failures and limits

- `INVALID_SCHEMA`, `MALFORMED_REQUEST`: reject unknown fields, bad
  revisions, paths, auth-envelope and typed operation.
- `IDENTITY_UNVERIFIED`, `INVALID_SCOPE`, `REVOKED_GRANT`,
  `STALE_ASSIGNMENT`: invalid or stale principals/source/leases/review.
- `NOT_CONFIGURED`, `NOT_SUPPORTED`: missing trusted ports, quota or atomic CAS.
- `BACKPRESSURE`: provider quota or backoff. Fixed-window quotas persist in
  SQLite and are independent across GitHub/Drive.
- `PROVIDER_UNKNOWN`/`PENDING_UNKNOWN`: timeout, crash, readback failure or
  ambiguous PR indexing; **no invented success**.
- `DOCUMENT_CONFLICT`, `IDEMPOTENCY_CONFLICT`: immutable readback differs,
  write precondition fails, or existing key payload conflicts. Dead-letter is
  retained for operator triage.
- `max_attempts=8` per intent, 1 hour capped exponential backoff;
  `max_artifact_bytes=16 MiB` by default. Provider readback can allocate a
  whole artifact into memory; large files need a future streamed digest port.
- No guarantee about live provider consistency, head approval, release
  authority or connector reachability without real integration gates.

## Local verification and external gates

Local tests exercise real SQLite transaction rollback and migrations via a
contract fake, injected GitHub/Drive transport operations, concurrency, lost
acknowledgements, timeout-after-effect, opaque idempotency conflicts, quotas,
readback mismatch and absence of unsafe PR retry. The root seed regression
suite must continue to pass. This does **not** verify real GitHub OAuth/GraphQL,
Drive update atomicity, actual head/QA permissions, source leases, or real
ChatGPT connectors; mark those **NOT_RUN** in the handoff until independently
observed. No source push, actual PR creation, or Drive mutation was attempted.

### `INTEGRATION_CHANGE_REQUEST`: trust authorization payload agreement

The frozen `trust.authorize` operation is present, but its exact payload and
positive response fields are **not defined** in the frozen seed. This module
issues `trust.authorize` as a **Component query** with payload
`resource`, `operation`, `bindings`, and `bindings_sha256` (SHA-256 of canonical
sorted JSON bindings). It requires `Outcome(state='ACCEPTED', code='AUTHORIZED')`.
WG-B/integrator must confirm or adapt this schema in the trust composition
facade before deployment; never substitute a local boolean trust mock. No
cross-package implementation imports are used. The local test trust Component
implements this one seam and is not proof of a real authenticated grant.
