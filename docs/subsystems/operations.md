# WG-M — Operations: host watchdog, verified reports, alerts and recovery

**Authority:** `CONTRACT/OPS/1`, `CONTRACT/STATE/1`, `CONTRACT/SHIFT/1`; `NOMAD/CMD/1`, `NOMAD/QUERY/1`. This module uses the frozen `Component.execute/query` facade and the injected **single** `StateStore`. It never constructs a production SQLite connection or imports sister implementation. No GitHub push, ChatGPT model scheduling, independent worker attestation, or destructive deletion is implemented here.

## Public integration

`from nomad_studio.operations.api import build_component`

`build_component(deps: Mapping[str, object]) -> Component` requires:

| Key | Type / contract | Purpose |
| --- | --- | --- |
| `store` | Frozen `StateStore` | Single durable SQL authority; V1 initial schema already contains the `operations_*` tables |
| `observer` | `ObservationPort.snapshot(*, start_us, end_us, limit_per_category) -> Mapping[str, Sequence[Mapping]]` | Read-only, host-authenticated, evidence-backed projections of sister-state records; no worker-provided unverified liveness. Configured by integrator. |
| `effects` | `EffectPort.perform(action,case_id,request)` and `.readback(...)` | Host-controlled, policy-gated WAL checkpoint/restart and WG-L archive, WG-J publication reconciliation, WG-B/E fence receipts. Both calls are **outside SQL transactions**. |
| `alerts` | Same `EffectPort` protocol | Configured owner/department delivery channel; `readback` proves delivery, not just SMTP/HTTP invocation. |
| `clock` | `() -> aware datetime` | Trusted host time, injected for deterministic testing. |
| `limit_per_category` | integer 1–500, default 200 | Bounded per-category observation rows. |
| `stale_after_seconds` | integer 60–2,592,000, default 3600 | Classification threshold; **never** permission to evict a worker/lease. |

Every operations call requires an actual **gateway-minted** `VerifiedIdentity`, with valid `expires_at_us`, scope and epoch from the trusted server. This component does not authenticate wire input or re-check remote revocation; the gateway must re-evaluate live revocation/current epoch before dispatch, as in frozen architecture. A caller claiming a worker ID cannot create a verified identity.

`from nomad_studio.operations.runner import HostWatchdog` supplies a host-side scheduling loop. Instantiate with the operations `Component`, `identity_supplier` (trusted refreshed gateway principal, never a prompt-asserted credential), trusted clock, and `max_backfill_hours` (1–48, default 48). Call `.tick()` on a host scheduler, or `.run_forever(stop_event, poll_seconds=60)` in a host service. A tick recovers missing UTC hourly slots up to its bounded lookback, then emits eligible Chicago-local daily reports. It triggers owner alerts from *persisted* hourly anomalies and returns explicit outcomes. It has no ChatGPT launch capability. First boot can emit older hourly slots, based only on available verified historical data, and no longer-than-48-hour catchup is automatic.

## Closed command payloads and authorization

All command envelopes: `version=NOMAD/CMD/1`, `domain=operations`, nonempty idempotency key (max 256 chars), nonempty `request_id`. Unexpected fields in payload are rejected.

| `execute` operation | Payload | Required gateway scope | Effect |
| --- | --- | --- | --- |
| `hourly` | `{}` or `{"hour_start_utc":"YYYY-MM-DDTHH:00:00Z"}` | `operations:run` | For a **completed** UTC hour no older than 60 days, sample at most configured limit; immutably persist hash and audit event. |
| `daily` | `{}` or `{"local_date":"YYYY-MM-DD"}` | `operations:run` | When host time is at/after **23:00 America/Chicago** that date, assemble immutable report from already committed hourly records. Fall-back day has 24 hourly windows before 23:00; spring-forward day 22. Incomplete counts stay `PARTIAL`. |
| `alert` | `{"report_id":str,"target":"owner" \| "department:<name>"}` | `operations:alert` | Durable intent, adapter delivery and independent delivery readback. Missing route => persisted `BLOCKED/ALERT_UNDELIVERED`; absent readback => `UNKNOWN/ALERT_UNDELIVERED`. |
| `recovery` | `{"action":"wal_checkpoint" \| "backup" \| "restore" \| "host_restart" \| "reconcile_publication" \| "archive_events", "case_id":str, "expected_sha256"?:str, "report_id"?:str}` | `operations:recover`; additionally `operations:restore` for restore and `operations:restart` for host restart | Durable intent, trusted external effect, exact readback, append final event; unknown outcomes never blindly retried. `restore` requires exact 64-character lowercase SHA256. `archive_events` requires `report_id`. |
| `recovery` | `{"action":"replace_worker","case_id":str,"worker_id":str,"fenced_epoch":int,"fence_ref":str}` | `operations:recover` **and** `operations:replace` | Readback-only verification that **both old sessions are revoked and old leases are fenced**, matching worker, new epoch, and evidence reference. Emits `OWNER_LAUNCH_REQUIRED` owner action case, **no reusable launch token** and no lease release. Actual owner-controlled one-use launch issued later by WG-B. |
| `recovery` | `{"action":"reconcile_case","case_id":str}` | `operations:recover`, plus `operations:restore`/`operations:alert` for original class where needed | For previously unknown pending effects, perform **readback only**, not a repeat side-effect. Exact corroborating provider receipt resolves case as `RECONCILED_VERIFIED`. |

