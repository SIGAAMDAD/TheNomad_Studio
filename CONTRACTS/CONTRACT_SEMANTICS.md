# Binding R5 Forge contract semantics

- All 72 operator API operations use the frozen new-plan path/request/response field shapes; their worker-related semantics are replaced by `operation_semantics.json` and `ARCHITECTURE/09_OPERATION_MATRIX.md`. The HTTP API is for owner/local Studio controls; manually started browser ChatGPT workers use GitHub connector/file relays directly, not the local HTTP API.
- Service behavior must follow `RELAY/PROTOCOL.md` and `RELAY/SESSION_LIFECYCLE.md`. Model inference ports, model-provider continuation, programmatic browser-chat spawning and paid LLM API workers are explicitly **forbidden** and must not appear as alternative code paths.
- `startWorker`: create prepared manual launch assignment; `resumeWorker`: construct resumable prompt/handoff for human; `cancelWorker`: publish STOP relay and await observation; `sendRelay`: publish GitHub file and record remote readback; `ackRelay`: accept only observed recipient-written GitHub receipt. Response `state` describes local operation state, never live browser execution guarantee.
- `request_id` on HTTP mutations is an idempotency key, bound to principal/project/body digest. GitHub `message_id`+immutable path+hash is the relay idempotency unit. A local DB commit is not remote GitHub delivery. UNKNOWN states require readback.
- GitHub authenticated commit metadata and path identity do not authenticate a specific ChatGPT chat under shared user credentials. Worker-claimed authority, owner approvals, QA verdicts and executable requests remain untrusted input until independently verified by authoritative services.
- For state-machine semantics, `CONTRACTS/state_machines.json` and `initial_schema.sql` are binding. Real runtime tests must use APSW; offline SQLite proof is not a host/connector integration PASS.

## R5 hard constraints
- `active_observed` requires exact verified HELLO evidence tied to project/task/lease/Canon/contract, never merely the existence of an outbound message. `HELLO` is not proof of a distinct browser identity.
- `qa_passed` and `completed_verified` require an independently PASS-reviewed original submission with matching source SHA, acceptance profile and linked artifact validator receipt. QA evidence cannot be deleted or rewritten after PASS; a new assessment is a new review.
- SQLite relay observation rows are written only after trusted GitHub readback; C's SQL triggers enforce relational structure, not remote authentication.

## R5 corrected effect classification
`startWorker` and `resumeWorker` prepare local human-launch packets: `external_effect=false`. `sendRelay` is a GitHub external write: `external_effect=true`, with durable intent, readback and UNKNOWN recovery. `cancelWorker` publishes STOP externally. A read-only ACK verification (`ackRelay`) uses already-published GitHub receipt, but no unobserved API-only ACK is accepted.
`relay_receipts` carries the original channel, envelope SHA-256 and contract digest; it can be recorded only after original message verification and trusted GitHub receipt readback. Shared-GitHub authorship is not treated as strong identity.
