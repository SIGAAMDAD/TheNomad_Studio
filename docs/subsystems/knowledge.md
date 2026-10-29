# WG-S — Game Knowledge Canon and Context Retrieval

Contract: `NOMAD-STUDIO/GREENFIELD/FROZEN-CONTRACTS/1`, subsystem operations `knowledge.*`. Owns only `src/nomad_studio/knowledge/**`, `tests/knowledge/**` and this file. No legacy runtime, network client, provider credential, global composition root or separate database is shipped.

## Composition / integration seam

```python
from nomad_studio.knowledge.api import build_component
knowledge = build_component({
    'store': shared_state_store,                 # WG-A frozen StateStore
    'source': github_readback_component,         # Component.query source.resolve
    'artifacts': drive_readback_component,       # Component.query artifacts.proof
    'service_identity': trusted_service_identity, # trusted WG-B issuer, not a prompt
    'owner_principals': ['actual-owner-principal-id'],
})
```

A missing `store`, provider or authenticated service identity is an explicit `NOT_RUN/NOT_CONFIGURED` for the affected operation. Do not silently replace missing ports with in-memory runtime state. `owner_principals` is an integration-root-provisioned allowlist, empty by default; a chat/user's self-reported owner status is never authority. The gateway must authenticate every `VerifiedIdentity`, validate live grant/epoch and authorize each operation before dispatch. The knowledge component checks unexpired identity and current scopes, but does not itself authenticate transport credentials, verify current server revocation, or mint identities.

**Provider adapter requirement (INTEGRATION_CHANGE_REQUEST, no shared-ABI edit):** The current frozen cross-component `QueryEnvelope`/`Outcome` protocol names operations but does not fix a readback receipt payload. Both adapters must implement the following *knowledge-side payload convention* before live integration. No sister source is imported or changed here.

| Source | Frozen query operation | Required request | Required accepted proof |
| --- | --- | --- | --- |
| GitHub | `source.resolve` | `payload={"source": git_ref, "verify_current": true}` | `source`, `current_revision`, `content`, `content_sha256`, `classification`, `readback_id`, `verified: true`; optional `folder_ref` |
| Drive | `artifacts.proof` | `payload={"source": drive_ref, "verify_current": true}` | Same fields. Content must be the exact UTF-8 text of the revisioned document, not a synthesized summary. |

Providers are called with the *trusted service identity* granted `knowledge:provider:readback` by the integration root, not a worker-supplied principal. The `verified: true` claim must come from the independently authenticated provider adapter after current-revision readback, not from `CommandEnvelope.payload`. All readback occurs outside SQL transactions. Provider network fault/unknown => `UNKNOWN/PROVIDER_UNKNOWN`; missing/unconfigured provider => `NOT_RUN/NOT_CONFIGURED`; mismatched evidence digest => `UNKNOWN/SOURCE_SHA_MISMATCH`; changed current revision => `CONFLICT/REBASE_REQUIRED`. Git blob bytes are independently Git-object-hashed (SHA-1 or SHA-256, according to the blob length).

This seam remains **NOT_RUN on a real GitHub/Drive connector** until the integrator validates authenticated provider credentials, upstream revision semantics (especially Git `ref` branch heads), conditional readback and the exact reply schema. No network was contacted by WG-S tests.

## Source records / accepted schemas

All `CommandEnvelope` requests use `version=NOMAD/CMD/1`, `domain=knowledge`, `operation=knowledge.<verb>`, nonempty `request_id`, nonempty `idempotency_key`. Queries use `NOMAD/QUERY/1`, same domain and `knowledge.<verb>`. Unknown payload fields, invalid domain, unsafe Git path, unexpected major version and ill-typed fields fail closed. The component's operations do not accept caller-supplied document text or assertion of verification.

Git source ref (all fields mandatory):

```json
{"type":"git","repo":"owner/repo","path":"docs/design/notes.md","ref":"main","commit_sha":"<40/64 lowercase hex>","blob_sha":"<40/64 lowercase hex>"}
```

Drive source ref (all fields mandatory):

```json
{"type":"drive","file_id":"immutable-provider-file-id","revision_id":"exact-provider-revision-id"}
```

