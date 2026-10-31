# R5R source and operator setup

This is the current setup reference for `SIGAAMDAD/TheNomad_Studio` after merged [PR #36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36) and [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37). The old S00-S09 scheduled worker pool is not part of the installation.

## 1. Source, credentials and authority

1. Check out an explicitly verified `main` revision. Keep the `studio/runtime` and `studio/comms` branches separate; neither is an implementation branch or a substitute for the APSW state writer.
2. Confirm a real owner-authorized project, trusted identities and permissions. A GitHub Issue, label or untrusted chat message alone does not authorize source/canon changes.
3. Configure authenticated, project-scoped GitHub provider bindings for commands, receipts, source revisions and CI. Source routes are enforced by `src/studio/app/source_policy.py` at the production source-revision edge.
4. Arrange a trusted deployment adapter module. The repository contains the interface `TrustedPorts` in `src/studio/app/production_factory.py`, but does not ship credentials, owner trust proofs or a ready-to-use live provider factory.

## 2. Install and verify locally

Requirements: Python >=3.11 (CI uses 3.13), APSW, Node 22, npm, .NET SDK 10, and a suitable Linux ToolHost sandbox backend for actual tool execution.

```sh
python -m pip install -e '.[coordinator,dev]'
python scripts/verify_workforce_schema.py
python scripts/verify_canonical_mirrors.py
python -m pytest -q tests/unit tests/integration tests/acceptance --cov=src/studio --cov-fail-under=90
cd ui
npm install --ignore-scripts --no-audit --no-fund
npm run check && npm test && npm run coverage && npm run build
cd ..
dotnet build host/Nomad.ToolHost/Nomad.ToolHost.csproj --configuration Release
dotnet run --project tests/unit/h/Nomad.ToolHost.Tests.csproj --configuration Release
```

The current UI has no tracked lockfile; use `npm install` in this source revision, matching CI, until `ui/package-lock.json` is introduced. CI enforces separate 90% line coverage on Python, all/authored TypeScript and production C# host source. A locally skipped APSW test is not a production integration pass.

## 3. Configure the actual coordinator

Supply these required environment settings to a trusted, noncommitted deployment environment:

| Variable | Purpose |
| --- | --- |
| `STUDIO_COMPOSITION_FACTORY` | `studio.app.production_factory:build_from_environment` |
| `STUDIO_TRUSTED_PORTS_FACTORY` | Importable `module:function` returning a fully configured `TrustedPorts` object |
| `STUDIO_APSW_DATABASE` | Persistent APSW database file path |
| `STUDIO_BLOB_ROOT` | Durable verified blob storage directory |
| `STUDIO_SOURCE_ROOT` | Durable project-scoped source integration workspace |
| `STUDIO_CONTRACT_DIR` | Optional contract directory, defaults to `CONTRACTS` |
| `STUDIO_OPERATOR_HOST` | Loopback only (`127.0.0.1` by default) |
| `STUDIO_OPERATOR_PORT` | Operator HTTP port (default `8000`) |

The factory must supply real authentication, owner policy, verified GitHub source/relay transport, command delivery, independent QA, release/readback and recovery adapters. The CLI exits `78` on missing/invalid configuration or an unbound operator API. Do not infer a running server from a successful source build.

Start only after the required configuration and persistent paths are available:

```sh
python -m studio.app.cli
```

Expose the loopback operator API through a reviewed TLS reverse proxy. The TypeScript cockpit is a separate frontend process/build, and ToolHost is a separately managed .NET executable. The Python Compose scaffold does not start either one.

## 4. Verify before pilot

Check remote identity/project separation, backup/restore, exact GitHub readback, outbox recovery under ambiguous writes, shift lease fencing, independently reviewed QA, owner-gated source promotion, and a live two-command browser shift (F-20). See [Owner acceptance](OWNER_SETUP.md) and [Browser-worker setup](CHATGPT_SETUP.md).

The distributed `deploy/Dockerfile` currently omits required `CONTRACTS/` and `schema/` files from its build image. Correct and revalidate the image before using that scaffold for an integrated deployment. See [Deployment](../deploy/README.md).
