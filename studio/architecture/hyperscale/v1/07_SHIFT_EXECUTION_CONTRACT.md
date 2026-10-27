# Shift execution and successive session contract — V1 proposal

**Design-only.** No existing Studio worker, scheduled task, model executor, source-write gate, owner permission, queue or GitHub workflow is activated by this file. Integrate only after Studio 1.0.0 cleanup and Hyperscale V2 Wave-0 contracts are reconciled.

## Product intent
One human Lead Director issues a bounded shift authorization for one or more departments. Many unique workers then execute a succession of independently bounded response attempts, pulling eligible work from the shared queue, committing/checkpointing real artifacts, sending lifecycle/ledger events and continuing while authorized work remains. Shift completion is controlled by deterministic queue state or an authenticated owner command, **not** a prompt asking the model to run forever.

Do not create a second "AI Shift Supervisor." Existing S00 Production Director/GitHub hourly auditor owns **observation, exceptions and reporting**. A non-LLM execution dispatcher/runner owns job acquisition, checkpoint-aware continuation and STOP decisions. The reporter cannot itself wake inactive ordinary ChatGPT conversations.

## Identity hierarchy
- shift_id: one explicit owner-authorized working period; scope, budget, worker capacity, start and stop policy.
- shift_epoch: monotonic fencing generation for command changes and revocations. Old epochs cannot launch another attempt.
- logical_worker_id: stable CW identity, not a specific ChatGPT tab or response.
- worker_attempt_id: one independent execution call or session; STARTED/YIELDED/SUBMITTED/STOPPED with time, ledger event and evidence.
- job_id / assignment_epoch: approved Studio job and current worker assignment, not inferred from messages.
- lease_generation: narrow source/path lease fencing; a work-queue lease does not grant source-writing privileges.
- queue_cursor: durable input/output replay checkpoint; no queue message is deleted until its idempotent processing effect is recorded.

## Shift state machine
- **OFF:** no owner-authorized shift; no new worker execution.
- **ARMED:** owner authorization exists, resources / credentials / budgets validated, but not yet dispatching.
- **RUNNING:** eligible work may be acquired subject to limits.
- **DRAINING:** acquire no new independent work; finish or checkpoint in-flight attempts, then stop.
- **PAUSED:** do not start new attempts; persist checkpoints; resumed only by authenticated approved command with a new epoch.
- **STOPPED:** no eligible work and no in-flight/retryable deliverables remain, or authenticated immediate stop has fenced all future launches.
- **BLOCKED:** lease, permission, budget, provider, backlog integrity, evidence or other hard gate requires human intervention. A queue with only blocked work is not "all work completed."

Allowed authenticated owner commands: START, PAUSE, RESUME, DRAIN, STOP. Command emission is a trusted control-plane operation with owner authorization, scope, current epoch and immutable audit receipt. A free-form worker message saying "You can stop working now" has **zero** authority. The worker sees a typed, verified STOP decision at its next supported checkpoint or session boundary.

## One response attempt
1. Existing supported runner (user-initiated chat, scheduled task, authorized Work or API executor) verifies shift_id/shift_epoch, owner spending scope, worker assignment and budget.
2. Runner atomically claims an eligible job/continuation if current shift is RUNNING; use current job/path lease epoch and frozen source baseline. If no eligible job, DO_NOT_START / WAIT_FOR_DEPENDENCY / STOP_SHIFT as policy dictates.
3. Model executes one bounded attempt; post STARTED with unique attempt_id, load only selected inbox context and immutable source evidence, then perform assigned work.
4. Before response/time/tool boundary, commit verified source changes/checkpoint and V4 department ledger event. Post HEARTBEAT/CHECKPOINT and terminal YIELDED (resume_required=true), SUBMITTED (eligible for independent QA, not verified), BLOCKED or STOPPED.
5. Runner reconciles actual GitHub evidence and release/renew lease in a transaction; if YIELDED and work remains authorized, queue a **new attempt** with the same logical worker and job, a fresh attempt_id, and compact previous-checkpoint context.
6. Decide NEXT_STEP, WAIT, RETRY, STOP, or ESCALATE using deterministic shift policy and queues. An interactive ordinary ChatGPT chat remains inactive after its response unless the user or an actually supported executor invokes it again.
7. If the whole shift is DRAINING or all scoped eligible work has finished and in-flight/retry queues have drained, stop launching new attempts. Publish an immutable SHIFT_COMPLETED or STOPPED_WITH_BLOCKERS receipt.

## Empty queue condition
"Queue empty" is not enough: a queued/leased count of zero can coexist with in-progress CI, QA, blocked dependencies, delayed retries or incomplete ledger receipts. Only mark CLEANLY_COMPLETED when:
- no eligible READY work in scope;
- no in-flight attempt/lease requiring continuation;
- no pending mandatory independent QA/rework or upstream dependency of this shift;
- no retryable transient failure and no unresolved accepted delivery;
- all required commit/ledger/CI/QA receipts have been reconciled.
If only permission-blocked work remains, stop *new spending* but report STOPPED_WITH_BLOCKERS, preserve the queue and request owner action. Never silently mark blocked jobs complete.

## Failure and timeout handling
At-least-once delivery; one effect per event/attempt/lease generation by unique constraints. A lost model response is INDETERMINATE until external evidence is reconciled; do not automatically start a second writer to the same branch. Stale workers are fenced before reissue. Retry transient provider failures with exponential backoff, bounded cost/attempt ceilings, and dead-letter owner escalation. Periodic hourly audits detect lack of observed progress, not actual hidden process liveness.

## Capacity controls
Global active_attempts, per-department in-flight capacity, per-job max attempts, independent QA capacity, per-repo/path exclusive source leases, approved credit/token budget, provider concurrency and rate limit budgets. These are individually enforced, not "100 workers means 100 source writers." The existing source_writes_enabled=false and auto_ai_enabled=false remain binding. Enabling any continuous executor is a separate owner-reviewed deployment.

## Work-shift termination
- DRAIN: graceful, no new jobs, finish/checkpoint already leased work.
- STOP: fence new claims and continuations immediately; running external executions must obey supported cancellation capability, otherwise stop at next checkpoint while keeping state consistent.
- AUTO_STOP_ON_EMPTY: stop launching once all included work is reconciled, without waiting for an owner to return.
- PAUSE: preserve queue, resumable later under fresh shift epoch and owner authorization.
- Expired authorization/budget: fail closed and report separately, never silently continue.

This semantics applies to each department under the same shared Studio coordinator. It does **not** increase the duration of a single ChatGPT response or guarantee unbounded model/API worker capacity.

## Invariants for implementation and QA
1. A model message cannot create a shift, approve a budget or stop another shift without authenticated owner command.
2. Every attempt has unique worker/job/attempt and matches a current epoch; stale attempts cannot mutate protected state.
3. External timeout + retry does not duplicate source commits, independent QA approvals or job completion.
4. No source writer starts while the live Studio source-write gate is false.
5. Workgroup/department stages continue independently; the Director receives compact exceptions.
6. A stopped shift publishes final partial-completion / blocked-work / retry inventory, not a fictitious all-DONE status.
7. Replay of START, DRAIN, STOP, ACK and retries is idempotent. Test 100, 1k and 10k synthetic worker attempts under concurrent stop/race conditions before activation.
