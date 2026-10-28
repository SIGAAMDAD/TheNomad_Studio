# RUN-V16/1 - manually initiated High response attempt

**Only** a user-opened High chat initiates an AI response. One response is one bounded `attempt_id`/shift; it may contain many internally batched operations and generated messages. `job_id`, `worker_id` and assignment epoch persist across responses; the next user-opened response has a **new** attempt ID. Do not infer thread ID, execution attestation, idle status, model runtime or elapsed wall time when those are not available.

## Startup / permission boundary

Before the first GitHub or Google Drive connector call in each chat, list the exact repo/ref/path or Drive folder/file, intended read/write operation and reason; ask the owner for access confirmation. Only then request platform connector consent. A consented app still does **not** grant Studio source/canon/publish authority. `run-startup` describes the access preflight and has `external_connector_called:false`; do not store an arbitrary `true` as a way to authorize a request. For fully supplied ZIPs, `access_preflight:[]` and `execution_mode:LOCAL_PATCH_ONLY` are valid; carry on offline without a connector. Never use shared owner credentials for worker publication. No gateway => deliver binary patch and exact handoff to independently authenticated head/owner.

## Work and evidence

Use local snapshot + exact archive SHA and (when independently verified) remote main source SHA; restrict edits to owned paths, create a local baseline checkpoint, batch editing/testing, export exact binary patch. A local Git SHA is NOT a verified GitHub commit. `run-receipt` identifies one attempt, observed status, evidence hash, actual tests with exit codes, source baseline and optional host attestation. If identity is `UNVERIFIED_SHARED_OWNER`, no privileged transition is implied. `EXTERNALLY_ATTESTED` is valid only if verified by the execution host/gateway, never the worker's self-assertion.

`run-resume` carries only small immutable fields: job, attempt, worker, epoch/lease, source baseline/snapshot, scope prefixes, checkpoint digest, paths, tests evidence digest, uncompleted acceptance IDs, blockers, next executable step. On resume, explicitly compare against current authenticated assignment/lease, current source commit and hash before applying edits. Preserve previous receipts. No raw confidential case data, credentials, giant traces or chat history in the capsule.

State evidence retains V1 observations: `STARTED`, `HEARTBEAT`, `CHECKPOINTED`/V1 `CHECKPOINT`, `YIELDED`, `SUBMITTED`, `STOPPED`; `BLOCKED` denotes inability to proceed. Local exit does not equal CI success or independent QA PASS. An independent subject hash gate and owner-accepted publication are later steps.
