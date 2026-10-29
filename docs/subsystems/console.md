# WG-O — Owner Fleet Dashboard / Manual Launch Console

## Scope and authority

Facade: `nomad_studio.console.api.build_component(deps: Mapping[str, object]) -> Component`.
Only Python standard library and frozen `nomad_studio.contracts.v1` imported.
The console does **not** authenticate owner tokens, start ChatGPT sessions, maintain a second jobs database, write sibling tables or call provider APIs. `VerifiedIdentity` is supplied by a real authenticated gateway; local scope checks and a fresh trust grant are mandatory for owner mutations. The dashboard is text/CLI-based; no misleading interactive web controls.

`deps` expected: `store` (frozen StateStore, required for control intents), `registry`, `trust`, `scheduler`, `organization`, `operations`, `verification` (all frozen Component handles); optional `clock_us: Callable[[], int]` for testing. If a port is missing/faulty, the affected facet reports `UNKNOWN/NOT_CONFIGURED` or `UNKNOWN/PROVIDER_UNKNOWN`, `availability=UNAVAILABLE`. No service is quietly replaced with in-memory authoritative state. Core fleet requires scheduler; optional cohorts via organization, metrics via verification/operations, exceptions via operations/organization.

## Owner-facing operations (`NOMAD/CMD/1` and `NOMAD/QUERY/1`)

All envelopes have `domain='console'`, operations **fully qualified** as below. Query max page size 100; cursors opaque (control-history cursor is `requested_at_us:uuid`). Unknown input fields are rejected with `INVALID_SCHEMA`. The gateway is expected to verify the identity before handing it to the facade. All read-only owner queries require `owner`, `owner:console:read` scopes and nonexpired `VerifiedIdentity`; owner commands additionally require the matching `owner:console:launch` or `owner:console:control` scope and an independent live `trust.authorize` grant matching current authority epoch. `identity.principal_id` is never accepted from user-provided payload.

| Operation | Kind | Closed payload | Result |
| --- | --- | --- | --- |
| `console.fleet` | Query | optional `department` (one of 12), `state` (READY/LEASED/SUSPECT/QA/UNKNOWN) | `FLEET_PAGE` with bounded `items`, `next_cursor`, counts, 12 cohorts, capacity violation flag, explicit unknown host capacity |
| `console.metrics` | Query | `{}` | hard admitted cap 500, design target 800, measured load receipt only, backpressure/availability |
| `console.exceptions` | Query | optional `kind` (`alerts`, `approvals`, `all`) | paginated alert delivery (SENT ≠ CONFIRMED) and open approvals; separate next cursors per facet |
| `console.actions` | Query (owner) | optional `worker_id` | keyset-paginated durable control intents; PENDING stays PENDING until shift poll observation |
| `console.actions` | Query (shift poll) | `view='pending_worker'`, `worker_id`, `assignment_epoch` | current-epoch PENDING intents, requires `shift:control:poll` grant and scopes |
| `console.launch_prompt` | Query | `worker_id`, `mode=START/RESUME`, optional `resume_ref` (RESUME only) | `PREVIEW_ONLY` registry template; no activation ticket and `copyable=false` |
| `console.launch_prompt` | Command (owner) | `worker_id`, `mode`, `assignment_epoch`, optional `resume_ref` | owner grant + `registry.prompt` + `trust.issue_launch`; requires authoritative trust receipt ID and returns copyable prompt only on one-use short-lived verified trust issuance |
| `console.actions` | Command (owner) | `action=DRAIN/PAUSE/STOP`, `worker_id`, `assignment_epoch`, `reason` | independently owner-granted, transactional `CONTROL_REQUESTED`; status PENDING |
| `console.actions` | Command (shift observe) | `action=OBSERVE`, `worker_id`, `assignment_epoch`, `intent_id`, `shift_id`, `receipt_id` | verifies shift observation authorization, transactional `CONTROL_OBSERVED_AT_POLL`; status OBSERVED, never host-running attested |

No automatic model launch. The owner manually opens a normal ChatGPT web chat and independently selects **High** in the UI. Text alone cannot attest the actual model setting. The short-lived activation reference is displayed *once to the owner* for manual copying, not logged to the SQL journal, printed to alerts, or included in durable control events. Never paste a long-lived owner, GitHub, Drive or provider secret into a prompt. The launch issuer must enforce short TTL, one use, slot/role/epoch binding, activation and revocation.

`Outcome.state` follows the frozen `ACCEPTED`, `DENIED`, `UNKNOWN`, `CONFLICT`, `BLOCKED`, `NOT_RUN` values. `Outcome.code` includes `FLEET_PAGE`, `FLEET_METRICS`, `PREVIEW_ONLY`, `MANUAL_LAUNCH_READY`, `CONTROL_RECORDED`, `CONTROL_OBSERVED`, `CONTROL_PAGE`, `PENDING_CONTROLS`, `EXCEPTION_PAGES`, or frozen negative codes. Read-only failure never silently returns an empty success page. Only evidence-backed 800-stream reports with exact SHA + receipt are shown as MEASURED_PASS/MEASURED_FAIL; missing real measurements show NOT_RUN. `model_chat_capacity` is UNKNOWN, and `model_ui_effort_host_attested=false` without a real host attestation channel.

## Frozen sister-component integration projections (explicit integration seams)

The frozen operation families do not specify response payload shapes. These payload shapes are WG-O's **requested adapter contracts**, not changes to siblings' private modules. The manual integrator may write composition-layer adapters or arrange sister component owners' existing payloads. WG-O does not import sister code or assume these adapters are already implemented.

