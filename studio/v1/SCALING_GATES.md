# Studio V1.5 — 500-worker architecture audit and launch gates

Scope: actual `SIGAAMDAD/TheNomad_Studio` main @ `755740cb46b1ea281876c9eeb7ba17385c4e2908` and `SIGAAMDAD/TheNomad_Studio_Oversight` main @ `d7f926265fb5ed6d1517bf115b6ebe5df7c79f60`.

## Confirmed code-level strengths
- Existing V1 communication broker validates actual approved jobs, worker assignment epoch, role and issue; archives untrusted typed messages and materializes inbox/ACK projections.
- V1 worker-health auditor correlates event attempts, timestamped ledger records, pending TODOs and overdue ACKs.
- Exact SHA source CI receipts and independent QA remain separate from owner acceptance.
- All 174 specialist roles, 12 department heads, and reusable W01–W06 exist as procedures/task profiles.
- Oversight encryption and signed minimal notices are coded and separately tested; production has no approved access to case content.

## Current V1 capacity blockers (not concealed by synthetic tests)
1. `studio_comms.scan_comments` rescans up to **10 pages x 100 comments per Issue** during reconciliation; an Issue exceeding the scan cap can block recovery. Requires cursor-based overlapping incremental reconciliation (V2 WG-B) to preserve missed messages after downtime.
2. `studio_inbox.archive_events` rereads all archived event files and enforces **100,000-event** hard ceiling; scheduled/full projection remains proportional to entire history. Requires sharded journal, incremental inbox cursors and bounded projection (V2 WG-C/D).
3. Broker, CI evidence and 23:00 reports use one GitHub Actions writer concurrency group; 500 comment-created actions imply many git checkouts, API calls and serialized branch writes. Requires durable external queue/batches (V2 WG-C/F).
4. GitHub JSON registry was limited to **250** logical worker IDs. V1.5 raises it to **1,024** but registration says nothing about real ChatGPT execution concurrency. No actual CW workers are yet registered.
5. Shared owner GitHub token cannot authenticate which chat is a department head. GitHub repository rulesets return HTTP 403 (private-repo account feature restriction); head-only merge is not mechanically enforced. Source writes and auto AI stay disabled.
6. A GitHub commit does not wake an idle manual chat. Unattended model executions require actual native Work trigger/Task access or separately authorized external executor/API; Github Actions alone cannot execute ChatGPT responses.

## Targeted V1.5 performance/correctness patch
- Observation-only lifecycle events no longer broadcast to all coworkers or create unassigned inbox jobs. Each accepted event remains in the immutable archive.
- Cap job-wide actionable broadcasts at 64 recipients and require targeted recipient-worker routing for large jobs. Rebuild assigned worker index once per projection.
- Index worker health raw events and ledger records once instead of O(registered workers x events).
- Add synthetic 500 registered-worker / 10,000 observation-event and explicit 500-worker ACK test, negative fanout/identity tests and a microbenchmark. This is not network or multi-chat throughput evidence.
- Replace 190 duplicated prompt blobs with compact specialty/acceptance prompts plus one authoritative execution kernel. Same 174 roles, S00–S09, W01–W06.

## Activation plan
- **Gate A:** run and independently review controller, comms, scale and Oversight CI at exact submitted SHAs.
- **Gate B:** implement and soak-test a cursor/durable queue via the already merged hyperscale workgroup DAG. Hold current broker as shadow compatibility; no direct production switchover by prompts.
- **Gate C:** isolate authenticated department-head merge credentials and owner approval; verify negative merge attempt by junior/QA/Director.
- **Gate D:** register 2–5 actual chat workers, then 25, 100 and 500 as external delivery evidence permits; measure end-to-end comment→inbox→ACK→ledger→CI under outage/retry/reassignment.
- **Gate E:** verify 23:00 Central S00 auditing and actual Gmail sent-message ID.
- **Gate F:** only approve Work credits for a genuine unattended model-execution dependency; deterministic Actions/ordinary interactive chats remain the no-new-Work-credit baseline.

This patch does not provision cloud services, enable source writes, create active agent runs or claim merge protection.
