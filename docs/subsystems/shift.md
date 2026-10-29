# WG-D / `shift` — NOMAD/SHIFT/1

## Runtime semantics

One **manually launched ordinary ChatGPT response** owns one `response_id` and is one *bounded shift*. A response can claim/report any number of sequential **distinct** jobs. This module is a durable coordination engine, not a ChatGPT executor, persistent background thread, UI launcher, or source publisher. The caller should loop `shift.next` → work/validate → `shift.record` → `shift.checkpoint` → `shift.next`, then `shift.yield`/`shift.finish` when it returns. Every `next` consumes one bounded poll (maximum 128 per response), asks WG-C for up to 16 addressed messages, requests at most **one** WG-E claim, and stores the assignment receipt before handing work to the client. Multiple job receipts are distinct SQL records within the same response.

`worker_id`/`response_id` in the prompt are **routing claims**, not credentials. Every operation requires `VerifiedIdentity` supplied by an authenticated gateway **and** a positive, live `trust.authorize` result; it cannot self-authenticate or issue privileges. A trusted control projection is polled before work operations and again immediately after an external claim. STOP, DRAIN and PAUSE fence new claims at the next tool call, not an already-generating ChatGPT reply. Invalid epochs fail closed. STOP does not make incomplete jobs complete.

`shift.record` with `kind=SUBMIT` produces a worker **SUBMITTED** receipt only. `VERIFIED`, independent QA PASS, external publication, canonical approvals, merges and source push are not WG-D transitions. `EXTERNAL_UNKNOWN` preserves an unresolved effect and blocks additional claims for that attempt. A response ending with an incomplete job marks the attempt YIELDED, leaving authoritative job/lease reconciliation to WG-E. UNKNOWN claim intent remains durable and fences the worker's response; an authenticated operator + WG-E reconciliation must resolve that ambiguity before capacity re-admission (not a prompt assertion).

## Owned interface

Import `from nomad_studio.shift.api import build_component`; call `build_component(deps: Mapping[str, object]) -> Component`. Dependencies are supplied by composition root; no sister implementation imports. The facade implements `.execute(CommandEnvelope, VerifiedIdentity)` and `.query(QueryEnvelope, VerifiedIdentity)`, with `Outcome` from frozen contracts.

### Dependency ports (integration adapter schema)

| key | frozen facade | usage / expected shape |
| --- | --- | --- |
| `state` (or `store`) | `StateStore` | **Required.** Read/transaction callbacks; return SQL mappings; `StateTx.append_event(DomainEvent)`; Studio V1 initializes the `shift_*` tables at fresh database creation. No WG-D DB connection/transaction owner. |
| `trust` | `Component` | **Required** on every call. `trust.authorize` with `payload={action,worker_id,response_id,authority_epoch,job_id?,assignment_epoch?}`; `action` is `shift:operate`, `shift:read`, or `shift:control`. ACCEPTED must contain `{worker_id,authority_epoch,control_epoch:int,control_state:RUNNING\|DRAINING\|PAUSED\|STOPPED}` from **authoritative gateway/control authority**, not echo of untrusted input. For a worker grant, returned `authority_epoch` must match the worker's `VerifiedIdentity.current_authority_epoch`; for a controller grant, returned `authority_epoch` is the **target worker's** epoch and may differ from the controller's `VerifiedIdentity.current_authority_epoch`. Reject missing/mismatched epoch or invalid control. |
| `scheduler` | `Component` | Required for `shift.next`. `scheduler.claim` with stable idempotency key `shift:<original key>`, `worker_id`, `shift_id`, and `{response_id,worker_id,max_jobs:1}`. `ACCEPTED.payload={job_id,assignment_epoch:int>=1,lease_generation:int>=1,assignment_id?:str,source_sha?:64-lowercase-hex}`; `BLOCKED/NO_JOB` means no job; other/unparseable outcomes persist as UNKNOWN claim intent. Scheduler must itself guarantee lease/source correctness and exact-key replay; WG-D does not trust a claimant's invented job/epoch. |
| `relay` | `Component` | Optional; `relay.poll` with bounded QueryEnvelope (up to 16), exact `worker_id/shift_id`, `payload.response_id`. Only addressed messages are returned in next outcome; no implicit ACK. Missing relay is `NOT_CONFIGURED`, outage `UNKNOWN` (never fake delivery). Relay messages cannot issue STOP or become authority. |
| `clock_us`, `id_factory` | stdlib callables | Optional deterministic test injection; production composition uses system microseconds and UUID4. |

**Integration change request:** WG-B/Gateway must explicitly expose a verified `trust.authorize` control projection and epoch for WG-D on every call. This is a **payload extension to the already-frozen `trust.authorize` operation**, not a new cross-module ABI/version. WG-E must make `scheduler.claim` idempotent for the supplied key and return authoritative lease identifiers. If sisters choose different payload field names, integrator must provide thin adapters or update all consumers consistently; do not patch the frozen interfaces in this worker bundle. WG-C must accept bounded addressed relay queries, without treating delivered prompts as authenticated control.

