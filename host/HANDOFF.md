# WG-H R5 integration handoff and status

**Ownership:** `host/**` and `tests/unit/h/**` only. Baseline `EMPTY_NEW_SOURCE_TREE` with immutable frozen plan and host protocol. No source tree from another worker is assumed present. WG-M owns canonical source integration and WG-L owns independent acceptance.

## Implemented source

- .NET 10 standard-library-only executable and frozen C# host DTO copy.
- Versioned, strict, size-limited NDJSON stdio, broker challenge/echo, explicit no-network-listener design.
- Broker-pinned SHA-256 operation registry, installed package manifest/executable/schema/resource hash validation, exact grant/project/task/lease/source/Canon/inputs binding, argument enum allowlist, symlink/path checks.
- Linux userns/bubblewrap preflight plus inherited `prlimit` bounds, read-only package/runtime, isolated network namespace, workspace-only writable mount, typed argv; no unsandboxed fallback.
- Async run/observe/cancel, process-tree kill, stdout/stderr hashing and output-artifact hashing, durable per-operation idempotency journal, unknown-on-restart semantics.
- Self-contained no-NuGet C# unit checks and Python offline source-contract tests.

## Required broker interfaces for WG-M/G/B/C/D/I

1. Broker resolves an authenticated operation from authorized D task and lease, A pinned Canon/owner effects, B grant/package and G installed bytes. Before sending run, broker commits C effect intention, then pins the operation in an immutable registry snapshot.
2. It launches WG-H as child with private pipe and expected SHA-256 of registry snapshot. It must keep both streams alive until all operations receive terminal receipts; EOF cancels outstanding jobs.
3. It echoes initial host handshake challenge and sends frozen host protocol frames; it independently verifies returned receipts/log or artifact bytes before C terminal observations. Host operation states are not directly written to C by WG-H.
4. Grants revocation, new tasks, and recovered/unknown jobs require broker rotation/reconciliation of the pinned registry. No remote browser control or approval authority is inferred from host receipts.

## Verification evidence (accurate status)

| Gate | Actual status | Evidence |
|---|---|---|
| Plan digest and frozen contract identity | PASS | SHA-256 of supplied `plan.json` matches brief; byte-for-byte copied `host.types.cs` |
| Source-level static checks | PASS | `R5_PACKET_ROOT=/mnt/data/wg_h_r5 pytest -q -p no:cacheprovider tests/unit/h/test_contract_static.py` (7 passed) |
| .NET 10 compilation | NOT_RUN | `dotnet` SDK missing in execution environment |
| .NET isolated unit harness | NOT_RUN | C# tests authored; `dotnet` SDK missing |
| Linux sandbox live probe | NOT_RUN | `/usr/bin/bwrap` missing; runtime would fail closed |
| Windows restricted token/Job Object | NOT_IMPLEMENTED | Backend not provided; Windows runs denied |
| Cross-worker GitHub/Drive/CI/real game tool integration | NOT_RUN | Other workgroup code, credentials and owner-selected Tier C project not supplied |
| Real game build/test pipeline, Tier C | NOT_RUN | Cannot be replaced by synthetic process execution |
| No model-provider runtime in host source | PASS (static only) | Offline scan for forbidden API classes; no HTTP listener or model runtime |

## Remaining integration/seam ownership

- **WG-M:** wire authenticated broker to G/B/C/D/A interfaces and C `tool_operations`, run actual build/QA pipeline, validate receipt readback and terminal state transition.
- **WG-H:** implement and independently verify a Windows restricted-identity/ACL/Job-Object/network-deny backend if Windows tools are selected; live Linux sandbox validation after bubblewrap installation; complete .NET clean build and runtime tests.
- **WG-L:** independent real isolation bypass tests, cross-project grants, symlink TOCTOU attack attempts, crash process-tree survival and Tier C outcomes.

**Completion statement:** This is a substantive locally authored WG-H source implementation, **not** a compiled or integration-certified host. No Git branch, remote commit, production launch, or live acceptance claim was made.
