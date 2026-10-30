# The Nomad Studio

Control plane for project-scoped game production, manually started ChatGPT browser workers, cabal coordination, GitHub command delivery, deterministic tooling, review and source integration.

## Worker execution

A registered worker is one persistent browser conversation. A **shift** is one human-started, continuously executing assistant response; within it, the worker may observe and complete multiple GitHub-issued assignments before returning a single final receipt. Worker, shift, assignment, command and external receipt are separately identified. A GitHub write or a database row does not establish browser execution.

`studio.workforce` implements worker enrollment, shift lifecycle, verified HELLO handling, durable outbox reconciliation, exact command receipt processing and final shift receipts. `studio.production.service` atomically issues a GitHub-addressed command outbox record with each lease and resource reservation. `studio.integrations.github` verifies immutable remote bytes and recognizes shift-addressed command envelopes.

The existing task-bound `studio.agents.service` paths and unbound production composition remain pending replacement. The current branch is therefore an **implementation in progress**, not a production-ready Studio or a validated live ChatGPT/GitHub execution loop.

## Technology boundaries

- **Python / APSW / SQLite**: coordination, governance, transactional state and scheduling. APSW is required for production, while sqlite3 tests are offline reference checks.
- **GitHub**: existing communications branch for addressed commands and peer messages, plus canonical project source revisions and CI. The coordinator does not start ChatGPT responses.
- **.NET**: authorized deterministic process execution, isolation, logs and receipts; not a model runtime.
- **TypeScript**: operator cockpit against verified API responses; it does not infer active conversations from dispatch state.
- **Google Drive**: project knowledge and Bibles, separate from source and command authority.

The optional Student Developer Pack deployment profile adds bounded CI and development capacity; `FREE_BASELINE` remains the default and does not require student benefits.

## Local development and verification

```sh
python -m pip install -e '.[dev,coordinator]'
python -m pytest -q tests/unit tests/integration tests/acceptance --ignore=tests/unit/h/test_contract_static.py
node --test tests/unit/k/*.test.mjs
(cd ui && npm install --ignore-scripts --no-audit --no-fund && npm run build)
dotnet build host/Nomad.ToolHost/Nomad.ToolHost.csproj -c Release
```

The .NET SDK and npm packages must be installed for their respective builds. These commands check local source, not real ChatGPT browser continuation or remote GitHub acceptance. See `deploy/student-profile.md` for capacity setup and free-tier rollback.
