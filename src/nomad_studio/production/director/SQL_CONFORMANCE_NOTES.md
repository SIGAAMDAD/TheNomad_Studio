# CAB-J — Frozen R8/R9 SQL conformance

Read contract: `CONTRACTS/initial_schema_v1.sql` and `CONTRACTS/schema_table_owners.json` in the R9 kit. Physical schema must be installed by P2 unchanged.

CAB-J is sole writer for `v1_feature_runs`, `v1_owner_commands`, `v1_operator_receipts`, `v1_dashboard_projections`. CAB-J reads `v1_projects`, `v1_briefs`, `v1_feature_contracts`, `v1_graph_revisions`, `v1_qa_ready_receipts`, `v1_test_executions`, and status-only reads task/dependency/attempt/budget/review tables. CAB-J never directly inserts into `v1_outbox` or `v1_events`, owned by P2; inject a P2 outbox writer scoped to the **same** database transaction. The injected CAB-J transaction capability must reject writes targeting every other table, including via SQL CTE and DDL.

`v1_feature_runs` has a unique `feature_id`, positive `revision`, composite `(project_id,feature_id,active_contract_digest)` FK into frozen `v1_feature_contracts`, and `(project_id,brief_id)` FK into `v1_briefs`. P2 must supply a serial writer transaction, commit atomically with outbox, and rollback on any error. In-process locks alone are insufficient. In tests, transaction `BEGIN IMMEDIATE` plus SQLite FK enforcement is used as a local simulated capability.

`v1_owner_commands` stores canonical command digest, owner, ID and globally unique idempotency key. Stored records do not contain a command operation or body; P2 outbox must preserve exact authenticated command data until ACK, and CAB-H must reauthenticate on recovery. `result_digest` is a state marker, **not** an external quality receipt. `v1_operator_receipts` holds observations, but it never upgrades a QA claim. `v1_dashboard_projections` keeps the paused run's last state for fenced resume and cannot be inferred from chat text.

`v1_qa_ready_receipts` is CAB-E-owned; matching record must exist for **same feature/contract/exact tested commit** before Director advances to `QA_READY`. Director also requires CAB-E trusted observation and at least one passing `v1_test_executions` record on that exact commit. CAB-E remains responsible for deeper independent lineage, bible-compliance, trusted build/import/smoke and immutable byte checks before issuance. This is necessary for Director to avoid forging its own QA readiness.

This note does not certify P2 writer capability, 56-table foreign keys, deployment, a native host, or the final integration suite. The scope is CAB-J SQL usage checked against the frozen contract.
