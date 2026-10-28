# WG-D gateway admission — V1.6 manual High pilot

**State: local integration component; DO NOT DEPLOY until WG-A contract QA/approval, WG-C durable authority adapter, WG-G provider adapter and owner-gated cutover.** It does not contain a working privileged deployment configuration or an implicit broad-connector fallback.

## Trusted runtime boundary

`studio_v16.gateway.transport.serve` is the credential-isolated, mTLS-only JSON service. Deploy to a separate process/account with client-CA validation, explicit SHA-256 certificate fingerprint → principal-ID bindings and restricted file system/network access. Credentials, service private keys and any GitHub App PEM belong in a secret manager; do not pass them to chats, GitHub issue bodies or Drive. A shared owner GitHub connector is never principal identity. An absent or unknown client certificate is rejected before the gateway method runs.

Only a trusted deployment composition may construct `Gateway(authority=..., git=..., drive=..., audit=...)`. Default construction denies. `Authority.load` returns **server-sourced** verified owner grants/current revocation/lease/QA, not request-supplied facts. `Authority.reserve` MUST atomically reread the live grant/lease/owner/QA, check the immutable intent digest and idempotency key under a unique index, and reject stale state revision. WG-C must implement this; fixture fakes are not a database. The gateway records a `PREPARED` reservation, never performs a source/Drive write. WG-G obtains separate internal-only credentials, applies a provider compare-and-swap update on the specified noncanonical branch or document revision, and implements `GitSource.readback` / `DriveSource.readback`. `Gateway.reconcile` accepts only independently fetched readback and records verified effects through WG-C. Failed/absent readback remains pending; never assume API success means publication.

## Endpoints (mTLS client certificate required)

- `POST /v1/auth/evaluate`: `AUTH-V16/EVALUATE/1` object. Returns `AUTH-V16/DECISION/1`.
- `POST /v1/publish/prepare`: `{ "intent": <PUBLISH-V16/INTENT/1>, "artifact_base64": "..." }`; raw decoded artifact ≤16 MiB. Returns `PUBLISH-V16/READBACK/1` status `PREPARED` or `BLOCKED`.
- `POST /v1/publish/reconcile`: `{ "publication_id": "...", "idempotency_key": "64 hex" }`; returns `VERIFIED` **only** after exact provider readback and a successful durable reconcile, otherwise `EXTERNAL_OBSERVED`, `CONFLICT`, or `BLOCKED`.

The service never accepts client-supplied `observed` readback, worker-supplied credentials, unsigned QA, policy override, source tree listing, or owner approval. Gateway reference contracts are read from `studio/v16/schemas/` locally via JSON Schema; network schema fetching is disabled. `GitSource.inspect_patch` MUST inspect the actual supplied bytes, derive both sides of rename/delete, proposed tree manifest, checked Git modes and changed-path set. The provider's branch HEAD must equal the frozen base. WG-G is responsible for a final atomic branch ref CAS immediately before effect. Git writes may only update assigned `studio/workorders/...` branches. There is no canonical merge endpoint.

## Installation and secrets (not implemented in this workgroup)

Provision a dedicated GitHub App installation with `Contents: Read and write` scoped to explicit repository IDs, no admin or Actions/workflow permission. `studio_v16.gateway.credentials.GitHubAppCredentials` can mint repo-restricted installation tokens from a server-kept private key; the token never leaves the isolated provider process. GitHub provider writing is WG-G's responsibility. Do not deploy with owner PATs or expose a worker-accessible gateway hostname until the issuer, revocation, owner grant store, QA store, PostgreSQL journal, CI and network controls are operational and separately assessed.

## Run local checks

`python -m unittest discover -s tests/v16/gateway -p 'test_*.py' -v`

`python -m unittest discover -s tests/v16/contracts -p 'test_*.py' -v`

All tests use a deterministic fake trusted authority, Git snapshot/patch analyzer and Drive adapter; none authenticate against GitHub, Google Drive, PostgreSQL or real client mTLS certificates. Real permission and revocation negative tests remain deployment-gated.
