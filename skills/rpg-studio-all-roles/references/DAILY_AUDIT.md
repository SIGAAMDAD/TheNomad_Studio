# 23:00 Central studio audit and email

## Cadence and authority

At **23:00 `America/Chicago` every calendar day**, a deterministic scheduled Studio audit runs (without a model execution) against the authoritative GitHub Issue inventory, `studio/runtime` jobs/slots/receipts/department event ledgers, actual source PRs/commits/CI/QA, and versioned content artifacts in Drive. Use the IANA zone, not a fixed UTC hour, so daylight saving transitions are respected. The human opens a High chat if AI synthesis is desired; a deterministic scheduled audit may collect evidence and send a configured report without launching any ChatGPT model.

A Skill alone cannot create the schedule or send the email. The deterministic scheduler must be configured and verified externally; Gmail delivery requires an authorized connector and a verified recipient. Record actual sent message ID. If delivery is impossible, retain the report and mark `DAILY_EMAIL_BLOCKED`; don't claim it was delivered.

## Reconciliation algorithm

1. Read owner-approved and open Studio Issues and Project fields, preserving issue ID, title, state, priority, parent, department, labels, issue link. Include every Issue in at least one department's ledger projection. Backfill missing `ISSUE_REGISTERED` events deterministically when authorized. Do not convert stale inferred state into a trusted owner event.
2. Fetch current runtime jobs, immutable receipts, role slots, accepted output revisions, source PR state and exact-SHA CI/QA. Canonical evidence overrides a worker-reported status.
3. Group all ledger events by `(repo,issue_number,job_id,department_id)` and order by timestamps/event IDs, accounting for multiple worker attempts. Deduplicate repeated retries by stable key. Do not count a task done merely because an event says DONE.
4. Derive **today's completed items** with links to actual verified artifacts, **in progress**, **blocked**, **QA/rework**, and **owner decisions**. Carry forward every unfinished TODO with owner/dependency, age, exact next action, and source link. Report totals and real worker utilization where observable.
5. Construct a **cross-department waiting-on matrix**: blocked department/job -> provider department/Issue/artifact -> blocking gate -> elapsed time -> next action. Flag dependency cycles and unassigned blockers.
6. Identify unreported activity, unreadable source, missing ledgers and stale milestones as `UNKNOWN`/`UNREPORTED`, not zero work.
7. Save one canonical dated report artifact and its immutable revision, then email a concise digest plus the link. Persist send success/failure evidence without overwriting immutable worker events.

## Daily email headings

- **Studio pulse:** verified completions today, active jobs/Issues, newly opened/closed Issues, rework and blockers.
- **Department summaries:** deliverables, exact revisions/PRs, work attempted and next TODO.
- **Dependency matrix:** `waiting_department | issue/job | waiting_on_department | required artifact | status | next unblock action`.
- **QA/integration:** statuses and exact source SHAs/Drive revisions, CI results, failed checks, owner acceptance gates.
- **Owner decisions:** only actionable, grounded questions; no invented approvals.
- **Data gaps:** ledgers or sources that could not be verified, schedule or email delivery failure.

The email and report are observations of what **actually happened**. A nightly skill run does not replace GitHub Actions' deterministic source/CI validation.
