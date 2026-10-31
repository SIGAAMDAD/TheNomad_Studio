# R5R deployment scaffold and operating requirements

`deploy/Dockerfile` and `deploy/compose.yaml` currently define a **Python-only coordinator scaffold**. They do not provision an authenticated Studio service, an HTTPS proxy, React cockpit, C# ToolHost, or any ChatGPT browser sessions. The Compose default is `restart: "no"`.

## Required environment

The CLI executes `python -m studio.app.cli`. Supply an installed trusted `module:function` factory returning `TrustedPorts` for the complete R5R service. See [`deploy/studio.env.example`](studio.env.example) for the actual variable names and [`docs/SETUP.md`](../docs/SETUP.md) for the setup sequence.

- `STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment`
- `STUDIO_TRUSTED_PORTS_FACTORY=<installed.module>:<factory>`
- `STUDIO_APSW_DATABASE`, `STUDIO_BLOB_ROOT`, `STUDIO_SOURCE_ROOT` on durable, writable storage.
- `STUDIO_CONTRACT_DIR` pointing to the verified frozen `CONTRACTS/` directory.
- `STUDIO_OPERATOR_HOST=127.0.0.1` (loopback only) and a reviewed TLS proxy for remote operator access.

The factory must bind all 72 operator routes, owner/project authorization, GitHub source and command delivery, receipt verification, independent QA, releases and restart recovery. Missing inputs intentionally fail startup; a bare green build is not a running Studio.

## Known image packaging gap

The checked-in `Dockerfile` currently copies `src/`, `README.md` and `pyproject.toml` but **does not copy `CONTRACTS/` or `schema/`**. Production contract loading and APSW schema installation require those files. Before running an integrated Docker deployment, update the image and mount/copy verified contracts, test schema initialization and startup with real trusted ports, and check relative paths from the container working directory.

The Compose volume alone does not set the required durable database, blob and source root variables, install the external factory or establish host connectivity. It must be completed and verified separately; `docker compose up` as currently distributed is not a valid live acceptance gate.

## Validation before launch

Run source CI for the exact candidate, the Python/APSW integration suite, 72-route startup check, authenticated GitHub effect readback, failure/restart tests, complete ToolHost sandbox checks on the actual target OS, and live F-20 browser continuation. See [Owner acceptance](../docs/OWNER_SETUP.md), [Browser setup](../docs/CHATGPT_SETUP.md), and [ToolHost](../host/README.md).
