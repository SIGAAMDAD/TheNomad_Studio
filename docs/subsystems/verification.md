# WG-Q — Verification / Acceptance / Performance / Chaos / Security

**Contract:** `NOMAD/CMD/1`, `NOMAD/QUERY/1`, `NOMAD/VERIFICATION/*/1` local artifacts. This module is an **independent verification harness**, not a second job database, owner-approval issuer, model launcher, gateway, provider client, or QA signer. Source scope: `src/nomad_studio/verification/**`, `tests/verification/**`, this document.

## Composition and frozen interface

```python
from nomad_studio.verification.api import build_component
component = build_component({
    'source_root': '/absolute/immutable/integrated/checkout',
    'evidence_dir': '/absolute/path/outside/checkout/receipts',
    'probes': {},                  # map check_id -> callable(Check) -> strict bool
    'probe_mode': 'FAKE',         # or INTEGRATED, only if integration root authorizes it
    'fault_adapters': {},         # map domain -> FaultAdapter with inject(FaultCase)
    'load_driver': None,          # TrafficDriver port; None selects synthetic driver
    'interface_diffs': None,      # reviewed list of unmatched interface changes; None blocks
    'external_verifier': None,    # trusted independent validator, never a worker assertion
})
```

`.execute(CommandEnvelope, VerifiedIdentity)` requires scope `verification:execute` (or `verification:*`) and a non-expired trusted identity minted upstream. `.query(QueryEnvelope, VerifiedIdentity)` requires `verification:read` (or `verification:*`). The `principal_id`, `credential_id`, `issuer_id`, `authenticated_via` and authority epoch must exist. The module has **no ability to mint these identities**. A copied `worker_id`, prompt or transport JSON cannot confer privilege. Envelopes are domain `verification`, command version `NOMAD/CMD/1`, query version `NOMAD/QUERY/1`; commands require nonempty request ID and idempotency key. Unknown payload keys are denied (`INVALID_SCHEMA`). No user-provided path to other modules is imported. This subsystem has **no SQLite schema or migrations**; WG-A remains the sole mutable Studio control authority.

| Operation | Command payload | Return / result authority |
| --- | --- | --- |
| `verification.contract_gate` | optional `check_ids: list[str]`, `mode: FAKE \| INTEGRATED` | 121-check matrix probe execution plus AST module inventory. Missing adapter `NOT_RUN`; fake success `MOCK_PASS`. `INTEGRATED` cannot mark EXTERNAL/QA passed. All live critical acceptance requires separate report gate. |
| `verification.load_run` | `streams` = 200/500/800; optional `events_per_stream` 1–100, `offered_per_second` 1–200000, `seed`, `max_queue`, `failures_per_hundred` 0–50 | Wall-clock and tracemalloc measurements plus separately labeled virtual queue arrival/service simulation. Default `SyntheticDriver` is `SIMULATED_ONLY`, outcome `NOT_RUN` for real-system acceptance. |
| `verification.fault_run` | optional `case_ids: list[str]` | 25 fault definitions spanning all 19 domains; missing adapters `NOT_RUN`, local fakes `MOCK_PASS`, mismatched expected state/code `FAIL`. |
| `verification.acceptance_report` | empty object | Strict gate: source-tree SHA, frozen-contract hash, 19 module facades, reviewed interface diffs, 121 critical check outcomes and independent E05 QA; produces `BLOCKED` until actual evidence. |
| Query `verification.contract_gate` | empty | Read machine-readable coverage matrix, no operation. |
| Query `verification.acceptance_report` | empty | Re-evaluate stored evidence and integration policy. |

**Facade:** `src/nomad_studio/verification/api.py::build_component(deps: Mapping[str, object]) -> Component`. Frozen `Outcome.state` values only: `ACCEPTED`, `DENIED`, `BLOCKED`, `NOT_RUN`, etc. `ACCEPTED` for a diagnostic invocation **does not approve source merging**, and neither `MOCK_PASS` nor exit zero of an arbitrary command proves a corresponding contract check. Errors are closed, with `IDENTITY_UNVERIFIED`, `MALFORMED_REQUEST`, `INVALID_SCHEMA`, `INVALID_SCOPE`, `SOURCE_SHA_MISMATCH`, `NOT_CONFIGURED`, `GATE_BLOCKED` and `CONTRACT_TEST_FAILED`; no raw exception message, token or private Oversight content is returned.

## Coverage and truthful receipts

`coverage_matrix.json` contains 19 `modules` (`A`–`S`) and 121 individually identified critical acceptance checks. It was derived from the frozen `acceptance_matrix.json` and additional frozen G00–G12, E01–E07 and seven `owner_fixed` requirements. Each check is classified `UNIT`, `INTEGRATION`, `EXTERNAL` or `QA`, with an immutable description. Each module includes its frozen facade path and every group-specific check ID. `CoverageMatrix` rejects missing domains, duplicate IDs, unrecognized tiers or missing module criteria.

