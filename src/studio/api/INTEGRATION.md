# WG-J: frozen HTTP API and integration boundary

All source in this directory is greenfield R5 and owned by WG-J. `FrozenContracts` loads **the untouched** `CONTRACTS/openapi.json`, `operation_catalog.json` and `operation_semantics.json` at service startup. It verifies catalog/OpenAPI operation identity, method, path, owner, authentication class, mutation flag, request field types and response field types. All **72** routes are compiled from those files. A mismatched contract fails startup; a missing owner implementation returns a typed HTTP 503.

## Host composition (WG-M)

Compose `FrozenContracts(path_to_contracts)`, a configured `IdentityVerifier`, a `ProjectGrantsReader` backed by the current WG-A/WG-C project grants, `DomainRegistry`, an APSW-backed `AtomicMutationRunner`, and `StudioOperatorService`. Register each A/B/D/E/F/G/I/M domain implementation under its exact owner code, and `EventPageService` under `J`. Wrap with `StudioWSGI(service)` and put behind the configured production HTTP/TLS gateway. `create_http_server` is a working loopback stdlib HTTP server useful for smoke tests; WG-M owns deployment and production networking.

Provision trusted principals through `PrincipalProvisioner(verifier, C_principal_store)` at bootstrap from **configured verified credentials**; C's scoped named writer must enforce immutable principal_id / auth_issuer / auth_subject / kind identity. This provisioning is intentionally not an OpenAPI route. The opaque bearer verifier supports preconfigured high-entropy token SHA256 identifiers and verified owner bits. A real issuer-backed verifier can be injected in its place. The request body, headers other than the bearer proof, GitHub relay claims and role strings cannot grant ownership.

`StudioOperatorService` calls `ProjectGrantsReader.granted_project_ids` on **each** request. The store must read current persisted per-project grants (including revocations). Owner-only operations also require verified human-owner identity. Domain handlers still enforce exact subject/project ownership and any required owner policy/decision; the HTTP router cannot replace the WG-A/C/G authority and external-effect checks.

## Domain port wiring

For each operation, call `DomainRegistry.register_owner(owner_code, service)` where the service implements the corresponding exact `StudioApiPort` name/arguments. All successful responses and idempotent replay responses are validated against the frozen response schema. `DomainRegistry.missing(contracts)` lists unbound operations. During the one-wave local authoring, missing domains are `capability_unavailable`, **never mocked success**.

For every HTTP mutation (including `createProject`), J calculates the idempotency payload digest from authenticated principal, operation, concrete path params and validated JSON body. C must provide `execute_idempotent(owner, project_id, principal_id, request_id, payload_digest, operation_id, command)` and implement the frozen `(project_id, action_owner, request_id)` key or the `(principal_id, request_id)` bootstrap key **transactionally**. Exact retries replay the original response; changed digest, owner/operation/principal or path must conflict; UNKNOWN external effects must not be blindly retried. The command callback must execute under C's scoped ownership guard. External effects are journaled in the domain/C outbox before publication through G, never silently performed from request handling without durable intent and readback.

`EventPageService.listEvents` requires WG-C's `AuditEventReader.scan_project_events(project_id, after, limit)`, backed by `audit_events` with the frozen index `idx_events_cursor`. Query must select only committed rows for the exact project using `WHERE project_id=? AND event_seq>? ORDER BY event_seq ASC LIMIT ?`, with bound values and no full-table scan. It accepts `after>=0`, `1<=limit<=500`, default `(after=0,limit=100)`, guards returned order/project, and returns durable `next_cursor` equal to the last event_seq (or the incoming cursor when empty). GitHub observations are included only after G/I verified projection into C's audit table.

The API does **not** connect to model inference APIs, create or resume ChatGPT browser conversations, or claim STOP took effect. WG-I handles the prepare-prompt/STOP relay semantics; WG-G handles GitHub real effects. Do not replace those with network/browser API calls in J.

## Dependency decision (REQ-037; J scope)

J uses **Python 3 standard library only** for schema subset validation, WSGI HTTP, bearer credential hashing/comparison and route compilation. That avoids adding FastAPI, Pydantic, jsonschema or a separate web framework to WG-M's shared `pyproject.toml`, which WG-J does not own. The frozen schema keywords are explicitly checked; a future incompatible schema fails startup, rather than silently dropping constraints. WG-C's APSW driver is required for actual persistence and not imported by J. A production WSGI host/TLS gateway choice remains WG-M's deployment work and must receive its own pinned dependency/configuration decision.

## Evidence and deferred seams

`tests/unit/j/test_api.py` runs all 72 operation shapes, HTTP loopback, isolation, owner spoofing, digest-bound replay (using a unit-only transaction simulator), missing owner/transaction failure, response validation, and paginated events. The unit simulator is **not** an APSW integration PASS. Tests needing C's APSW implementation, A/B/D/E/F/G/I/M business components, live GitHub, browser ChatGPT and external owner actions remain `NOT_RUN` until integrated by M and independently assessed by L. No historic Studio schema or compatibility code is present.