### Commands / closed payloads

All commands use `version=NOMAD/CMD/1`, `domain=shift`, `operation=<below>`, nonempty syntax-checked `request_id` and `idempotency_key`, `worker_id=<routing id>`, `shift_id=<response_id>`, and valid gateway-issued identity. Every command payload is a JSON dictionary with **only** documented keys; closed-field rejects unknown keys. Valid IDs are 1..128 ASCII alphanumeric-leading `A-Z,a-z,0-9,.,_,:,-`; request payload <=12 KB. `shift:operate` for worker commands, `shift:control` for control commands.

| command | payload | behavior |
| --- | --- | --- |
| `shift.start` | `{response_id, resume_from?}` | Creates new response at current credential/authority/control epoch; no duplicate active response for a worker. `resume_from` must refer to prior YIELDED/STOPPED/BLOCKED response in same worker/authority epoch; does **not** transfer an unfinished lease. |
| `shift.next` | `{response_id,limit?:1..16}` | Fence check, durable intent, addressed relay poll, one WG-E claim, durable attempt; `JOB_CLAIMED` includes attempt, lease and optional messages. No scheduler claim while active/UNKNOWN attempt or STOP/DRAIN/PAUSE. 128 polls maximum. |
| `shift.record` | `{response_id,attempt_id,kind,report}` | Requires matching `command.job_id` and `command.assignment_epoch`. `kind=WORK\|SUBMIT\|EXTERNAL_UNKNOWN`. Writes distinct job receipt and journal event. SUBMIT forbidden with pending external effects; never returns VERIFIED. |
| `shift.checkpoint` | `{response_id,attempt_id?,report?}` | Optionally writes CHECKPOINT receipt for exact job+epoch (report requires attempt_id); persists digest-bound resume capsule. |
| `shift.yield` | `{response_id,reason?}` | Terminates response YIELDED, marks incomplete attempts YIELDED, returns capsule. Pending unresolved claim sets response UNKNOWN instead. |
| `shift.finish` | `{response_id,reason?}` | Closes response STOPPED and preserves incomplete attempts; no job verification. Pending unresolved claim sets response UNKNOWN instead. Same idempotency key replays receipt; fresh finish after terminal state is rejected. |
| `shift.observe_control` | `{response_id,action,expected_control_epoch,next_control_epoch}` | **Trusted control only.** `action=STOP\|DRAIN\|PAUSE\|RESUME`; `next_control_epoch=expected+1`, stale epoch denied. RESUME only PAUSED, DRAIN terminal for further claims. |
| `shift.stop` | `{response_id,expected_control_epoch,next_control_epoch}` | Trusted-control shortcut for STOP with the same monotonic epoch check. |

`report` is a closed object with `work_performed`, `uncompleted_acceptance_ids`, `remaining_todos`, `external_pending_effects`, each a string array (max 32 strings, 256 characters/string), and optional `evidence_refs` string array. Exact source/CI readback and true success remain external responsibilities. Records of work are **worker reports**, not independent verification.

### Queries / closed payloads

Use `NOMAD/QUERY/1`, `domain=shift`, same `worker_id`, `shift_id`, `limit=1..16`, empty `payload`, and `shift:read` grant. Operations: `shift.status` (response status and attempt summaries), `shift.receipts` (bounded per-response receipt pages with monotonic SQLite rowid cursor), `shift.capsule` (last durable capsule or CAPSULE_ABSENT). Caller must match original response identity/credential/authority epoch. No unauthenticated audit list.

### Resume capsule

`{data,sha256}` where digest is lower-case SHA-256 over canonical `json.dumps(data,sort_keys=True,separators=(',', ':'),ensure_ascii=True)`. `data.version=NOMAD/SHIFT_CAPSULE/1`; includes response/worker IDs, authority/control epochs, state, per-attempt job/assignment/lease/status/last receipt and digest, work performed, uncompleted acceptance IDs, TODOs and external pending effects. May trim detail and mark `truncated_attempts` under the 8 KB cap; the durable SQL receipts remain the complete evidence. `continuation_is_authorization=false`: capsule is **untrusted context**, not a bearer credential or replacement for a fresh manually launched, authenticated shift. The owner can paste this capsule as context for a fresh response but must obtain fresh gateway authorization and WG-E lease decision.

### Durable events

