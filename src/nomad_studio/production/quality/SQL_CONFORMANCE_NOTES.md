# CAB-E — SQL conformance notes (R9 frozen new-only DDL)

Contract reviewed: `CONTRACTS/initial_schema_v1.sql`, `CONTRACTS/schema_table_owners.json`, `CONTRACTS/records.schema.json`, `CONTRACTS/runtime_ports_v1.py`, and `CONTRACTS/R9_OPERATIONAL_SEMANTICS.md` from the pinned R9 kit. These files are **not edited**. The quality-owned test fixtures `tests/production/quality/frozen_schema_v1.sql` and `frozen_table_owners.json` are copies for executable schema/FK tests; the P2 integrator must assert equality to the installed canonical schema and deny any drift.

## Exactly six CAB-E writer-owned tables

| Table | Primary key / unique | Allowed writer | Evidence and relation |
| --- | --- | --- | --- |
| `v1_quality_requirements` | `(feature_id, requirement_id, acceptance_check_id)` | CAB-E | FK to `v1_feature_runs`; requirement/acceptance mapping extracted from active `FeatureContract` |
| `v1_test_executions` | `execution_id`; indexed `(feature_id, commit_sha, test_id)` | CAB-E | FK to `v1_feature_runs`; exact commit/test/exit/log/adapter/toolchain/input/trusted runner and execution timestamp |
| `v1_review_assignments` | `review_id` | CAB-E | FK to `v1_feature_runs`; exact subject hash, authenticated reviewer, author-lineage hash and state |
| `v1_review_verdicts` | `verdict_id` | CAB-E | FK to `v1_review_assignments`; `ACCEPT`, `REJECT`, `INCONCLUSIVE` only; exact subject/evidence and reviewer |
| `v1_qa_ready_receipts` | `receipt_id`, UNIQUE `(feature_id,commit_sha)` | CAB-E | FK to `v1_feature_runs`; immutable QAReadyReceipt digest, tested SHA, contract/manifest checksums and signer |
| `v1_independent_qa` | `qa_id` | CAB-E | FK to `v1_feature_runs`; `PASS`, `FAIL`, `INCONCLUSIVE` independent verdict, reviewer, exact SHA and verified evidence digest |

All tables present in `schema_table_owners.json` have exactly one listed owner. CAB-E does not create tables or issue DDL in production. `v1_qa_ready_receipts` holds the digest **and the trusted immutable bytes store holds the full QAReadyReceipt**. R9 `Result.status` and reason codes are distinct from durable `QAReadyReceipt.status=QA_READY`; downstream independent `v1_independent_qa.verdict=PASS` does not let CAB-E mutate `v1_feature_runs.phase=QA_PASSED` (CAB-J Director owns that transition).

## Integrity and flow

1. P2 passes a writer-capability-restricted `store` whose transaction callback can write exactly the six CAB-E tables. CAB-E reads authoritative feature/contract/task rows through P2-injected read snapshots; cannot write CAB-B, CAB-C, CAB-I or CAB-J tables.
2. Reviewer authentication/lineage is independently minted by CAB-H. CAB-E hashes author lineage, compares root ancestry, and denies self/same-author reuse, stale graph, subject drift and unbacked source bytes. Assignments/verdicts bind immutable subject digests.
3. CAB-I provides verified artifact bytes and source/branch readback; `ArtifactPort.get_feature_manifest` and `verify_artifact_bytes` must return provider-verified results with non-synthetic receipts. CAB-E checks every required deliverable binding and bytes SHA, project/feature/bible/contract/graph provenance and active branch commit. Task writer remains CAB-C.
4. CAB-I project adapter runs import, build, smoke and asset verification using the CAB-H trusted native runner. The `TestEvidence` record is schema-exact and includes `commit`, `adapter_digest`, `toolchain_digest`, `input_digest`, observed exit status, log SHA and trusted runner. CAB-E separately reads original command + log bytes via a trusted readback; test doubles or self-reported successes cannot satisfy the production gate.
5. `QA_READY` is emitted only after full required test coverage, successful actual project adapter tests, matched bible clause trace/semantic independent review, complete artifact bytes, exact active graph/contract/bible and immutable byte-backed receipt. A changed commit or changed runner/adapter/toolchain/input digest invalidates the receipt.
6. `record_independent_qa` compares independent reviewer provenance and source SHA to the persisted QA_READY; FAIL/INCONCLUSIVE send a narrow verified failure receipt to `WorkGraphPort.create_rework` after CAB-E transaction completes. Graph revisions are never written by CAB-E.
7. P2 must enforce transaction-level CAS of active `FeatureRun` context and branch-head SHA, and root-scoped idempotency. A provider outage returns `NOT_VERIFIED`/`UNKNOWN_EXTERNAL` or a local retry rather than invented success. CI/GitHub/production host native evidence remains unverified in this P1 source submission.

## Known integration constraints for P2

- The current pinned V1 `app.py` and V1 `contracts/v1.py` are **obsolete**. They are P2-owned and are not modified here. The former `review.api` and `verification.api` frozen Component facades have been removed within CAB-E ownership, not aliased. P2 must register the new `build_quality_port` with the R8/R9 frozen `Result` class and actual writer-limited transactions.
- P2 provides CAB-E `proofs` with `read_record`, `read_artifact`, `verify_test`, `trusted_runner`, `author_lineage`, and `persist_receipt`. This is a *trusted internal dependency*, not a new public runtime port. Production implementations must read native runner/byte provenance from provider or immutable content store and must reject `simulated` receipts. Unit fixtures are marked local-only.
- The frozen SQL provides a uniqueness constraint on `(feature_id,commit_sha)` QA_READY and an index (not uniqueness) for test `(feature_id,commit_sha,test_id)`; CAB-E adds a serial read-and-insert duplicate guard within its own transaction. CAB-E may not add an index to the frozen DDL.
- The review semantic trace must reference the independent review assignment and exact manifest digest, with reviewer lineage independently checked and its signed verdict byte-backed.

Verification in this workgroup: 41 local R9 quality tests (including negative cases); exact imported new-only schema installs under SQLite and `PRAGMA foreign_key_check` returns zero rows. Wider 22-port integration, remote CI, live provider and P3 independent review are **NOT_VERIFIED** here and must run after the 14 workers integrate.