`EvidenceWriter(source_root, output_dir)` writes one immutable-by-name, atomic JSON receipt per invocation outside the checked-out source tree. SHA256 is calculated over sorted, length-delimited relative paths + per-file SHA256 content digests; it is **not** a Git SHA-1/commit hash. It includes source-file digests, Python/platform identity (no process-wide environment dump), exact argv command, elapsed time, exit code, skipped IDs, stdout/stderr digests, optional independently checked external receipts, and observed probe/fault/load results. `EvidenceWriter.run_command(argv, ...)` executes a bounded subprocess without shell expansion (max 600 seconds) and records its actual exit, including timeout. It does **not** infer individual check success from a bare exit-zero. Receipt files are local verification artifacts, not the authoritative WG-A event journal or evidence of independent QA by themselves. Directory symlinks inside source tree are rejected; evidence directory must be outside the source tree.

`evaluate_gate()` blocks if **any** critical check lacks a valid exact-source receipt; if worker modules are absent; if `contracts/v1.py` differs from frozen seed; if facade signatures are wrong; if the interface diff review is missing or non-empty; if a check was skipped, failed, or lacks a valid mode; if any current file manifest differs from a receipt; or if external/QA/integrated claims lack verification by the trusted `external_verifier` port. This validator **must** verify real provider/independent reviewer evidence and bind it to the exact tree digest; the harness never supplies a permissive verifier in production. It is an integration-owned trusted adapter, not a user-supplied JSON flag. `E05` explicitly requires independent exact-SHA QA. Tampering with source after a test changes the SHA and invalidates receipts. `NOT_RUN`, `MOCK_PASS`, arbitrary `skipped_ids`, self-asserted QA or failure fixtures cannot promote the gate to accepted.

## Load methodology and limitations

`run_load(LoadConfig, driver: TrafficDriver | None)` has a seeded, reproducible ordering, deterministic fault choices (429, network ambiguity, SQL-lock response, stale epoch, duplicate transport), and offered event arrival times at the specified **virtual** events/second. A labeled synthetic service completes at 2,000 events per virtual second and uses a bounded queue. Admission is `min(streams, 500)`, so the 800-stream scenario applies backpressure to 300; excess streams still generate attempts and receive bounded refusals. `SyntheticDriver` is an in-memory **test double**. This exercises dispatch, queue bounds and metrics calculations without pretending SQLite or provider infrastructure exists. The 500 figure is a logical slot cap, **not** proof of concurrent ChatGPT web executions.

For every 200/500/800 scenario it records requested/queued/handled events, admitted shifts and admission/queue backpressure, retry/fault counts, duplicate deliveries, 429s, virtual queue wait p50/p95/p99, actual Python call wall-time p50/p95/p99 for ingestion/claim/inbox, actual wall throughput, tracemalloc peak/current bytes, process max RSS (platform-dependent native units), and max pending queue. SQL queue/lock, WAL, disk, read/write amplification, oldest job age, real provider rates, crash recovery and real host concurrency are explicit `{'state':'NOT_RUN'}` on a fake driver. An injected `INTEGRATED` driver must expose `admit(worker_id)->str`, `submit(TrafficEvent)->str`, `metrics()->dict` and `mode='INTEGRATED'`; integration should connect it to actual gateway/relay/scheduler through **public port interfaces** and provide measured DB metrics. The virtual service rate is a workload generator control, **not** an observed production SLO. No SLO threshold has been owner-approved.

## Fault matrix and adapter isolation

25 explicit cases cover all frozen domain boundaries and the required network timeout, duplicate ACK, SQL lock, crash-after-checkpoint, revoked ticket, stale epoch, HEAD drift, flipped QA SHA, protected rename, and corrupted/truncated archive cases. Other probes cover fake owner approval, stale lease, forged Oversight notice, missing connector, budget exhaustion, stale canon and untrusted operator. `run_faults` invokes only opt-in `FaultAdapter.inject(FaultCase)->Outcome`, **never** an undocumented injection key on a production `Component`. Missing adapter `NOT_RUN`; adapter exceptions `FAIL` (exception type only). A connector-absent observation remains `NOT_RUN` even when it is the expected result. The supplied `ScriptedFaultAdapter` is a test fixture; it cannot yield production acceptance.

No fault adapter is imported from a sister module. Each sister system can provide a temporary test-mode wrapper in the manual integration worktree, using its own public `Component.execute/query` API and controlled synthetic dependencies. This is a single-wave ownership constraint; no verification source will be patched into sister packages.

