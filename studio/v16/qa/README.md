# WG-H — independent QA and synthetic fault/load harness

This tree belongs to WG-H and consumes WG-A's `AUTH-V16`, `DB-CONTRACT`, `PUBLISH-V16`, and `RUN-V16` candidate. Contract bundle fingerprint: `d38a73649855cb5d38790929076ff79e64556a6a9f69780966b346186c78c520`. Its manifest currently says `CANDIDATE_PENDING_OWNER_ARCHITECT_QA`; WG-H cannot approve it.

## Offline execution (stdlib except `jsonschema` for boundary validation)

From the Studio repository root:

```sh
python -m unittest discover -s tests/v16/contracts -p 'test_*.py' -v
python -m unittest discover -s tests/v16/system -p 'test_*.py' -v
python -m unittest discover -s tests/v16/security -p 'test_*.py' -v
python -m unittest discover -s tests/v16/load -p 'test_*.py' -v
python scripts/studio_scale_probe.py --v16 --all-profiles --seed 1729 --out /tmp/wgh_load.json
python scripts/studio_scale_probe.py --workers 5 --events 20
```

The original synthetic projector benchmark remains available without `--v16`. New mode uses deterministic actor/fault assignments, isolated fake job leases, fake GitHub work branches and fake Drive revisions; source and Drive requests are **never** sent to providers. The JSON report preserves workload seed and SHA, per-fault counts, violation counters, p95/p99 per-actor local execution time, Python memory high-water mark, simulated provider request count, and measured wall/CPU time. Timings are environment-dependent; seeded functional digests and invariant outcomes should match. Required progression: **1, 5, 25, 100, 500 simulated actors**. Any lost work, duplicate side effect, authorization escape, or idempotency conflict misclassification sets `abort=true` and nonzero process exit.

**Never describe 500 fake actors as 500 independently launched chats.** `provider_calls` is local fake calls; external API calls and model cost are zero / unmeasured respectively.

## What is simulated and what is verified

| Surface | Verification here | Production acceptance |
|---|---|---|
| Frozen offline schemas | WG-A JSON Schema + independent mutation vectors | WG-A exact-subject independent review, owner/architect approval |
| Grant, authority and paths | WG-A test-only oracle challenged with unauthorized writes, unverified/shared connector, stale fences, revoked grant, path/rename/link/case attacks | WG-D isolated gateway with trusted identity, real Git tree and enforced token scope |
| 100 concurrent claims | A Python lock over one fake job, exactly one simulated lease winner | WG-C PostgreSQL 15+ transaction/row-lock/connection tests |
| Timeout-after-write, conflict, replay, partial Drive | In-memory provider with CAS, readback and idempotency ledger | WG-G real isolated GitHub/Drive integration and readback, protected branch denial |
| Crash/recovery | In-memory snapshot plus integrity-checked JSON file checkpoint, replay tests | WG-C migration rollback, actual PostgreSQL backup/restore/PITR and outbox recovery |
| 1/5/25/100/500 scale | Locally measured fake workload + deterministic trace | Bounded real user-opened chat pilot; measured DB connections, queue lag, API cost |
| Authored module tests | Local test pass only | Different verified QA reviewer, exact source SHA, authenticated provider evidence |

## Adapter conformance intake, later integration (C/D/F/G)

`adapter_cases.v1.json` enumerates 18 concrete adversarial tests mapped to an owner. Without merged adapters, this independent harness defaults to **NOT_RUN**, exit `2`:

```sh
python tests/v16/system/adapter_gate.py --source-sha <actual40hexsha> \
  --qa-principal <independent-id> --subject-author <implementer-id>
```

Once components exist, an independently provided **test bridge** may run a case on isolated integration resources. The bridge is supplied explicitly, never auto-detected or launched from a webhook. It consumes exactly one JSON object on stdin (`protocol`, `case`, `expected_source_sha`, `test_only`) and emits a single JSON receipt on stdout containing `case_id`, `adapter_owner`, `source_sha`, `synthetic:false`, `observed`, integer `observed_effects`, `command`, `evidence_sha256`, `isolated_test_resource`, and `tested_by`. The bridge itself maps the test scenario onto **actual** adapter calls and must provide a reproducible command/evidence, rather than hard-coded fixture answers. This harness does not define or modify the C/D/F/G production API.

```sh
python tests/v16/system/adapter_gate.py \
  --source-sha <actual40hexsha> --qa-principal <independent-id> \
  --subject-author <implementer-id> --owner D \
  --out /tmp/wgh_adapter_receipt.json --bridge python <isolated-bridge-script>
```

Even all `PASS_EVIDENCE_PRESENT` receipts yield only `EVIDENCE_ONLY_AWAIT_SIGNED_INDEPENDENT_QA`. A bridge's text is **not** cryptographic authentication; independent verification must inspect the exact source, test resource, logs, provider readback and credential boundary, then sign an exact-subject PASS/REWORK outside this harness. Never put gateway secrets, case contents or tokens in scenario JSON or evidence reports.

## Explicit blockers for production admission

1. WG-A candidate must be independently reviewed and approved without semantic drift.
2. Actual WG-C/D/F/G/F merged commits and integration test bridge do not exist in this supplied snapshot.
3. No PostgreSQL server, protected GitHub work branch, isolated gateway credentials, real Drive test document, or externally attested owner grant is exercised here.
4. The 500-actor profile is not a demonstrated 500-chat pilot and cannot authorize cutover, spending, branch merge or model launch.

Test-only infrastructure must never be imported by production gateway/worker code. WG-I receives the evidence, runs external conformance once dependencies land, and issues no activation verdict until a different reviewer verifies the exact SHA.
