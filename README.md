# The Nomad Studio

The Nomad Studio is a project-scoped production control plane for manually initialized ChatGPT browser-worker conversations. It coordinates approved feature briefs, department/cabal work, durable assignments, GitHub relay evidence, exact-source integration, independent QA, and operator review. The coordinator does not create or awaken consumer ChatGPT conversations.

## Current architecture (R5R)

| Component | Authoritative responsibility | Source |
| --- | --- | --- |
| Human owner | Scope, architecture, Canon, protected merges, release and spending decisions | [Owner guide](docs/OWNER_SETUP.md) |
| Python coordinator | Authenticated 72-operation API, project-scoped APSW state, leases, outbox, recovery and governance | `src/studio/`, `CONTRACTS/openapi.json` |
| GitHub transport | Project-bound immutable command/receipt bytes, source commit readback and CI evidence | `src/studio/integrations/`, `src/studio/agents/` |
| Browser worker | A human-initialized ordinary ChatGPT conversation; each executing response is a distinct shift | [Browser worker guide](docs/CHATGPT_SETUP.md) |
| C# ToolHost | Deterministic, authorized tool execution with fail-closed host isolation | `host/Nomad.ToolHost/` |
| React/TypeScript cockpit | Authenticated operator client for all frozen API operations | [UI guide](ui/README.md) |
| Specialist catalog | 174 roles across 12 departments; 526 SHA-addressed catalog entries | `src/studio/organization/catalog/` |
| Google Drive | Versioned, owner-reviewed creative documents through configured providers | `src/studio/integrations/drive.py` |

The reviewed Studio source is on `main`. Operational `studio/runtime` and `studio/comms` are separate GitHub branches; they are not code integration targets. The obsolete ten-slot scheduler and its scheduled GitHub workflows were removed by [PR #36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36); the follow-on one-wave cleanup was merged through [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37).

## Source policy

The five owner-selected source targets are checked at the R5R trusted source-revision boundary by `src/studio/app/source_policy.py`. See [Source repository routing](studio/workforce/SOURCE_REPOSITORIES.md) for the exact repository/ref pairs and approval process. The Studio repository does not receive game implementation source.

## Local verification

Use Python 3.13 with APSW for production-representative Python tests, Node 22 for the cockpit, and the .NET 10 SDK for ToolHost:

```sh
python -m pip install -e '.[coordinator,dev]'
python scripts/verify_workforce_schema.py
python scripts/verify_canonical_mirrors.py
python -m pytest -q tests/unit tests/integration tests/acceptance --cov=src/studio --cov-fail-under=90
(cd ui && npm install --ignore-scripts --no-audit --no-fund && npm run check && npm test && npm run coverage && npm run build)
dotnet build host/Nomad.ToolHost/Nomad.ToolHost.csproj --configuration Release
dotnet run --project tests/unit/h/Nomad.ToolHost.Tests.csproj --configuration Release
```

`ui/package-lock.json` is not committed. Use `npm install` as CI currently does; `npm ci` becomes valid once a matching lockfile is checked in. CI on PRs, `main` pushes and `implementation/**` validates Python/APSW, TypeScript/TSX, .NET host, role skills, contracts and provenance. See [.github/workflows/studio-validation.yml](.github/workflows/studio-validation.yml). Successful PR checks establish tested source behavior, not a deployed service or live browser-worker acceptance.

## Deployment and acceptance

The CLI entrypoint is `python -m studio.app.cli`, configured with `STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment` and a deployment-provided trusted ports factory. Startup requires real project-scoped adapters and durable paths; absent dependencies fail closed. The provided Docker/Compose files include the schema and frozen contracts, but remain scaffolding and do **not** supply the trusted production provider or complete production configuration. Do not treat `docker compose up` as production proof.

[Documentation index](docs/README.md) | [Setup](docs/SETUP.md) | [Deployment scaffold](deploy/README.md) | [Production pipeline](docs/PRODUCTION_PIPELINE.md) | [Repository cleanup](docs/REPO_MAINTENANCE.md) | [Launch acceptance](docs/OWNER_SETUP.md)

Live browser acceptance F-20 remains unverified until an existing manually initialized conversation processes two successive addressed GitHub commands within one continuing response and produces two independently read-back receipts. No paid inference-worker runtime is installed by this repository.
