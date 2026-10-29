# Budget — WG-R / S52–S54 / `NOMAD/BUDGET/1`

## Ownership and limitations

`src/nomad_studio/budget/api.py` exports `build_component(deps: Mapping[str, object]) -> Component`. Its only imports outside the Python standard library are the frozen `nomad_studio.contracts.v1` types. The component owns budget grants, reservations, measured evidence, provider request attempt budgets, and exception reporting. It does **not** mint VerifiedIdentity, start model/web-chat sessions, issue owner approval, open SQLite connections, dispatch GitHub/Drive requests, or install migrations. The owner must manually open any ChatGPT web chat; a unit reservation is an authorization decision, not proof of model work or billing.

### Integration dependencies (provided by composition root)

| Injection | Contract / trust | If unavailable |
| --- | --- | --- |
| `deps['store']` **required** | Shared WG-A `StateStore` with transactional `StateTx` and `read`. The pinned V1 initial schema creates the `budget_*` tables before constructing this facade. SQL access uses only store callbacks. | Constructor raises `ValueError`. Do not instantiate a second SQLite owner. |
| `deps['clock_us']` optional | Callable returning trusted Unix microseconds; defaults to Python wall-clock. For acceptance tests inject deterministic clock; for production use trusted host service time. | Invalid clock raises `ValueError`. |
| `deps['billing_verifier']` optional | Callable `(source_ref: str, identity: VerifiedIdentity) -> Mapping`. Trusted, independently read billing receipts / provider response headers. Called **outside** database transactions. No user-supplied observed cost becomes a real charge. | `budget.measure` for `provider_charge`/`provider_rate` returns `NOT_RUN/NOT_CONFIGURED`; missing or invalid readback returns `BLOCKED/PROVIDER_UNKNOWN`. Estimates/manual reports still work without the adapter. |

WG-B/gateway supplies **server-minted** `VerifiedIdentity` with up-to-date credential, scopes, expiration, and current authority epoch. The facade checks expiry and scope but cannot independently attest the gateway. Revocation of principals/authority epochs is WG-B's responsibility; revocation of *budget grants* is implemented here. A shared web-chat identity cannot prove which ChatGPT chat performed work. The scheduler (WG-E), relay (WG-C), publication (WG-J), console (WG-O), and their authenticated gateway callers consume this component *only through `Component.execute/query`*. Call `budget.reserve` before admitting budgeted work or a provider API attempt. `budget.query(..., kind='evaluate')` is advisory; reserve is the definitive serialized check. Resource/worker admission and source/QA ownership remain the responsibilities of their respective components.

## Operations and permissions

All commands are `CommandEnvelope(version='NOMAD/CMD/1', domain='budget', operation=..., idempotency_key=nonempty, payload=...)`, with a trusted `VerifiedIdentity`. Every accepted mutation appends a `DomainEvent(domain='budget', ...)` in the same transaction and returns its event ID as `Outcome.receipt_id`. Every command is idempotent by `(idempotency_key, principal_id, operation, canonical payload SHA-256)`; reusing a key for a different request returns `CONFLICT/IDEMPOTENCY_CONFLICT`. Client `actor_claim` and `worker_id` are **never authorization evidence**.

