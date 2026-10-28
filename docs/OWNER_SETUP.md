# Owner setup — V1.6 manual High pilot

This page replaces obsolete scheduled/Work event-trigger setup advice. **Do not turn on auto AI execution, source auto writes, old task subscriptions or GitHub PR worker triggers.** The human owner manually opens each High chat; deterministic CI/audits may still run without launching models. Existing historical job receipts and original GitHub events are preserved.

## Immediate local workflow

1. Select one approved bounded job and verify its source repository/target branch using `studio/workforce/source_targets.v1.json`. Freeze a source snapshot/commit SHA and owned paths. Get owner/architect approval for WG-A's `AUTH-V16` and `RUN-V16` contracts before promoting downstream patches.
2. Open a GPT-6 High chat using `studio/v16/worker/MANUAL_LAUNCHERS.md` (or locally generated launcher). Attach source archive and any RUN-V16 resume capsule. Load only its assigned one of 174 specialist role manuals and its accountable department head.
3. Work offline against the immutable snapshot, test locally and batch a binary patch. If GitHub/Drive must be accessed, have the chat ask you for the **first** exact resource+operation+reason before the connector call. Shared-account consent cannot authenticate a worker for privileged source publishing.
4. Require a machine-readable change manifest, actual test outcomes and one response audit report. An independent reviewer verifies exact patch/source hash; an independently authenticated owner/head handles publication/readback. QA worker source access is read-only and integration worker is staging-only. Neither role may merge canonical branches.
5. Each new chat response is a distinct attempt/shift. Use the compact RUN-V16 resume capsule, verify current source SHA and assignment/lease before external writes, and continue unfinished work. If no durable ledger/relay was written, require a local pending receipt and do not mark it persisted.

## Deployment later (not done by WG-B)

WG-C builds PostgreSQL journal/leases; WG-D provides isolated gateway and real scoped authority; WG-F maps runtime/state and cursors; WG-G removes old PR-to-ChatGPT routing and adds GitHub/Drive publication adapters; WG-H runs independent failure/scale tests; WG-I performs reviewed shadow rollout and owner-gated cutover/rollback. Branch rulesets, source write rights, canon, privileged merges and spend require separate actual setup and external negative tests. A successful local unit test cannot prove these permissions.
