# SQLite C8 CI contract

The PR publishes **three independent checks**, each bound to the actual Git checkout SHA. An acceptance mapping is a claim of local test coverage, not a reviewed release decision.

| Check | Green means | Red means |
| --- | --- | --- |
| **SQLite C8 Regression** | Baseline and C8 tests, crash/restore, response, performance fixtures, syntax and registry validation pass | Executable source failure, broken test discovery, or invalid local test contract |
| **SQLite C8 Acceptance Coverage** | All *mapped* cases execute and report honestly; missing cases remain in `unverified_cases` | A mapped assertion fails, source identity differs, or the coverage inventory is invalid |
| **SQLite C8 Merge Gate** | All 76 cases executed on this checkout, exact-head independent review and separately approved release evidence recorded | Any untested case, failed assertion, missing independent QA/provider/restore/owner authorization or unverifiable commit identity |

`--report-only` is **only** for the second workflow. The original `scripts/sqlite_acceptance.py --run-mapped` remains strict and is exercised by the merge gate. No change to the production/cutover permissions or the independent QA requirement is implied by green regression or green coverage.

GitHub `pull_request` runs use a synthetic **tested merge commit**. Reports record that SHA separately from the **PR head SHA**, the **base SHA**, and the frozen **inventory baseline SHA**; none should be labeled interchangeably. Exact test failures retain their captured unittest traceback in the case receipt and the action log. `sqlite_c8_validate.py` enforces a reviewable discovery floor of 172 inherited and 95 C8 tests and rejects duplicate discovered test IDs rather than accepting a loose total alone.

All three workflows use a single `pull_request` trigger plus optional `workflow_dispatch`: the redundant active-branch `push` trigger was removed. The merge gate is intentionally expected to fail while cases or independent approvals remain outstanding. Workflow artifacts persist even on failure, and status reports must never be mistaken for owner approval or live provider evidence.
