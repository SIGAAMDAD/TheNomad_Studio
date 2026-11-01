# C#/.NET deterministic tool host

Scope: `host/**`, with self-contained tests under `tests/unit/h/**`. This is a **greenfield** source contribution. It is not the Studio orchestration API, GitHub transport, Python APSW authority, browser-chat worker runtime, or a model inference agent.

## Build and isolated unit checks

```sh
dotnet build host/Nomad.ToolHost/Nomad.ToolHost.csproj --configuration Release
dotnet run --project tests/unit/h/Nomad.ToolHost.Tests.csproj --configuration Release
R5_PACKET_ROOT=/path/to/extracted/R5 pytest -q tests/unit/h/test_contract_static.py
```

Target framework is `net10.0`, using only .NET base libraries; no NuGet runtime packages. The C# test harness uses a synthetic process factory **for isolated lifecycle checks only**, never to claim sandbox/production acceptance. The actual executable always uses `Sandbox.Create`.

## Broker/host boundary

A trusted local Studio broker prepares a **private stdin/stdout** child process and a version-1 *registry snapshot* whose bytes are pinned by SHA-256 at child startup. Each authorized operation is pinned to one `operation_id`, `request_id`, `project_id`, `task_id`, `worker_id`, `shift_id`, generation, source revision, Canon snapshot, installed package digest, entrypoint ID, grant digest, workspace ID, and ordered input artifact digests. Multiple operations can appear in one registry snapshot. **No run is accepted without an exact matching installed authorization.** Broker-provided text is not an executable path or shell command. Grant revocation requires terminating/restarting affected children with a new registry snapshot; the broker is responsible for such enforcement.

The registry is prepared from the verified immutable package installed by WG-B, current project grants/leases and source/Canon context verified by A/B/D, using C's scoped transaction services. WG-G/I relay messages have to be validated by their domain owners before the broker creates an operation entry. WG-H never reads raw GitHub relay files as executable tool jobs. WG-C's authoritative `tool_operations` table is updated through WG-C's named scoped commands by the integrating broker/service, not through direct host SQLite access. The host-local journal is **recovery evidence**, not authoritative domain state.

The broker launches:

```sh
dotnet host/Nomad.ToolHost/bin/Release/net10.0/Nomad.ToolHost.dll \
  --registry /absolute/path/registry.json \
  --registry-sha256 64_lowercase_hex_hash \
  --journal /absolute/private/journal
```

The host emits a `handshake` frame with `protocol_version=1.0.0`, unpredictable `host_nonce` and only **verified** `supported_isolation`. The broker echoes the exact frame. It may then send the frozen `run`, `observe`, or `cancel` frames. The host emits frozen `receipt` frames, including intermediate `queued` and `running`. Transport is UTF-8 newline-delimited JSON, one frame per line, max 65,536 bytes, no network listener. Invalid protocol frames receive `failed` receipts with `operation_id=protocol_error` and `project_id=unscoped`; process stderr is an operational diagnostic channel, not a Studio decision channel.

## Registry fields (broker-internal, not shared API)

Registry v1 contains arrays of:

- `packages`: `digest` (hex SHA-256 of actual `package.json` manifest bytes), absolute `install_root`, package-relative `manifest_file`, and broker-mapped entrypoints (`id`, `relative_executable`, `sha256`, `isolation`, `fixed_args`, `arguments`, `environment`, `output_files`, resource caps). Every selected executable, args schema, output schema, and package resource is rechecked against hashes in the **installed package manifest**. Argument rules support exact `enum` strings; all values are passed using `ArgumentList`, never a shell.
- `grants`: `digest`, `project_id`, `package_digest`, exact allowed workspace/entrypoint IDs, `max_deadline_ms`.
- `workspaces`: `id`, `project_id`, absolute existing filesystem root; all parent components must be symlink/reparse free.
- `operations`: the full frozen `ToolRun` identity context except `args` and deadline. The broker must pin the task lease and effects before execution.

No registry can grant owner authority; it only allows the already-authorized local tool effect. Build output filenames are declared by the broker-pinned entrypoint registry `output_files`; successful outputs are hashed and reported as `output_artifact_digests` in fixed manifest order. Logs are bounded and hashed. Journal state for interrupted `running`/`queued` operations becomes `unknown` after restart, never fabricated completed.

## Isolation and limitations

The currently implemented production backend is **Linux bubblewrap + user namespaces + network namespace + read-only runtime/package mounts + writeable project workspace + RLIMIT CPU/address space/file size/process count** (`linux-bwrap-userns-rlimits-no-network`). The host probes actual sandbox viability before advertising and before using that backend. `--unshare-all` also unshares networking; configured network access is not implemented. Cancellation and deadline enforcement kill the process tree and produce a receipt.

This backend does **not** claim seccomp, cgroup accounting, authenticated Windows restricted tokens, or Windows Job Object enforcement. On Windows or systems without functioning bubblewrap/prlimit/user namespaces, a run fails closed as `capability_unavailable`. A real Windows backend is an outstanding WG-H requirement. A run requested with an unavailable required isolation never degrades to unsandboxed execution.

The integration broker must verify remote/staged bytes and source SHA separately and persist an effect intent with WG-C **before** transmitting `run`; it must reconcile host `unknown` receipts rather than crediting success. This implementation does not fabricate WG-G, WG-B, WG-C or WG-M integrations.

## CI-hosted distributable

The Studio source verification workflow publishes a `toolhost-publish/` directory
and SHA256SUMS along with the production C# coverage report. The binary is built
from the exact workflow source SHA using .NET 10 `dotnet publish`. Verify the
artifact SHA256SUMS and the associated workflow identity before installation.

A real trusted local broker must launch the verified binary with
`--registry`, `--registry-sha256`, and `--journal` arguments, own its
private pipes and provision only authorized operations. The binary is a
sandboxed execution component, not a standalone remote API service. It must
run on an OS supporting the declared sandbox features (Linux bubblewrap +
userns/prlimit) and requires operational acceptance on that OS.