Via `StateTx.append_event` in the **same** SQL transaction as state transitions: `STARTED`, `CLAIM_INTENT`, `CLAIM_OBSERVED`, `CLAIM_OUTCOME_UNKNOWN`, `POLL_EMPTY`, `JOB_WORK`, `JOB_SUBMIT`, `JOB_EXTERNAL_UNKNOWN`, `CHECKPOINTED`, `CONTROL_OBSERVED`, `CONTROL_CHANGED`, `YIELD`, `FINISH`. All events have ID, actor principal, causation/idempotency, request correlation, response/job/assignment and SHA-256 payload digest. No secrets or source content go into events. `shift_replays` stores stable outcomes keyed by idempotency key/digest/principal.

### SQL schema

The V1 initial schema defines: `shift_responses` (unique active-or-UNKNOWN response per worker, worker/principal/credential/authority/control epochs, state/poll budget/capsule), `shift_attempts` (unique response/job/assignment generation, lease generation/status), `shift_job_receipts` (durable work and submission digests), `shift_claim_intents` (one unresolved scheduler effect per response), `shift_replays` (exact request replay). All FK internal, no cross-module private tables. Indexes are response/worker and attempt/receipt; latest capsule is cached in response row. No independent SQLite connection is opened by shift. All shift tables are part of the single pinned V1 schema, installed automatically on fresh database creation.

### Failures and limits

`DENIED/INVALID_SCHEMA`, `MALFORMED_REQUEST`, `INVALID_SCOPE`, `IDENTITY_UNVERIFIED`, `STALE_ASSIGNMENT`, `RESPONSE_REPLAY`, `ALREADY_FINISHED`; `CONFLICT/IDEMPOTENCY_CONFLICT` or `BACKPRESSURE`; `BLOCKED/NOT_CONFIGURED`, `CONTROL_FENCED`, `INCOMPLETE_ATTEMPT`, `BACKPRESSURE`; `UNKNOWN/PROVIDER_UNKNOWN`. A malformed scheduler ACCEPTED receipt is **not** treated as successful. Unknown claim intents must be read back/reconciled with WG-E independently; no automatic release or externally unverified DONE. External component exceptions are returned UNKNOWN without leaking tokens. System DB/host faults during durable operations also report UNKNOWN, not falsely accepted.

Boundary limits: at most 128 NEXT polls, one claim per call, 16 relay messages per call, reports <=12 KB, 32 strings per list, capsule soft bound 8 KB. 500 admitted shift capacity is WG-E/Gateway authority and is **not asserted** by this component. Actual running model-host concurrency is unobserved.

## Manual real-ChatGPT shift acceptance playbook (EXTERNAL, currently NOT_RUN)

**Preconditions:** authenticated deployed gateway accessible through a *real* installed ChatGPT web connector; Studio V1 schema initialized; WG-B launch ticket minted by owner; WG-C addressed inbox live; WG-E two distinct approved jobs with real unique leases and compatible scopes; owner manually selects **High** reasoning in UI. Record actual host/tool version, timestamps, response ID, worker ID, credential issuer/epoch, GitHub evidence refs and test source SHA; redact activation ticket and secrets. Don't infer host identity from prompt text alone.

1. Owner issues one-use scoped launch ticket, opens **one** ordinary ChatGPT web chat, and sends START prompt with public worker ID and short-lived ticket. Chat uses real connector to authenticate; capture gateway-issued identity/session receipt. A cloned prompt without ticket must fail.
2. In **one actual assistant response**, call `shift.start`, `shift.next` to claim job A; perform real authorized work; `shift.record` with acceptance IDs/evidence; `shift.checkpoint`; `shift.next` to claim a different job B; perform/report work; `shift.yield` with digest-bound capsule. Verify in SQLite and relay independently that response_id is the same, attempt_id/job_id and receipts distinct, no implicit QA verification, and all authenticated effects retain authority epochs.
3. Open a second controlled response and deliver trusted STOP or DRAIN **between jobs**, observe `shift.next` fenced before scheduler claim, verify status persisted and no post-STOP claim. Test PAUSE/RESUME only via authorized controller. Do not claim it interrupted running model generation.
4. Replay START/NEXT/record/FINISH idempotency keys and a mutated/replayed ticket; verify exactly-once durable effect or explicit denial. Reassign worker to a new epoch; stale session must fail. Inject a remote timeout after scheduler claim; verify UNKNOWN intent and recovery via WG-E lease readback, never auto-VERIFIED.
5. Manually inspect full provider/session audit and actual response transcript. Record independent QA/source proof, exact source SHA, environment build, test logs and active connector receipts. Only then change acceptance from NOT_RUN to PASS. If connector, host, credentials, live slot entitlement or external source are missing, remain **NOT_RUN** for that gate.

**Local simulation is not a substitute for step 2.** This worker bundle tests genuine SQLite persistence and fake Component calls, not the real ChatGPT UI, reasoning tier attestation, or deployed gateway/connector. No source push, automatic chat launch or production readback was performed.