| Operation | Required gateway-issued scope | Purpose | Successful code |
| --- | --- | --- | --- |
| `budget.grant` | `budget:owner` | Owner creates/revises scoped allocation with optimistic revision CAS | `GRANT_REVISED` |
| `budget.revoke` | `budget:owner` | Permanently fence new allocations; preserve existing ledger and receipts | `GRANT_REVOKED` |
| `budget.quota` | `budget:owner` | Configure/update GitHub/Drive provider rate/retry limits, CAS revision | `QUOTA_REVISED` |
| `budget.reserve` | `budget:reserve` **trusted service**, `budget:reserve:<grant_id>` **bounded delegation**, or `budget:owner` | Atomic unit and cost hold, enforce all six scope coordinates and provider retry/rate caps | `RESOURCE_RESERVED` |
| `budget.reconcile` | `budget:reconcile` or `budget:owner` | `RELEASE` hold, `SETTLE` used units, or `AUDIT` ledger invariants | `RESOURCE_RELEASED`, `RESOURCE_PENDING_EVIDENCE`, `RESOURCE_SETTLED`, `LEDGER_OK` |
| `budget.measure` | `budget:measure` or `budget:owner` | Record trusted billing/rate receipt or explicitly unmeasured / estimated usage | `PROVIDER_CHARGE_MEASURED`, `PROVIDER_RATE_OBSERVED`, `COST_ESTIMATED`, `NOT_MEASURED` |
| `budget.query('quota')` | `budget:read` or `budget:owner` | Bounded read projections, including exception reports | `QUERY_OK`, `AVAILABLE`, `BUDGET_DENIED` |

Scopes `budget:owner`, `budget:reserve` and `budget:measure` must **never** be minted merely from a worker prompt, GitHub issue label, or unauthenticated user data. `budget:reserve:<grant_id>` permits only that envelope; a worker-specific grant must also match the six scope coordinates. Any global `budget:reserve` permission is appropriate only for the trusted scheduler/publication gateway, not broad worker prompts.

## Closed payload schemas

All fields listed below are mandatory except those explicitly labeled optional. Unknown payload keys are rejected with `INVALID_SCHEMA`. UUID-style or URL-safe identifiers must match `[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}`. Unit counts, cost amounts (integer minor units), times (Unix microseconds) and revisions are bounded nonnegative integers (<= 2^60); booleans and floats are rejected. `currency` is three uppercase letters. No floating-point billing arithmetic or invented dollar conversions occur.

**`grant`**: `grant_id`, `feature_id`, `department_id`, `worker_slot_id`, `model_effort`, `resource_class`, `provider`, `unit_limit`, `cost_limit_minor`, `currency`, `valid_from_us`, `expires_at_us`. Optional `expected_revision` **required when revising**; absent on create. `model_effort` is `instant|medium|high|extra_high|pro|none`; `resource_class` is `ci|github_api|drive_api|storage|manual_chat|model|other`. Scope feature/department/slot and provider allow `*` (explicit owner-approved wildcard); provider must be `github` for `github_api`, `drive` for `drive_api`. Scope coordinates and currency cannot change on amendment; limits cannot be lowered below committed/reserved balances. Grant revision increases on creation, amendment, reservation, reservation reconciliation, verified charge accounting, and revocation; owner updates must use the latest revision. Revoked grants cannot be reactivated under the same ID.

**`reserve`**: `grant_id`, `reservation_id`, `request_key`, `job_id`, `units` (>0), `cost_ceiling_minor`, `expires_at_us`, and all six scope coordinates (`feature_id`, `department_id`, `worker_slot_id`, `model_effort`, `resource_class`, `provider`). Optional `attempt` (default 0). Reserve must match grant scope (owner wildcards allowed), current validity window and both available unit/cost envelope limits. `manual_chat` must have zero `cost_ceiling_minor` because subscription work is not independently metered. Provider API calls require configured quota, bounded sequential attempts `0..max_retries`, unique provider/request-key/attempt, and a prior `RELEASED` attempt before retry; count accepted attempts in a moving `window_us` and enforce observed rate-header cooldown. Each accepted provider attempt consumes the window even if its reservation is later released. `BACKPRESSURE` means the caller must wait/escalate instead of polling without limits. Retrying a request after a provider reset uses a **new idempotency key** for a new legitimate attempt; repeat of an existing accepted key replays the original receipt.

