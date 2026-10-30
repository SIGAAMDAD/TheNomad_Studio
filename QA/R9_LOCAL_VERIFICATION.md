# Nomad Studio R9 local verification — 2026-10-29

Source authority: R9 local-authoritative PR #34 head `7bd22971d0f49cc8c673b6ba693713ad64051fa1`,
with in-place R9 repair and physical deletion of old V1 runtime modules/tests.
No compatibility or migration layer was added. `CONTRACTS/initial_schema_v1.sql`
and `CONTRACTS/runtime_ports_v1.py` are frozen *R9* contract resources.

## Executed on the patch worktree

- R9 source contract / 56-table SQLite owner-map gate: PASS.
- Python AST / bytecode compile pass: PASS.
- Full retained R9 and oversight suite: **1,338 passed, 48 subtests passed**;
  one pre-existing Pytest collection warning.
- Included direct 22-port composition, real SQLite persistence, cross-CAB
  ownership, idempotency, crash/recovery, second-owner lock, read-only doctor
  with active writer, verified outbox ACK / UNKNOWN, no double dispatch, and
  explicit no-old-module import tests.
- Clean wheel built with installed `setuptools` using `--no-build-isolation`
  because this execution environment has no internet for a build-isolated
  dependency install; the GitHub workflow uses normal isolated wheel building.
- Cold wheel install/entrypoint/56-table R9 schema: PASS.
- Wheel inspection: no old V1 state, source, archive, knowledge, publication,
  or artifact `build_component` modules.
- No native provider, external TLS gateway acceptance, or independent QA
  was represented as tested by the local suite.

## Remaining external verification

- Exact new PR SHA remote CI: NOT_RUN on this *unpublished* patch until commit.
- Verified native provider/host capability with external receipts: NOT_RUN.
- Native TLS ingress, external effects and independent QA: NOT_RUN.
- Deployment/production activation: NOT_RUN.

Source-level tests and packaging cannot substitute for those external gates.
