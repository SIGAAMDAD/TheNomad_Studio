# Department progress ledger V4 — append-only operational contract

## Canonical truths

1. `TheNomad_Studio` GitHub **Issues/Projects**: task identity, owner intent, priority and approvals. Existing issue -> `FEATURE-N` controller admission remains authoritative; do not create competing feature identities.
2. `TheNomad_Studio:studio/runtime`: jobs, worker slots, immutable `studio/receipts` and **department event ledgers**. A ledger is an evidence-linked activity timeline and materialized projection, not an alternative issue status owner.
3. Source repositories and exact Git commits; immutable Drive/doc revisions or real asset bytes and hashes: actual deliverable authority.
4. CI and independent QA records: verification authority. Owner-approved events: accepted/finalized authority.

Google Sheets may read from GitHub as a dashboard. Never require manual dual-writing to GitHub and Sheets and never treat a sheet row as a second authoritative task record.

## Append-only storage

Create one UTF-8 JSON file for **every substantive worker response**, even for planned/blocked/partial progress, at:

`studio/ledgers/events/<department_id>/<job_or_request_id>/<event_id>.json`

on branch `studio/runtime` of `SIGAAMDAD/TheNomad_Studio`. `department_id` must appear in `departments.v4.json`; path components must match `[a-zA-Z0-9_-]+`. Use a stable response/attempt identifier and a unique event ID such as `S03-20261027T201500Z-0001` or `CHAT-20261027T201500Z-<unique-suffix>`; no overwrites. Record `event_id` in JSON equal to basename. For different authored tasks, create separate event files even if the chat response summarizes several.

Read the existing event index/issue before create. Use GitHub `create_file` with the exact branch; retry a transient branch-head collision only after readback confirms the file is absent and current authority is unchanged. If file exists, compare content and treat a semantically identical event as idempotent, not as a new attempt. Never rewrite another worker's ledger or the shared runtime queue; let the deterministic controller aggregate it.

## Required event fields

See [`ledger_event.schema.json`](ledger_event.schema.json) for machine validation. Every event contains:

- `schema_version: 4`, `event_id`, `recorded_at_utc` (offset-aware UTC timestamp), `request_id`, `department_id`, `role_id`, `mode` (`interactive`, `scheduled`, `event_work`, `controller`), `event_type`, `status`, `summary`.
- `issue`: real `{repo, number, url}` or `null` if an issue was not created/authorized; `job_id` and `attempt_id` when known; `worker_slot` when known.
- `todo`: list of `{id, description, state, issue_number?}`. Use `OPEN`, `DOING`, `BLOCKED`, `DONE`; do not delete historic TODOs. A state change is a new event.
- `dependencies`: list of `{issue_number, department_id, state, reason}`; link blockers to actual issues when available.
- `artifacts`: list of `{kind, uri, revision, verified}`; source code uses exact commit SHA, content uses immutable doc revision or repository ref; assets include file hashes.
- `checks`: list of `{name, status, evidence_uri?}`; status `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED`; never infer `PASS` from prose.
- `owner_decisions`: list of explicit unanswered decisions or recorded owner decisions with references.

`status` is a **worker-reported** stage, not proof of authority; report generators reconcile it with Issues, CI, QA, approvals and artifact evidence. A worker may report `SUBMITTED`, but cannot self-certify `VERIFIED` or `FINALIZED`.

## Every created or discovered GitHub Issue must appear in a ledger

When creating a parent or downstream child Issue, record an `ISSUE_REGISTERED` event in the responsible department **in the same work attempt** with the issue's exact number and URL; include `parent_issue_number` where relevant. Create per-department cross-link events for cross-team dependencies. If an Issue already exists, link it rather than duplicating it.

The scanner or 23:00 reconciliation also enumerates the owner-approved and open Issues, verifies that each is represented, and backfills an `ISSUE_REGISTERED` event for missing entries (idempotent key `repo + issue_number + event_type`). A scanner unable to read Issues marks `INVENTORY_UNAVAILABLE`, never claims the ledger is complete.

A downstream issue should have `Parent: #N`, `Department: <id>`, unique task ID, accepted inputs, objective, output format, requested role, dependencies, acceptance gates and protected paths. Do not add the trusted `studio:approved`/`studio:accepted` labels on behalf of the owner; those are verified owner events and label duplication could create a second controller intake.

## Response-final write obligation

Before sending the final substantive assistant response: (1) create the ledger event in GitHub, (2) read back the exact file and its SHA/commit if connector supports it, (3) include `LEDGER_COMMITTED` with a link in the response. If unavailable, (1) retain a complete proposed event payload in the response/work artifact, (2) mark `LEDGER_WRITE_PENDING` with exact reason and responsible department, (3) do not claim the ledger or Issue index synchronized. A successful response without its external ledger write is `HANDOFF_PENDING`, not a completed studio checkpoint.

A daily report is derived from the latest corroborated event per job and Issue plus immutable outputs. If two events conflict, preserve both, use the newest source-authoritative state and mark `CONFLICT_REQUIRES_RECONCILIATION`; never delete history to make an attractive dashboard.