**`reconcile`** has three variants:
- `{'action':'RELEASE','reservation_id':...}` returns all held units and cost (the caller must establish that the external effect did not occur).
- `{'action':'SETTLE','reservation_id':...,'used_units':integer}` moves `used_units<=reserved units` to consumed, releases unused units, and retains the reserved cost ceiling until independently observed charge evidence; status `PENDING_EVIDENCE`, or `SETTLED` if the cost ceiling was zero.
- `{'action':'AUDIT','grant_id':...}` transactionally recomputes unit/cost ledger aggregates from reservations and observed charge evidence and detects corrupted totals, overruns, or cost above reservation ceiling. Returns `BLOCKED/LEDGER_CONTRADICTION` plus durable exception on mismatch. A unit hold will not silently expire or be reclaimed if the external provider outcome is unknown; explicit outcome-aware reconciliation is required.

**`quota`**: `provider` (`github|drive`), `window_us` (>0), `max_requests`, `max_retries`, optional `expected_revision` on update. A policy revision does not erase previously counted provider requests. Set `max_requests=0` to block new API attempts. A verified rate observation with `remaining_requests=0` enforces `cooldown_until_us` no earlier than the verified `reset_at_us`.

**`revoke`**: `grant_id`, `expected_revision`. Revocation prevents new reservations but does not erase already accepted reservations or measurement/exception history. Existing reservations can be reconciled after revocation.

**`measure`**: `grant_id`, `evidence_id`, `kind` (`provider_charge|provider_rate|estimate|manual_web`), `source_ref`; optional `reservation_id`, `estimate_cost_minor`, `currency`, `note`. For `estimate`, both `estimate_cost_minor` and `currency` are required. For `manual_web`, actual cost remains NULL and status `NOT_MEASURED`. For provider kinds, payload cannot supply costs, currency or rate fields; the `billing_verifier` must read them independently. One canonical provider source reference can be charged/observed only once for its kind, even with a different evidence ID or grant. Measured charges attach to `PENDING_EVIDENCE` reservations when specified, settle reserved cost ceilings, and add *actual observed* minor-unit charges to the ledger. Verified charges exceeding a ceiling/owner limit are **recorded truthfully** with a `LEDGER_CONTRADICTION` exception and block new allocation. A charge without an attached reservation is recorded and accounted for as external evidence without inventing a prior reservation.

Trusted provider observation adapter return (exact keys, no extras):

```python
# provider_charge
{'provider': 'openai_api', 'evidence_ref': 'canonical-provider-line-id',
 'source_kind': 'billing_receipt', 'source_sha256': 'a'*64,
 'observed_at_us': 1800000000000000, 'actual_cost_minor': 120,
 'currency': 'USD'}
# provider_rate
{'provider': 'github', 'evidence_ref': 'canonical-provider-header-id',
 'source_kind': 'rate_header', 'source_sha256': 'b'*64,
 'observed_at_us': 1800000000000000, 'requests_observed': 1,
 'remaining_requests': 0, 'reset_at_us': 1800000060000000}
```

The *adapter*, not the caller, must authenticate provider response provenance, verify source contents/digest and canonical line identity, and return the **independently verified** values. An unavailable adapter does not authorize `MEASURED`. `source_sha256` must represent actual external evidence, not a digest of an untrusted prompt. The fake adapter in tests is a simulation; no real connector or invoice was reached.

**`query`**: `QueryEnvelope(version='NOMAD/QUERY/1', domain='budget', operation='quota', payload={'kind': ...}, limit=1..200, cursor=nonnegative decimal offset string or None)`. `kind` may be `grants|reservations|policies|evidence|exceptions` with optional `grant_id` / `provider` filters where supported, returning `items` and `next_cursor`. Or `kind='evaluate'` with `grant_id`, `units`, `cost_ceiling_minor`, returning *advisory only* `available_units`, `available_cost_minor`, `grant_revision`. Always call `reserve` for an enforceable decision.

## Schema, events, errors and operational behavior

