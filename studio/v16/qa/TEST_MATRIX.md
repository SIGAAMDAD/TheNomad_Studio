# WG-H V1.6 test coverage ledger

All newly implemented cases are **SYNTHETIC** unless a separate, exact-SHA adapter bridge records actual isolated execution. `adapter_cases.v1.json` defines the later integration requirements and initial `NOT_RUN` verdict.

| ID / topic | Synthetic executable test | Actual C/D/F/G |
|---|---|---|
| CLAIM-100, unique lease owner, claim collision | `system/test_pipeline.py` 100 threaded attempts | NOT_RUN (PG row locks) |
| AUTH-REVOKED, forged identity, approval, expiry, policy downgrade, stale generation | `system/test_pipeline.py`, `security/test_boundary.py` | NOT_RUN (gateway credential, revocation) |
| AUTH-PATH traversal, renamed side, symlink/submodule, Unicode/case | `security/test_boundary.py` versus WG-A reference | NOT_RUN (Git tree) |
| QA self-review, changed QA source subject, readback mismatch | `security/test_boundary.py`, `system/test_adapter_gate.py` | NOT_RUN (signed QA) |
| DB duplicate out-of-order event, ACK replay, outbox dedupe | `system/test_pipeline.py` | NOT_RUN (PG and F) |
| DB crash after reservation / after provider effect | `system/test_pipeline.py` | NOT_RUN (PG) |
| DB restore and checkpoint corruption | `system/test_pipeline.py` (file-backed JSON fake) | NOT_RUN (PG restore/PITR) |
| PUBLISH timeout before/after effect; readback before retry | `system/test_pipeline.py` | NOT_RUN (G) |
| PUBLISH Drive partial/revision mismatch | `system/test_pipeline.py` | NOT_RUN (Drive CAS) |
| PUBLISH source head drift / merge conflict | `system/test_pipeline.py` | NOT_RUN (GitHub) |
| PUBLISH changed artifact same idempotency key | `system/test_pipeline.py` | NOT_RUN (gateway/DB) |
| No AI task dispatch via event/outbox | `system/test_pipeline.py` | NOT_RUN (live router/CI audit) |
| SCALE progression 1/5/25/100/500 | `load/test_workload.py` and `scripts/studio_scale_probe.py --v16` | NOT_RUN (real chat pilot) |
| External adapter evidence gate | `system/test_adapter_gate.py` | NOT_RUN (bridge absent) |

An `OK` from unittest is local unit evidence only. A local worker cannot independently approve its own modified harness or upstream implementation.
