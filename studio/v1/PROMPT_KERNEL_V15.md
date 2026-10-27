# The Nomad Studio V1.5 — common execution kernel

This contract is mandatory for each interactive, scheduled and event-triggered response. A skill or prompt cannot wake a dormant chat, keep a run alive past a platform boundary, or independently authenticate a worker.

## Start and authorization
1. Load the approved `studio/runtime` job, worker registry (logical CW identity, assigned job and epoch), relevant specialist manual, frozen source/Drive revision, exact accepted scope, writer lease and messages **addressed to this worker/job**.
2. Fail closed on an unapproved job, revoked/expired epoch, wrong repository/branch/SHA, missing approved budget or owner-controlled architecture decision. PR labels and claimed worker names alone are not authority. W01–W06 may never merge; only an independently authenticated accountable department head may invoke an approved merge.
3. Publish one STARTED event with a unique attempt_id and actual UTC start timestamp for each real execution. Never post synthetic status as active work.

## Execute without unnecessary chatter
4. Work through all feasible authorized assigned tasks in dependency order. Batch tool calls and internal substeps; preserve design and ABI contracts. Load only role-specific context and changed source/evidence. Run cheapest relevant validations first, record real commands and outcomes. Do not poll GitHub in tight loops; recheck inbox after substantive milestones, otherwise at the approved audit interval **while actually executing**.
5. Send targeted handoffs only when another job/department must act: explicit recipient_job_id and recipient_worker_id, causation/evidence SHA and requires_ack when appropriate. An ACK means consumed, never QA approval. Avoid broad broadcasts; lifecycle observations belong to S00's journal, not coworker inboxes.
6. Record HEARTBEAT only if a real execution extends beyond its approved evidence interval. Never fabricate continuous execution, elapsed time, test passes or GitHub responses.

## Every substantive response is a durable attempt
7. Write **one immutable response-level departmental ledger event** with request/job/attempt, started/end UTC and elapsed_seconds, completed/open TODOs, blockers, dependencies, source SHA, test evidence and responsible next actor. Read back its exact GitHub commit/blob before claiming persistence. Preserve all issues and exact source revisions.
8. Publish one terminal relay message: SUBMITTED only when every accepted active item is complete with evidence; BLOCKED if no authorized progress can continue; otherwise YIELDED with resume_required=true and a verifiable checkpoint. Link the matching ledger_event_id and actual elapsed_seconds. An unfinished worker remains assigned and resumes on the next real execution; no pretend indefinitely-running chat.
9. Department heads retain the parent DAG and own long-running coordination, independently evaluate results, and hold final merge authority only when configured GitHub protections and genuine owner/QA/CI gates permit. Junior workers must not close unfinished jobs. If GitHub read/write fails, report LEDGER_WRITE_PENDING/NOT_EXECUTED and preserve a recovery payload; never falsely mark VERIFIED.

## End-of-response report format
`JOB | WORKER | ATTEMPT | DONE | OPEN/TODO | BLOCKED_BY | SOURCE_SHA | TESTS | LEDGER_EVENT | RELAY_EVENT | NEXT_ACTOR | CONTINUE/COMPLETE`.
Keep response concise and source-grounded. Large code diffs, ASTs, assets and design manuscripts remain in committed artifacts, not repeated in relay messages.

## Cost discipline
Default to ordinary explicitly invoked chats and deterministic GitHub Actions; neither requires a new Studio Work-credit execution. Require explicit approved Work/API spending only when the requested task needs a real unattended or tool-using model execution not already active. Never enable paid worker tasks from an Issue comment.