## Integration seams and failure policy

1. **WG-A:** optional trusted `StateStore` / read-only metrics instrumentation through integration `TrafficDriver` and independent restore receipt; WG-Q must not start a SQLite connection or mutate another module's tables.
2. **WG-B/C/D/E/F/I:** component-port probes in integration harness for authenticated launch/epoch, relay ACK/fanout, multi-job STOP, leases, source exact SHA/paths and independent QA; use test-only adapters in composition root.
3. **WG-G/H/R/S:** role and organization source snapshots, budget claims, and versioned canon test probes. Never turn supplied catalogue content into owner acceptance.
4. **WG-J/K/L/M/N/O/P:** provider readback, real bytes, independent archive restore, hourly reports, confidential notices, owner console and ChatGPT connector. `EXTERNAL` probes remain `NOT_RUN` until real receipts exist.
5. **Final integrator:** creates immutable checkout, supplies `source_root`, independently stored `evidence_dir`, contracts and interface review, source-bound independently authenticated `external_verifier`, and test/probe adapters. Runs full `G00`–`G12`, `E01`–`E07`, and independent final SHA QA. `interface_diffs=None` is blocked; review with `[]` means reviewer found no unmatched diff, not auto-approved by WG-Q. No import of sister implementation in WG-Q.

### Real two-chat web relay manual QA checklist (E01)

- [ ] Human owner manually opens **two** distinct standard web ChatGPT chats with High selected in the actual UI; record time and independently observed UI setting. Do not mark model setting verified based on the prompt.
- [ ] Issue separately scoped short-lived launch tickets through the authenticated production gateway; record owner authorization/issuer, worker IDs, job IDs and epochs **without storing ticket plaintext**.
- [ ] From both real chats, invoke the actually installed ChatGPT connector into the trusted HTTPS gateway. Capture authenticated transport traces and gateway receipts; explicitly test that copied worker ID without valid ticket is denied.
- [ ] Relay two different addressed jobs to one of the chats. In **one assistant response** observe claim, execute, separately persisted report, checkpoint, next job, then STOP/DRAIN/YIELD control. Capture each exact job+shift+epoch receipt.
- [ ] Verify chat B cannot poll/ACK chat A's inbox or publish on its grants. Revoke/reassign an epoch and confirm stale handles fail. Trigger duplicate ACK, timeout and retry with source-side readback.
- [ ] Verify exact source/CI SHA, QA independent reviewer/issuer and owner/head gates. Observe network transport readback and record actual provider receipts. A local fake or two local Python clients **cannot satisfy** this checklist.
- [ ] Record external receipt issuer, observed exact-source SHA, time, command, exit, screenshots/trace refs in controlled evidence store; independent reviewer validates them and signs E01/E05. Missing stages stay `NOT_RUN`.

### Final independent release acceptance checklist

- [ ] Integrate 19 independently checksummed owned bundles against one immutable seed; verify no contract or protected path drift.
- [ ] Compose all 19 real module facades via frozen ports; verify no runtime/private cross-module import.
- [ ] Run all 121 checks on exact final checkout with actual subprocess/provider receipts; include 200/500/800 synthetic profiles against integrated backend and all 25 injected faults with independent negative evidence.
- [ ] Record actual SQLite/WAL/queue/per-provider metrics, crash/restoration integrity, protected source rejection and security replay denial; record unavailable metrics as `NOT_RUN`.
- [ ] Obtain distinct independent QA reviewer exact-source attestation and verified external E01–E07 evidence. An owner release grant is separate and required by upstream governance.
- [ ] Recompute source tree digest immediately before any merge/release; invalidate all receipts on drift. Review `unmatched_interface_diffs`, non-zero exit codes, skipped tests, missing modules and false-green fixtures. A failing fixture must block.

### Known external gates

`E01` real web-chat connector/High UI; `E02` actual platform parallelism; `E03` GitHub and Drive live readback; `E04` production TLS/SQLite/off-host restore; `E05` independent final SHA/owner cutover; `E06` hourly alert delivery; `E07` Oversight independent issuer. All are **NOT_RUN** in this isolated workgroup. No provider token, actual deployment host, real reader, secrets, email destination or archive witness is supplied. Worker High setting is owner-selected, not programmatically attestable here.

## Local test commands

```sh
PYTHONPATH=src python -m unittest discover -s tests/verification -p 'test_*.py'
PYTHONPATH=src python -m unittest discover -s tests/contracts -p 'test_*.py'
PYTHONPATH=src python -m compileall -q src/nomad_studio/verification
```

Tests include intentional failing fixtures that are **asserted to fail the release gate**, not an intentionally failing `unittest` process. See the workgroup handoff for actual executed results and workload metrics; no real 19-way integration or live connector has been performed.
