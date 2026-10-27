# Unique execution identity and worker-shift semantics

Status: PROPOSED V2 receipt protocol. Existing V1 attempt_id and assignment_epoch remain valid. A UUID proves uniqueness, NOT identity or authorization.

A Studio work campaign has studio_shift_id plus owner-signed shift_epoch. Every persistent logical worker has worker_id (for example CW-ENG-042). Every assigned job has job_id and assignment_epoch. Every bounded chat response/Work/API call has a newly generated execution_attempt_id, which is the user's "per-chat shift". The chat_thread_id is separate and OPTIONAL (only if exposed by the actual execution host); the independent executor_run_id and cryptographic host receipt are only populated if the runner really attests them. Resource lease_generation fences protected code and document writes.

Example fiction (not a real session or verified receipt):
- studio_shift_id = SHIFT-20261027-EXAMPLE
- worker_id = CW-ENG-042
- job_id = FEATURE-42
- assignment_epoch = 7
- execution_attempt_id = 01K8EXAMPLE8RANDOMF4A
- chat_thread_id = null
- executor_run_id = null
- identity_assurance = UNVERIFIED_SHARED_OWNER
- current verified checkpoint = exact Git SHA and/or Google document ID + revisionId
- terminal state = YIELDED (resume_required true)

If the worker resumes, the new response gets a NEW execution_attempt_id, while worker_id/job_id/studio_shift_id persist as applicable. STARTED, HEARTBEAT, CHECKPOINT, YIELDED, SUBMITTED, STOPPED each reference the exact attempt. The Director groups attempts by worker, job, department and studio shift with real source/CI evidence, not model self-assessment.

Authentication/admission is independent: a trusted gateway verifies current owner authorization, assignment epoch, source SHA, budget, executor issuer and scoped short-lived credential before privileged action; source publication also needs lease_generation. A shared-owner GitHub connector cannot distinguish individual ChatGPT tabs. Such events must remain labelled UNVERIFIED_SHARED_OWNER, useful for observation/handoffs but not privileged source writes, independent QA, merge, canon promotion, budget grants or Oversight enforcement. No role/ID string in a worker message becomes an authorization grant.

A true host-integrated runner can issue an ATTESTED_EXECUTOR attempt receipt with signed/verified host execution reference and timestamps, revocation and cost. Ordinary inactive ChatGPT chats cannot be awakened by a database or GitHub Action. STOP/DRAIN fences future attempts; an in-flight response stops through supported cancellation or at next checkpoint. Dead attempts are INDETERMINATE until source/Drive readback reconciles their real effects.

Acceptance tests: one-attempt uniqueness across 1,000 synthetic workers, no duplicate effects on retry, old assignment/lease/shift epochs denied, self-claimed role and spoofed owner notice denied, attempt chain resumes from verified checkpoint without destroying previous submitted evidence, and dashboards never equate unverified worker claims to attested runtime activity.