The pinned Studio V1 initial schema contains `budget_grants`, `budget_reservations`, `budget_provider_quotas`, `budget_provider_attempts`, `budget_evidence`, `budget_exceptions`, `budget_effects`. Foreign keys are enforced by WG-A's central SQLite connection policy. Provider evidence lines have a unique index for `(kind, source_ref)`. No secrets, permanent tokens, raw Oversight notices, or provider credentials are stored. Source refs/hashes, provider observations and independently derived audit events remain durable. `budget_effects` contains exact replay outcomes; **do not clear it** while clients can retry old commands. Safe operational purge/archival requires independent owner retention policy.

Events: `GRANT_REVISED`, `GRANT_REVOKED`, `QUOTA_REVISED`, `RESOURCE_RESERVED`, `RESOURCE_RELEASED`, `RESOURCE_SETTLED`, `RESOURCE_PENDING_EVIDENCE`, `LEDGER_AUDITED`, `EVIDENCE_MEASURED`, `EVIDENCE_RATE_OBSERVED`, `EVIDENCE_ESTIMATED`, `EVIDENCE_NOT_MEASURED`, `EXCEPTION`. Each `DomainEvent` has the frozen envelope fields, request correlation, principal, trusted wall-clock, a payload digest, and a minimal redacted accounting payload.

Denials/blocks include `INVALID_SCHEMA`, `MALFORMED_REQUEST`, `IDENTITY_UNVERIFIED`, `OWNER_APPROVAL_REQUIRED`, `BUDGET_DENIED`, `INVALID_SCOPE`, `EXPIRED_GRANT`, `REVOKED_GRANT`, `BACKPRESSURE`, `PROVIDER_UNKNOWN`, `NOT_CONFIGURED`, `STALE_ASSIGNMENT`, `STALE_LEASE`, `LEDGER_CONTRADICTION`, `IDEMPOTENCY_CONFLICT`. Expired grant, revoked grant, quota exhaustion, budget exhaustion and ledger contradictions are recorded in the exception table and as audit events when encountered inside a permitted transaction. Actual provider failures are never reported as provider success. SQLite/provider programming errors propagate for WG-A to roll back and the caller to surface as service faults; they are not silently mapped to ACCEPTED.

### Resource and observability limits

- `max_requests` governs accepted GitHub/Drive request attempts; it does **not** claim control of the provider's real external quota or the owner's actual subscription charges.
- Absolute unit and cost sums are protected against signed integer overflow at the input validation boundary; all cost arithmetic uses integer minor units in a single grant currency.
- Concurrent reservations are serialized by WG-A's StateStore transaction implementation. WG-R never uses a second database connection.
- Time-window attempt counting requires index `idx_budget_provider_attempts_time` and depends on WG-A's serialized writer. For very long-lived services, retention/archival policy must be provided by the owner so attempts do not grow indefinitely.
- This module makes no network requests, schedules no chat execution and performs no external writes. Any use of `budget.reserve` to gate an external effect still requires the caller to handle provider-unknown and reconcile safely.

## Local verification and external gates

Unit tests execute real SQLite SQL via a serialized fake `StateStore`, assert 50 competing reservations yield exactly 10 accepted against a 100-unit cap, and a separate 50-attempt provider burst admits exactly 5 against a 5-request window, and fault-inject SQL/event failures to prove rollback. They test owner forgery, scopes, revision CAS, provider retry exhaustion/429-style cooldown, measured vs estimated/manual usage, duplicated source evidence, over-budget billing, audit corruption, expiry, and revocation. The frozen contract test suite is also run. These are local tests; WG-Q's 500/800 synthetic-stream integrated performance acceptance, WG-B's live principal/epoch checks, real GitHub/Drive/billing receipt readback, production deployment, and manual ChatGPT connector identity are **NOT_RUN** until integrated with the other components and real credentials.

### Studio V1 SQL and wheel packaging

The complete `budget_*` schema is installed from the single pinned
`nomad_studio/state/schema_v1.sql`, included in the Studio V1 wheel. No module
migration file, redundant embedded SQL copy or development database upgrade is
required. The installed-wheel smoke test checks the schema resource and fresh
SQLite initialization.