For Git `current_revision` equals `<commit_sha>:<blob_sha>` and must describe the **current checked-out object at the named ref**, not merely the historical object's existence. For Drive it equals the current provider revision ID. Returned source identity must exactly echo the requested source. Body bytes must hash to `content_sha256` and the hash must be lowercase 64-hex SHA-256. Trusted provider classification only `PUBLIC` or `INTERNAL`; Oversight and other classifications are rejected. Supplied folder reference must equal verified `folder_ref` if the provider emits one; canonical folder mapping is injected as data, never inferred from repo or department names. `readback_id` is stored as provenance.

Supported document domains: `design`, `narrative`, `gameplay`, `environment`, `fmod`, `art`, `qa`. No domain folder is hardcoded; `folder_ref` is a verified caller-supplied folder locator. Oversight folder/visibility input is rejected. Domain and access are recorded with every immutable source revision.

## Public operations and scope gates

| Method | Operation | Request payload (closed fields) | Key outcome |
| --- | --- | --- | --- |
| `execute` | `knowledge.index` | `source`, `domain`, `subject`, `folder_ref`, `roles`, `departments`, `jobs`, `read_scopes` | `ACCEPTED/INDEXED` and deterministic `record_id`; content and provenance from provider readback only |
| `execute` | `knowledge.accept_canon` | `subject`, `record_id`, `approval_reference` | `ACCEPTED/CANON_ACCEPTED`, new generation and revisioned owner audit; `CANON_UNCHANGED` for same current record |
| `execute` | `knowledge.invalidate` | `source` | Trusted latest revision readback; `INVALIDATED` or `UNCHANGED` and context generation invalidation |
| `query` | `knowledge.resolve` | `subject`; optional `include_history`, `role`, `department`, `job_id` | Authorized citations, historical acceptance generation and projection status; metadata is not a fresh live-proof certificate |
| `query` | `knowledge.context` | `role`, `department`, `domains`, `max_bytes`, `max_tokens`; optional `job_id`, `topic`, `max_items`, `include_references` | Live-readback-checked, scoped, deterministic `NOMAD/KNOWLEDGE-CONTEXT/1` export with digest, byte counter, exact citations and explicit `truncated` |

Required scopes: `knowledge:index`, `knowledge:invalidate`, `knowledge:accept_canon`, `knowledge:read`. Canon acceptance additionally requires `owner:canon` **and** `principal_id` in the externally provisioned owner allowlist. The owner command itself is the independent authenticated acceptance decision, recorded in history with actor/approval ref. An ordinary content author cannot self-approve lore, and a worker prompt never grants owner authority.

For `context`, scope must also include `knowledge:role:<role>` (or `knowledge:role:*`) and `knowledge:department:<department>` (or `knowledge:department:*`). If `job_id` is present, it must equal `QueryEnvelope.job_id` and require `knowledge:job:<id>` or `knowledge:job:*`. Each source revision's nonempty role/department/job lists further restrict visibility; all named `read_scopes` must also be present. `resolve` accepts those selector fields with equivalent checks. `include_history` additionally requires `knowledge:history`. `include_references=true` additionally requires `knowledge:reference:read` and labels every unapproved item `REFERENCE_UNAPPROVED` with no canon generation. Default context returns accepted canon only. These selectors select context; they are never elevated to identity by caller claims.

The context export includes `epoch`, `digest` (SHA-256 of canonicalized export excluding self-hash/counters), `entries`, `truncated`, `byte_count` and `token_upper_bound`. Every entry contains `subject`, `domain`, `authority`, `canon_generation`, verbatim snippet and citation with exact immutable provider ref, SHA-256, record ID and readback provenance. Snippets may be truncated and **are never synthesized**. The byte and model token *upper-bound* use deterministic JSON ASCII bytes; no exact model tokenizer usage is represented. The full serialized payload is checked against **both** budgets. A digest is for resume/launch provenance and never conveys write, review or owner approval powers.

## Durable StateStore schema

The `knowledge_*` tables are included in the pinned Studio V1 initial schema (`state/schema_v1.sql`). The knowledge module has no separate SQL installer or database owner.

