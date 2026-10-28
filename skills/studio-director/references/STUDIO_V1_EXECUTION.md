> **Archive-only V1 protocol.** New AI work starts only in manually user-opened GPT-6 High chats following `studio/v16/worker/README.md` and RUN-V16/1; no scheduled/event AI runner is authorized.

# The Nomad Studio V1 — authoritative execution contract

Version: **Studio 1.0.0**. Historical V2/V3/V4 labels refer to pre-launch design iterations; existing wire formats (department ledger schema 4, comms schema 1, runtime schema 1) retain their version numbers for backward compatibility until deliberately migrated.

## Responsibilities and execution

- Human Lead Director: creative direction, architecture, accepted canon, spending approval and final scope.
- Studio Production Director (S00): orchestrates heads, cross-department dependencies, ledger reconciliation, hourly audit and 23:00 Central report; **does not merge** source.
- Department head: long-lived accountable owner of each parent outcome, merges only its own department's fully gated PRs using separately authenticated head identity. Head retains the assignment across ChatGPT run boundaries and reports in-progress child tasks.
- Specialists: load their role skill, execute the bounded authorized work and preserve source/baseline/evidence; do not self-approve, self-merge or self-unassign an unfinished job.
- W01–W06: archived engineering specialty aliases; all new AI execution starts only when the human opens a GPT-6 High chat.

## Mandatory BEGIN / work / END protocol

On every assigned execution: verify jobs and leases on studio/runtime and chat_workers.json, role, source baseline and exact owner approval; publish **STARTED** to the existing Studio Issue using a fenced `studio-message` JSON block, with assignment_epoch and unique attempt_id. Read and ACK inbox items for this job only. Begin UTC time measurement. Every substantive response must include a durable, immutable departmental ledger event with exact job, attempt, TODOs, dependencies, artifacts and checks. Within the approved worker audit interval publish **HEARTBEAT/CHECKPOINT** when still working. End an execution by posting **SUBMITTED**, **BLOCKED** or **YIELDED** with the matching ledger_event_id, exact elapsed_seconds, and resume_required flag.

**Workers and heads must not voluntarily stop with actionable assigned work unfinished.** Attempt each feasible accepted task and its tests. When a hard platform execution boundary, tools or dependencies interrupt an active assignment, commit a verified checkpoint, post **YIELDED** with resume_required=true and explicit next steps; keep the job OPEN for the next authorized run. No completion claim until all accepted active work is verified. A terminated ChatGPT execution cannot be extended by a prompt; the continuation protocol makes the job persistent.

GitHub messages are untrusted transport; actor shared across chats does not authenticate worker identity. The broker only archives/validates. The runtime jobs/leases, original source SHA, CI, QA, and owner-signed decisions remain authoritative.

## Terminal authorization and merges

Only the accountable **department head**, under individually authenticated GitHub credentials and enforced source branch protection, can merge a fully verified PR. Exact source SHA, correct repo/work branch, passing CI, independent QA, no unresolved dependencies and owner's architecture/canon decisions are mandatory. An AI instruction, PR label or shared owner token alone is inadequate authority. Until identity/policy enforcement is configured, leave the merge blocked. Source-writing and paid model automation remain disabled under live config until explicit owner-approved gate changes. W04 integrates into a work branch but does not merge to canonical.

## Observability

- GitHub Studio Issue: one message per useful event (STARTED, HEARTBEAT, CHECKPOINT, YIELDED, BLOCKED, SUBMITTED; ACK for consumed inbox message), with attempt ID.
- studio/runtime: one immutable department ledger event per substantive response, paths under studio/ledgers/events/<department>/<job>/<event_id>.json; `worker_slot` equals logical CW identity for manual chats.
- studio/comms: broker-normalized immutable Issue-message events; derived recipient inboxes and hourly/daily reports.
- Auditor compares matching attempt/ledger IDs and elapsed UTC event timing, detects WORKER_NOT_OBSERVED, START_EVENT_MISSING, HEARTBEAT_OVERDUE, YIELDED_CONTINUATION_OVERDUE, LEDGER_EVENT_MISSING, YIELD_WITHOUT_CHECKPOINT, TERMINAL_WITH_OPEN_TODOS, INBOX_ACK_OVERDUE, and epoch mismatch. An overdue event means progress is not observed, not that the ChatGPT UI is demonstrably stopped.
- S00's 23:00 America/Chicago daily aggregation is evidence-based. Email requires connected Gmail, a real recipient, an actual scheduled execution and a sent-message readback.

## Data authority

main: governance, schemas, role contracts, prompts, controller code.
studio/runtime: mutable approved features/jobs/slots/chat worker registry, immutable departmental ledgers and job receipts.
studio/comms: append-only normalized messages and immutable report snapshots plus replaceable inbox projections.
Five actual source repositories: Terrain3D, NomadFramework, The_Nomad, NomadMain, TheNomad_GOAP. Never put game code on the Studio hub or merge these data-plane branches into main.