`case_id` is 1–120 characters `[A-Za-z0-9._:-]`; a different idempotency key may not dispatch the same case twice. Some known `BLOCKED` outcomes require a fresh approved case to retry; only `PENDING_UNKNOWN` and `UNKNOWN` cases are eligible for `reconcile_case`.

Queries: `QueryEnvelope(version="NOMAD/QUERY/1", domain="operations", operation=...)`, all require `operations:read`, limit 1–500. `hourly`/`daily` accept optional `report_id`, otherwise descending window order; `inspect` takes optional `kind` (`alert`,`recovery`,`replacement`) and `state`; `recovery` takes optional `case_id`; `health` has empty payload and returns next 23:00 cutoff, port configuration, `NOT_MEASURED` measurements when no source receipt. Query `operation` and all payload fields are closed and reject unknown keys.

## Evidence projection schema and deterministic classification

The integrator must bind `observer` to read-only authenticated StateStore projections, or to properly authenticated sister Component queries converted by a host adapter. The result is a mapping of zero or more of these exact categories: `workers`, `jobs`, `leaders`, `leases`, `outbox`, `relay`, `recoveries`, `backups`. An absent category is **NOT_MEASURED**, not an empty observation. Each list must be capped at `limit_per_category` and each counting row must have `verified is True` and nonempty `evidence_ref`. Unverified rows are not included in counts; hitting limit sets `CATEGORY:LIMIT_REACHED`, never interpreted as global totals. All timestamps are microseconds from Unix epoch, produced by trusted host/state.

Recognized fields (in addition to `id`, `verified`, `evidence_ref`):

| Category | Optional fields used | Checks |
| --- | --- | --- |
| `workers` | `last_observed_at_us` | Missing/future last observation => `NOT_OBSERVED`; older than threshold at report cutoff => `SUSPECT`; else `OBSERVED`. Never claim `CRASHED`, release a write lease, revoke a shift, or appoint a replacement as a consequence of this classification. |
| `jobs` | `status`, `enqueued_at_us` | Oldest queued age for `APPROVED`/`READY` jobs, if verified. |
| `leaders` | `state` | `MISSING`, `SUSPECT`, `UNOBSERVED` => escalation evidence. Includes director and head records as host adapter provides. |
| `leases` | `state` | `COLLISION`, `EXPIRED_UNFENCED` => review, no release. |
| `outbox` | `state` | `PENDING_UNKNOWN`, `CONFLICT` => provider readback needed. |
| `relay` | `state` | `ACK_OVERDUE`, `CURSOR_STALLED` => relay review. |
| `recoveries` | `state` | `UNKNOWN`, `BLOCKED` => recovery review. |
| `backups` | `state` | `FAILED`, `UNVERIFIED` => backup review. |

Report JSON is canonicalized (`sort_keys`, compact separators, `allow_nan=False`), SHA-256 hashed, persisted once per slot, and linked to an appended domain audit event. Repeated slots are read back, never regenerated with newer evidence. Cost, effort and token usage are `NOT_MEASURED` because no authenticated metering source is integrated. Hourly reports are partial projections; `LIMIT_REACHED` and missing categories explicitly signal incomplete coverage. Daily aggregates use only existing recorded SHA-256 hourly digests and count their anomaly records; no present-day worker claims can rewrite history.

## External effect rules and recovery safety

