# V1.6 SQLite — C8 observed local and CI test evidence

**Status:** PARTIAL_LOCAL_C8_PASS; **production GO remains blocked**.
Repository: `SIGAAMDAD/TheNomad_Studio`; branch: `studio/v16-sqlite-unified`.
Tested source commit: `28438b3d7ca30c749c9bb5a7501bde5b2702066f`.
CI run: https://github.com/SIGAAMDAD/TheNomad_Studio/actions/runs/40626362530 — **success**, as reported by GitHub Actions.

## Executed
- `PYTHONPATH=. python scripts/sqlite_c8_validate.py --json /tmp/c8_verified_receipt.json`: **209 passed, 0 failures, 0 skips**, on Python 3.13 and SQLite 3.46.1. The run comprises 172 inherited tests and 37 new isolated C8 tests.
- All eight SQLite migrations executed on disposable private real files; repeat migration idempotence, immutable ledger and rollback on injected SQL failure were exercised.
- Single database owner, WAL/FULL configuration, transaction rollback and threaded one-writer behavior were exercised.
- Atomic claim CAS, idempotency conflict, 16 concurrent claims, checkpoint/reassignment fences and partial publication-command denial were exercised.
- Publication admission, separate source patch/tree hashes, 12 parallel identical reservations, one dispatch without blind retry, and exact trusted readback **after grant revocation and SHADOW** were exercised. Wrong tree and untrusted recovery were rejected.
- Ordered department inbox delivery/ACK, metadata idempotency, terminal response receipts, STOP/PAUSE, stale leader and document writer fences were exercised.
- Independent watchdog identity, audit absent/degraded reporting, unobserved response status, and alert retries requiring verified readback were exercised.
- Real-file online snapshot, manifest verification, tamper rejection and SHADOW-only restore were exercised.
- `python scripts/studio.py validate`, `python scripts/production.py validate`, role registry validation for 174 roles/12 departments, and `python -m compileall` all passed locally.

## What these tests do NOT establish
- They use controlled fixture principals and locally simulated evidence; no production mTLS identity, real GitHub or Google Drive provider credentials/readback, or production cutover occurred.
- The full enumerated SQLite/TX/PUB/RC/STOP/CM/HR/PERF/RECOVER acceptance matrix has not been individually closed; this report is supporting evidence, not a blanket PASS.
- The complete 26 HR failure injections, independent watchdog host execution and alert delivery, multi-host load/p95 thresholds, live PostgreSQL transfer if applicable, off-host backup verification, RPO/RTO measurement, two-invocation manual canary, owner approval and independent exact-SHA QA remain pending.
- The database remains SHADOW by default. The worker-authored test result is not an independent QA verdict.

## Reproduce
```bash
python -m pip install -r infra/gateway/requirements.txt
python scripts/sqlite_c8_validate.py --json /tmp/c8-receipt.json
python scripts/sqlite_c8_validate.py --c8-only
python scripts/studio.py validate
python scripts/production.py validate
python skills/rpg-studio-all-roles/scripts/validate_v4.py
```
