# WG-F R5 integration contract — operations, resources, reports

This directory implements WG-F-owned business commands against the **frozen**
R5 SQL/OpenAPI shapes. It does not own an APSW connection, the HTTP router, the
GitHub relay, owner grants, or any other group's physical state writes.

## Imports and exact named plans

`from studio.operations import Operations, Scope, plans, request_report`

Pass `plans()` to the WG-C named-plan registry with domain owner `F`. The
returned map is `{plan_id: ("select"|"execute", parameterized_sql)}`.
The `WRITES` map targets only `budgets`, `reservations`, `usage_receipts`,
`reports`, and `notifications`, and all parameter objects are project-scoped.
`SELECTS` may read other domains without mutating their records.

WG-C `CommandRunner.query(scope, fn)` must provide a project-scoped consistent
read. `CommandRunner.command(scope, owner='F', request_id, body_digest, fn)`
must provide an exclusive write transaction with durable, body-bound scoped
idempotency, exact prior response replay, rollback, and audit event insertion.
The `NamedTransaction` callback is used only inside that transaction. Its
`execute_named()` returns **rows modified**, not the SQLite cursor or last rowid.
`record_event()` must atomically append an `audit_events` row with the trusted
actor, event_type/subject_type/subject_id/digest provided by F; WG-F never
inserts `audit_events` directly.

**Budget version seam:** the frozen `budgets` table has no revision column, so
`configure_budget(expected_revision=N)` reads the durable count of exact
`budget.configured` audit events under that `(project_id,budget_id)`. Fresh
budgets start at revision 0, one successful command appends exactly one event,
and duplicate C-replayed commands append none. C must preserve this invariant
under a single-writer transaction. The read is not supported by a covering
index in frozen SQL; a large audit log may make configuration expensive.
Changing frozen DDL requires a WG-M-owned contract decision. Also, the frozen
configureBudget request supplies no `resource_key`, so a newly created budget
currently uses its path `budget_id` for `resource_key`.

## Wire routes / owner mapping

| OpenAPI operation | WG-F call | Response |
|---|---|---|
| `configureBudget` | `Operations.configure_budget` | budget_id, limit_amount |
| `getBudgets` | `Operations.get_budgets` | budgets[] |
| `listReports` | `Operations.list_reports` | reports[] |
| `requestReport` | `request_report` | report_id, state |
| `listNotifications` | `Operations.list_notifications` | notifications[] |
| `ackNotification` | `Operations.ack_notification` | notification_id, acknowledged |

Read methods expose optional **internal** bounded cursor parameters. They do
not change the frozen OpenAPI body shape. Error objects use `DomainError.code`
from the frozen code enumeration. Caller J/A/C must authenticate and authorize
Scope from *trusted verified identity*, not arbitrary HTTP `Scope` fields.

## Internal lifecycle / cross-worker hooks

1. D/G/H calls `reserve` **before** dispatch/external work. The budget mutation
   atomically records `reserved_amount` and a new `reservations` row.
2. G/host supplies a `UsageEvidenceVerifier` returning True only after actual
   provider/tool/build/CI/storage/hosting evidence readback of reference,
   category, amount and SHA-256. Then `consume` atomically settles full reserved
   amount, adds actual spend and stores the immutable provider-tagged receipt.
3. For cancel/reconciliation, the coordinator/G supplies
   `ResourceClosureVerifier`, confirming no possible late charges; only then
   `release` refunds a live reservation. Unknown external effect remains
   reserved. External checks happen outside C's write lock.
4. G supplies `ProfileStore.load_verified(project,digest)` and
   `VerifiedBlobWriter.store_verified(project,bytes,purpose='report')`. The
   report is assembled from bounded observed SQL data with exact source keys;
   hash-matched bytes are durably written before `reports` is committed.
5. I may supply `ReportRelay.queue_report` for configured GitHub channel. The
   report remains `queued` until trusted G/I evidence appears in C-owned
   `effect_intents` with `subject_type='report'`, `action='report.deliver'`,
   and state `observed`. `mark_delivery` performs the final state CAS; the
   frozen trigger independently rejects unsupported `reports.sent`.
   Ambiguous queue retries reuse the same original digest/idempotency key.
6. Owner configuration may contain `schedule` in a verified profile JSON.
   `Schedule.due(now,last_completed_slot)` deterministically calculates slots,
   honoring an explicit time zone and DST. A scheduler must keep the last
   completed slot durably and pass it into the function; the frozen schema has
   no schedule/destination/last-fired relation. This is an **integration seam**,
   not a silently invented project policy.
7. `emit_notification` is a domain-internal preparation command for decisions,
   blockers, failures, resources, oversight and releases. There is no automatic
   owner-policy change, and `mark_delivery('notification','sent',...)` demands
   observed provider effect instead of inferring receipt from local state.

No code starts a browser chat, calls inference APIs, creates a new Git branch,
or treats local SQLite state as evidence of external GitHub delivery.

## QA and ownership

Portable unit tests in `tests/unit/f/test_operations.py` run against an exact
SHA-256 pinned **copy** of the frozen initial SQL fixture. Tests use stdlib
SQLite as an **offline reference only** and in-memory fake C transaction/remote
proof adapters. This is not a production APSW service or independent QA.
WG-C/G/I/J/M integration and live GitHub readback remain `NOT_RUN` here.

Frozen SQL fixture SHA-256:
`3fd131472f90b1fefc51a3db2d7eb93cbfc952b1de4c2527715692ea8843f535`.
