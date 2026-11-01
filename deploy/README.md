# R5R deployment scaffold and operating requirements

`deploy/compose.yaml` builds the Python coordinator and a production React cockpit behind a Caddy TLS reverse proxy. The proxy shares the coordinator network namespace, so the Python API remains loopback-only. The stack does not install an owner-authorized trusted ports implementation or a C# ToolHost broker. Compose defaults to `restart: "no"`.

## Required environment

The CLI executes `python -m studio.app.cli`. Supply an installed trusted `module:function` factory returning `TrustedPorts` for the complete R5R service. See [`deploy/studio.env.example`](studio.env.example) for the actual variable names and [`docs/SETUP.md`](../docs/SETUP.md) for the setup sequence.

- `STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment`
- `STUDIO_TRUSTED_PORTS_FACTORY=<installed.module>:<factory>`
- `STUDIO_APSW_DATABASE`, `STUDIO_BLOB_ROOT`, `STUDIO_SOURCE_ROOT` on durable, writable storage.
- `STUDIO_CONTRACT_DIR` pointing to the verified frozen `CONTRACTS/` directory.
- `STUDIO_OPERATOR_HOST=127.0.0.1` (loopback only) and a reviewed TLS proxy for remote operator access.

The factory must bind all 72 operator routes, owner/project authorization, GitHub source and command delivery, receipt verification, independent QA, releases and restart recovery. Missing inputs intentionally fail startup; a bare green build is not a running Studio.

## Image packaging

The Docker image packages `src/`, `CONTRACTS/` and `schema/` with the Python coordinator. Installed wheels include the frozen schema files under the environment's `share/nomad-studio/schema/`; the schema loader verifies the frozen digest. CI validates these resources and the complete 72-operation API contract. Bootstrap a new volume before first startup using `docker compose -f deploy/compose.yaml run --rm --entrypoint python studio /studio/scripts/bootstrap_studio_database.py --database /var/lib/nomad-studio/studio.sqlite3`. This refuses to overwrite an existing database. A trusted ports provider and integration startup acceptance remain deployment responsibilities.

Compose supplies persistent volumes and reads the required paths from the private `deploy/studio.env`. The mounted trusted factory and its authorized credentials must still exist before startup; the Compose file alone is not a complete external-provider acceptance gate.

## Validation before launch

Run source CI for the exact candidate, the Python/APSW integration suite, 72-route startup check, authenticated GitHub effect readback, failure/restart tests, complete ToolHost sandbox checks on the actual target OS, and live F-20 browser continuation. See [Owner acceptance](../docs/OWNER_SETUP.md), [Browser setup](../docs/CHATGPT_SETUP.md), and [ToolHost](../host/README.md).

## Cockpit and encrypted ingress

1. Put the trusted Python provider module implementing `TrustedPorts` in `deploy/providers/`, and set `STUDIO_TRUSTED_PORTS_FACTORY` to its actual `module:function` identity in the untracked `deploy/studio.env`. This directory is mounted read-only at `/studio/providers`, not shipped in the image.
2. Provision real TLS certificate and key files at `deploy/certs/tls.crt` and `deploy/certs/tls.key` (kept out of Git). Caddy serves the built React UI and proxies `/v1/*` and `/webhooks/github` to the local Python API. The published TLS port is `127.0.0.1:8443` by default.
3. Initialize the database volume with the explicit bootstrap command above; then start `docker compose -f deploy/compose.yaml up --build` from the repository root, using `--env-file` when appropriate. Verify an authenticated `/v1/projects` response, then independently inspect GitHub effect/receipt readback.
4. Deploy the separately built C# ToolHost with its actual broker and Linux isolation prerequisites. The cockpit is an operator interface, not an executable tool broker.

The `deploy/providers` and `deploy/certs` directories intentionally contain no committed production credentials. Startup fails closed when required trusted adapters or TLS certificate bytes are absent. The exposed port is loopback-only until an authorized ingress configuration is supplied.

## Running-service acceptance

After installing private trusted providers, TLS certificates and the initial
database, set `STUDIO_OPERATOR_TOKEN` in a private shell and run
`python scripts/verify_live_operator.py --url https://localhost:8443`
with a trusted local CA (`SSL_CERT_FILE`, if needed). This tests both the
unauthenticated reject path and the authenticated project list over the real
TLS proxy. The separate GitHub two-receipt readback command and its manual
browser-continuity evidence are described in [Owner acceptance](../docs/OWNER_SETUP.md).

## Mandatory trusted ToolHost lifecycle

The production environment factory now requires `TrustedPorts.toolhost_broker` in
addition to the authenticated GitHub delivery and source-target adapters.
The broker may be a verified `PrivateToolHostBroker` or a trusted builder
providing `for_writer(writer, blob_root)`; the factory rejects all other
outputs. The broker starts **after APSW startup recovery** and stops before
closing the C writer. A current-lease authorization callback must be bound
to the same trusted C/A/B/D owner context, and the actual host assembly and
registry bytes must be SHA-256 pinned. ToolHost transport validation in CI
does not constitute installed owner/project authorizations.