| Table | Purpose |
| --- | --- |
| `knowledge_sources` | Immutable exact source revision records: source identity, subject/domain, UTF-8 bytes, SHA-256, provenance receipt, visibility, role/dept/job/scope filters and metadata hash. `UNIQUE(source_key,revision)`. |
| `knowledge_heads` | Latest independently observed revision and latest indexed source record, nullable on source divergence. |
| `knowledge_canon` | One current owner-selected record ID and generation per subject. |
| `knowledge_canon_history` | Append-only owner selection generations; retains superseded and reverted canon lineage. |
| `knowledge_exports` | Stable exported digest, included source and record IDs and `CURRENT`/`STALE` marker. |
| `knowledge_epochs` | Monotonic SQL generation; any content revision/canon change conservatively invalidates previous exports, including empty bundles. |
| `knowledge_idempotency` | Request fingerprint/receipt; prevents identical key with different actor/payload effects. |

Every accepted mutation (`index`, `accept_canon`, `invalidate`) emits `DomainEvent` via the frozen `StateTx.append_event` inside the same transaction, except `CANON_UNCHANGED`, which has no new mutation. Receipt payloads redact game text and include only hashes/references. Readback never runs inside a write transaction. Source revisions remain retained even after canon supersession. An attempted content or metadata mutation at the same revision is denied, not quietly overwritten. Replay returns the original accepted receipt even while the external provider is unavailable. Mutating SQL errors return `BLOCKED/STORE_FAILURE` rather than fabricating provider success.

## Errors and resource limits

- Source/provider outage: `UNKNOWN/PROVIDER_UNKNOWN`; adapter absence or reported `NOT_RUN`: `NOT_RUN/NOT_CONFIGURED`.
- Wrong version/schema/path: `DENIED/MALFORMED_REQUEST`, `DENIED/INVALID_SCHEMA`, `DENIED/INVALID_PATH`.
- Unauthorized source/role/job/owner access: `DENIED/INVALID_SCOPE` or `DENIED/OWNER_APPROVAL_REQUIRED`; unverified/expired identity: `DENIED/IDENTITY_UNVERIFIED`.
- Provider revision divergence or knowledge generation race: `CONFLICT/REBASE_REQUIRED`. Source content mismatch: `CONFLICT` or `UNKNOWN/SOURCE_SHA_MISMATCH` and quarantine of the current indexed record.
- Conflicting revision metadata: `CONFLICT/DOCUMENT_CONFLICT`; reused idempotency key with different fingerprint: `CONFLICT/IDEMPOTENCY_CONFLICT`.
- Oversized/malicious payload, oversized query document scan: `DENIED/BACKPRESSURE`.
- A source body cannot exceed 256 KiB UTF-8. Query scan maximum 512 source rows after domain filtering. Output max 64 KiB, max 16,384 conservative token units, max 24 entries. Input budgets must each be at least 512. Excess corpus requires a more selective domain/topic/canonical catalog ingestion or a separately reviewed paged query design; do not expand memory silently.
- Current bundle returns `REBASE_REQUIRED` if **any eligible matching record** is stale; no partially verified context is rebranded as canonical. A revision or owner-canon mutation bumps `knowledge_epochs` and invalidates historical exports. A concurrent change between readback and export persistence is fenced by checking that generation again inside the transaction.

## Events

`knowledge.indexed`, `knowledge.revision_observed`, `knowledge.canon_accepted` are represented as `DomainEvent(domain='knowledge', kind='indexed'|'revision_observed'|'canon_accepted')`, with deterministic event ID tied to request and idempotency key, real transaction-time timestamp, principal ID and redacted digest payload. No event represents a verified ChatGPT UI session or connector entitlement.

## Local verification and external gates

The isolated tests use Python `unittest`, real SQLite in memory **only as a fake frozen-port StateStore**, and independent fake readback Components. They exercise actual SQL migration parsing, transaction rollback, concurrent duplicate indexing, provider failures, stale revisions, scoped source access, owner-gated acceptance, unapproved reference isolation, deterministic digests, expired-generation conflict, content SHA tampering and outside-transaction readback. They do not prove WG-A's real single-writer runtime, WG-B's gateway issuer, actual GitHub/Drive revisions, deployment resource bounds or 500/800 logical worker capacity. Those remain `NOT_RUN` until manual integration with the actual sister systems and provider identities.