| Required Component call | Input payload | Expected ACCEPTED output |
| --- | --- | --- |
| `scheduler.query(scheduler.ready)` | `view=fleet`, `department`, `state`; pass `cursor`, `limit` | `items` page of worker objects with `worker_id`, `department`, `state`, `source_sha`/`source_ref`/`artifact_ref`, `last_observation_us`, `observation_kind`, `shift_id`, `assignment_epoch`, `next_cursor`, `admitted_active`, `registered_total`; batch query, not N+1 |
| `scheduler.query(scheduler.ready)` | `view=capacity` | `admitted_active`, `registered_total` (single aggregate) |
| `organization.query(organization.leader_status)` | `view=department_cohorts` | `cohorts` bounded list of `{department,state,count}` across the fixed 12 department codes |
| `organization.query(organization.leader_status)` | `view=pending_owner_approvals`, cursor/limit | `items`: `{id,state,source_ref,decision_scope}`; `next_cursor` |
| `operations.query(operations.inspect)` | `view=alerts_with_delivery`, cursor/limit | `items`: `{id,state,source_ref,delivery_state,delivery_receipt}`; `next_cursor`; receipt cannot be forged by console |
| `operations.query(operations.inspect)` | `view=backpressure` | `status=OK/BACKPRESSURE/UNKNOWN`, `pending` integer |
| `verification.query(verification.acceptance_report)` | `view=load_800` | `tested_streams=800`, `verdict=PASS/FAIL`, 64-hex `source_sha`, nonempty `receipt_id` |
| `registry.query(registry.prompt)` | `worker_id,mode,resume_ref,assignment_epoch` | `prompt_text`, `role_id`, `approved_refs`, `assignment_epoch` (must match requested epoch) |
| `trust.execute(trust.authorize)` | `scope,worker_id,assignment_epoch,authority_epoch` | `authorized=true`, `grant_id`, `authority_epoch` matching verified identity epoch |
| `trust.execute(trust.issue_launch)` | `worker_id,role_id,mode,assignment_epoch,approved_refs,resume_ref,grant_id` | `version=NOMAD/LAUNCH/1`, `activation_reference` (one-use), `expires_at_us`, `one_use=true` |

If a sister service uses different projection fields, the integrator must reconcile at its *facade* boundary, not modify frozen contracts or import private implementation. `console.actions` polling requires WG-D Shift worker polling or trusted gateway to call console with current assignment epoch and shift `VerifiedIdentity`, then send OBSERVE when the control is actually seen at the poll boundary. Stale epochs denied by `trust.authorize` and intent match. Observation is **control delivery**, not evidence the model has terminated. The console records no model-running attestation. WG-M Operations owns alert dispatch/receipt; this console only presents observed delivery status. `console.exceptions` `kind=all` exposes two separately paginated facets; consumers should page each facet independently with `kind=alerts` or `kind=approvals`.

## Durability and schema

The Studio V1 initial schema creates namespaced `console_control_intents` with unique idempotency key, owner/verified grant/epochs, action, PENDING/OBSERVED, receipt/shift reference and indexed pagination. Schema identity is pinned and verified by the Studio V1 state owner. Runtime `store.transact` appends `DomainEvent(domain=console, kind=CONTROL_REQUESTED|CONTROL_OBSERVED_AT_POLL)` with SHA-256 of redacted event; no side effects inside store transaction. Durable authoritative event and SQL intent update occur atomically. All accepted control transitions return an immutable journal event ID in `Outcome.receipt_id`; successful issuance carries the trusted launch issuer's receipt ID. `idempotency_key` conflicting request digest returns `CONFLICT/IDEMPOTENCY_CONFLICT`. `PENDING` cannot become `OBSERVED` through owner UI; only the authenticated shift-observe command can record that polling occurred. Real worker compliance after observation is not inferred.

## CLI fallback

- `python -m nomad_studio.console --help` lists available **working** commands.
- Trusted composition calls `nomad_studio.console.cli.run_cli(argv, component=console_component, identity=gateway_verified_identity)` with a live service container and trusted owner/worker identity, never a forged identity supplied on CLI flags.
- `fleet [--department ... --state ...]`, `metrics`, `exceptions --kind alerts|approvals|all`, `actions --worker-id ...`, `preview WORKER`, `launch WORKER --epoch E --idempotency-key KEY`, `control STOP|DRAIN|PAUSE WORKER --epoch E --reason TEXT --idempotency-key KEY`; `--cursor` and `--limit` global flags before subcommand.
- Output JSON includes state/code/payload; exit 0 only for ACCEPTED, 2 for any nonacceptance. Standalone without trusted host returns `UNKNOWN/NOT_CONFIGURED` and exit 2. No disabled fake controls, no secrets/identity issuance inside CLI. Owner copies one-use launch prompt from authorized stdout and does not store it as an archive.

## Failure / external gates

`NOT_RUN`: real standard web ChatGPT connector activation; host-attested High selection, per-chat identity, real 500 chat UI concurrency; 200/500/800 service load benchmarking; GitHub/Drive artifacts; production owner auth/TLS/secrets; email/push delivery readback; end-to-end shift control delivery and worker STOP compliance. Unit CLI tests with fake ports do not prove these. A missing dependency is reported UNAVAILABLE, never silently a positive receipt. Runtime limits: max 100 workers per fleet page, 12 department summaries, 500 admitted ACTIVE cap from WG-E (console displays and flags upstream count violations, but does not enforce), fixed calls per page (scheduler once, organization once), 800 engineering target (not claimed achieved), max 32 approved refs, short-lived one-use tickets, bounds for IDs/text/cursors and keyset indexed control query.
