# Hourly shift audit, statistics and email contract — V1 proposal

The existing GitHub Actions Studio Director pulse at :16 America/Chicago is the evidence producer; the existing S00 Director remains owner of accountability and exceptions. A separate *email-delivery-only* ChatGPT automation was authorized on 2026-10-27 for :35 each hour, using the owner's connected Gmail account. This task must not start workers, approve source writes, modify queue or send confidential Oversight case content.

## Source and timing
- Preferred input: most recent immutable studio/comms/reports/hourly/YYYY/MM/DD/timestamp-digest.json; include its exact UTC timestamp, generated central date, GitHub file ref and last verified checkpoint.
- Corroborate new results from studio/runtime job/department ledgers, actual owner-approved GitHub Issues, source repo PR commits, exact SHA CI, independent QA and worker STARTED/YIELDED/SUBMITTED/STOPPED messages.
- If the report is absent or older than 90 minutes, construct explicitly labelled LIVE_FALLBACK from current GitHub evidence, and issue an auditor health alert. If all evidence sources fail, send MONITORING_BLOCKED instead of inventing an "idle" conclusion.
- Email once per completed hour at :35 Central, using deterministic subject with reporting-hour timestamp; search Gmail SENT before retry to avoid duplicate delivery.
- Hourly auditor and email task are observers. They do not wake ordinary ChatGPT tabs or change worker execution duration.

## Required observability (all nullable / NOT_MEASURED)
- shift_id and verified state (OFF/ARMED/RUNNING/DRAINING/PAUSED/STOPPED/BLOCKED or UNKNOWN); owner authorization ref and epoch, where available.
- eligible queued, claimed/leased, currently running *with attested executor receipt*, blocked, QA pending, retries due, delayed, dead-letter, submitted, exact-SHA VERIFIED and owner-accepted job counts, with differences from previous hour.
- registered logical workers, observed attempts, active runtime attempts (only if attested), stale/missing heartbeat count, YIELDED resumable attempts, STOPPED and failed attempts, sources of missing evidence.
- submitted artifacts, exact source commits by owning repo, CI PASS/FAIL/CANCELLED, QA PASS/REWORK, regressions and dependency resolutions.
- inbound/outbound messages, pending ACK, oldest unacknowledged age, accepted/deduplicated/rejected queue count, oldest queue lag, hot job/partition and source lease conflicts.
- cost: authorized vs consumed token/currency budget only when actual provider measurement is available; GitHub Action run minutes; optional storage/API request amounts if metered.
- department and role cohort metrics; compare junior, senior, department-head and QA performance across bounded consistent work types without equating lines of code with useful output.

## Computation semantics
Only calculate metrics when both numerator/denominator have evidence:
- productive_attempt_hours = sum of separately verified, non-overlapping attempt elapsed_seconds / 3600; label worker-claimed timing where host attestation is missing.
- verified_throughput = number of *new exact-SHA independently reviewed and accepted job completions* / productive_attempt_hours, otherwise NOT_MEASURED. Do not use PREPARED/TESTED as VERIFIED.
- handoff_latency = ACK event time minus accepted original event timestamp, for valid ACKs of eligible current recipients.
- QA_wait = independent verdict time minus accepted submission time for that exact source SHA.
- regression_rate = reopened or QA REWORK jobs divided by reviewed jobs for that comparable cohort and reporting period.
- model efficiency = verified outcome per billed unit only if the actual billed model/effort and cost are recorded by an authorized execution host. User-supplied model labels are not billing evidence.
- 0 completed work with 0 verified active attempts is INSUFFICIENT_EXECUTION_EVIDENCE, not evidence of poor worker performance.

## Email layout
Subject: The Nomad Studio | Hourly Shift Report | YYYY-MM-DD HH:00 America/Chicago
Body: date and source freshness; shift mode; throughput/current state; changes since last hour; per-department cohort; stalled worker/job IDs; CI/QA/test summary; delivery lag; cost where measured; owner decisions and deep links. Highlight NO_ACTIVE_WORK_OBSERVED if appropriate, distinguish monitoring failures and stale reports from worker failures.

## Failure policy
- If EMAIL_BLOCKED, record reason and do not assert a sent email.
- If event/ledger inbox evidence mismatches, report CONFLICT_REQUIRES_RECONCILIATION, retain all original refs.
- When shift has no approved work, do not start any worker for the purpose of generating a nicer report.
- A missing model response is NOT_OBSERVED, not an engine crash. Detect actual process crashes only with attested executor failures, runtime logs or specific CI run errors.

## Evaluation and adaptation
After a meaningful amount of real verified shifts, optimize based on observed bottleneck: source lease collisions, reviewer starvation, transport lag, timeout/retry rates, context load overhead, work type and model/effort economics. Change concurrency limits through reviewed controlled configuration, not spontaneous worker claims.
