# Response claim source-verification blocker

Status: OPEN — architecture/security review required. No authentication semantics have been changed.

The real-file regression `tests.v16.db.test_sqlite_response_extra_c8.SQLiteResponseExtraC8.test_response_claim_without_verified_source_base_fails_closed` demonstrates that a response created through `ResponseAccess` cannot claim an otherwise authorized SQLite job. `ResponseAccess._context()` delegates to `DocumentAccess._context()`, which constructs a verified-gateway context without `verified_source_base`; `SQLiteStore._claim()` rejects it with `REBASE_REQUIRED`. The test asserts that no attempt, lease or response-job start is created. This is a legitimate denial, not a reason to force the flag true.

The currently supplied interfaces do not establish how the response gateway independently verifies the authorized repository, branch and exact source base at admission. A reviewed fix must inject a trusted provider/source verifier on the server side, bind verification to the actual command, job and authenticated principal, fail closed on absent, mismatched, stale or unavailable readback, and address a source moving between verification and claim. Worker-supplied booleans, a client assertion, an unconditional `verified_source_base=True`, or treating the fixture `GatewayContext` as deployed mTLS proof are not acceptable evidence.

This blocks end-to-end response claim/STOP and multi-job acceptance via the current `ResponseAccess` integration. Local coordination and SQLite unit tests remain useful but cannot close those cases. Independent architecture/security review and a real isolated gateway/provider check are still required.
