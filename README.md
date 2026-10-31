# The Nomad Studio

The Nomad Studio is the project-scoped production control plane for manually initialized ChatGPT browser conversations, cabal/task scheduling, source integration, independent QA, GitHub relay evidence and deterministic tooling. It is not an AI model runtime or a browser wake-up service.

## Runtime and authority

- **Owner:** final authority for architecture, policy, protected source merges, canon and releases.
- **Workers:** a registered worker is an ordinary browser conversation; a shift is one actually executing assistant response. Assignments, shifts, leases, commands and remote receipts are distinct.
- **Python/APSW:** project-scoped durable state, transactions, governance, scheduling, leases, outbox, checkpoints and recovery.
- **GitHub:** immutable addressed communications, verified source refs, CI and exact remote readback. Source and communications branches have separate bindings.
- **.NET ToolHost:** pinned, bounded, manifest-authorized process runner with fail-closed OS sandbox controls; not a ChatGPT runner.
- **TypeScript cockpit:** authenticated operator client for the frozen 72-operation API.
- **Google Drive:** long-form creative bibles under their own owner-approval and revision rules.

The coordinator cannot autonomously start or resume a ChatGPT browser response. A queued command is not proof of browser execution.

## Source tree

| Area | Contents |
| --- | --- |
| src/studio/ | Live studio domains, APSW state, provider adapters, HTTP and recovery |
| CONTRACTS/ and schema/ | Frozen operator contracts and storage contracts |
| host/ | Deterministic ToolHost |
| ui/ | Operator cockpit |
| tests/ and qa/ | Regression, security, failure injection and provenance evidence |
| skills/ and studio/ | Specialist reference material and active department contracts |
| deploy/ and docs/ | Configuration, deployment and acceptance instructions |

## Verification

Install the appropriate Python 3.11+ / APSW, Node/npm and .NET toolchains before running:

    python -m pip install -e '.[dev,coordinator]'
    python scripts/verify_workforce_schema.py
    python -m pytest -q tests/unit tests/integration tests/acceptance --cov=src/studio --cov-fail-under=90
    (cd ui && npm ci && npm run check && npm test && npm run coverage && npm run build)
    dotnet run --project tests/unit/h/Nomad.ToolHost.Tests.csproj --configuration Release

Hosted CI additionally verifies source provenance and enforces 90% line coverage independently for Python, complete TS/TSX, authored TS/TSX and production C# ToolHost.

## Deployment

The real service entry point is studio.app.cli with STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment and an installed STUDIO_TRUSTED_PORTS_FACTORY. The factory constructs all 72 operation handlers around a shared APSW writer; missing trusted adapters fail closed. Operator HTTP binds only to loopback and requires a trusted TLS proxy. See deploy/README.md and docs/SETUP.md.

## Acceptance

The source/coverage/control gates have passed at the verified R5R implementation revision. Tier C browser continuation F-20 still needs live evidence: an already-initialized ordinary browser conversation handles two consecutive GitHub assignments and publishes two authentic receipts during a continuing response. No paid AI API execution is implicitly authorized.

See docs/OWNER_SETUP.md, docs/CHATGPT_SETUP.md, docs/PRODUCTION_PIPELINE.md and docs/REPO_MAINTENANCE.md.