WG-M stores `PENDING_UNKNOWN` intent before invoking any effect. `perform` is not proof; it must be followed by `readback` with `verified=True`, nonempty trusted `evidence_ref` and action-specific corroboration. On crash after the intent but before readback, the case remains `PENDING_UNKNOWN`; `reconcile_case` consults independent readback. Provider errors => `UNKNOWN`, no automatic redispatch. Alert delivery additionally requires a verified `provider_message_id` and `delivered=True`.

**Restore has TWO explicit proof gates:** first, before any `perform`, call `effects.readback(action="restore_preflight", ...)` requiring authenticated `offline_confirmed`, `sealed_archive_sha256` matching requested hash and an evidence ref. The integrator's host adapter must acquire and hold a maintenance fence while performing restore (race protection is adapter responsibility). Then `effects.readback(action="restore",...)` must attest exact restored database SHA-256, independent restore verification and offline mode. The only source of actual database backup/restore is WG-A/WG-L through the integrator-provided effect adapter. This module does not manipulate `.sqlite` files itself or claim to verify bytes inside a fake archive port. Backup requires independently restored checksum evidence. WAL checkpoint requires verified `wal_checkpoint_ok`; host restart requires verified generation increase; publication reconciliation is **readback-only**, never republishes; `archive_events` submits to WG-L and requires `archive_sealed=True`—no event deletion. Off-host copying is the WG-L adapter's responsibility and must be proved by its own witnessed readback.

## Events and durable schema

The pinned Studio V1 initial schema owns the `operations_*` table names:

- `operations_receipts`: idempotency key, payload/identity/epoch digest, durable result state and receipt ID.
- `operations_reports`: unique immutable report slot, kind, [start,end), UTC microseconds, SHA-256 of canonical JSON and audit event ID.
- `operations_actions`: unique case ID, kind (`alert`,`recovery`,`replacement`), action, pending/final state, expected and observed SHA-256, redacted evidence reference, result, timestamps and audit event ID.

Indexes: reports `(kind,period_start_us)`, cases `(kind,state,updated_at_us)`; bounded queries never `SELECT *` without `LIMIT` except exact-key lookups and transactions. Report/events are append-only and **never deleted** by this module. Provider payloads and confidential Oversight case text are not saved. Events through `StateTx.append_event`: `HOURLY_RECORDED`, `DAILY_RECORDED`, `EFFECT_RESERVED`, `EFFECT_ACCEPTED`, `EFFECT_BLOCKED`, `EFFECT_UNKNOWN`, `EFFECT_RECONCILED` (the latter checks readback). Each includes correlation, actor, canonical redacted payload digest and receipt reference.

## Failure semantics / bounded runtime

`ACCEPTED` means persisted report or externally corroborated outcome; `DENIED` means malformed/unauthorized; `CONFLICT/IDEMPOTENCY_CONFLICT` prevents duplicate effects; `BLOCKED` covers missing configured ports and failed verified preconditions; `UNKNOWN/PROVIDER_UNKNOWN` covers uncertain provider/storage state. No raw provider exception, token, secret or private case material is included in Outcome. The host runner is synchronous, bounded to at most 48 UTC hourly slots and two daily dates per tick; actual host uptime/scheduler cadence is deployment-owned. Current SQL transaction APIs are exactly the frozen `StateStore.read/transact` ports. Query caps are 500; no cross-component write transaction is attempted.

## Unfulfilled external integration gates (NOT_RUN)

- Authenticate and exercise a real trusted gateway / tool / ordinary ChatGPT connector path; no credential, host, or connector access is bundled.
- Wire the single Studio V1 production writer; verify real `sqlite3` WAL/checkpoint/backup under single-owner host startup and crash-injection.
- Bind WG-B/WG-E epoch fencing and live lease/session proofs, WG-C/D actual shift/relay evidence, WG-G leader status, WG-J publication readback, WG-L archive compressed database restore and independent off-host copy, WG-R authentic cost telemetry if desired.
- Verify production alert recipient, provider routing, delivery ID and readback; no addresses/credentials bundled.
- Verify deployment host restart, off-host archive media, actual backup bytes and independent database integrity proof.
- Run cumulative 500 admitted / 800 synthetic streams through fully integrated host/SQL/gateway and independent QA/merge on final integrated SHA (WG-Q/integrator responsibility).

Unit tests use actual in-memory SQLite to exercise WG-M SQL and rollback, but all outside-module ports are explicit fakes. Their green result is **local implementation evidence**, not proof of these production gates.
