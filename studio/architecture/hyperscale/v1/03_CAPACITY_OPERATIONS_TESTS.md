# Capacity engineering, failure recovery, observability and tests

## Configurable operating budgets (proposed defaults)
These are unverified engineering targets, not measured promises or account/platform entitlements.
- Routine message aggregation window: 30-120 seconds for external durable adapter; 1-5 minutes for Github-only V1.5. Events are durably accepted immediately, not held in memory for the window.
- Batch by **both** elapsed time and size: 512 events or 2 MiB uncompressed, whichever comes first; tune from benchmarks. One hot job may be split into sequential segments while preserving per-job accepted sequence.
- Hot queue payload: <=8 KiB bounded event metadata, with large diffs, ASTs, logs, meshes and case files stored as referenced immutable artifacts.
- Projection: update only the touched (job_id, worker_id, assignment_epoch) keys; write one aggregate per workgroup and per department as needed, not 10,000 Git commits.
- Healthy latency targets (external service): event durable-ingest P95 <=5 s, eligible inbox visibility P95 <=60 s, dead-letter alert <=5 min, director aggregation freshness <=15 min; latency for a dormant ChatGPT chat is outside the transport SLO.
- Test active concurrency: 100, 1,000, 10,000 logical active attempts plus a 100,000-record dormant registry fixture; multi-hour and multi-day soak runs. Start smaller before provisioning infrastructure.
- Backpressure: per worker/job/department rate bucket, bounded in-flight queue, dead-letter after capped retries, automated retry with jitter, priority traffic reserved headroom, no silent drops.
- Profile CPU, allocations, DB locks, queue depth, store size, archive bandwidth, GitHub Actions minutes, API rate headers, CI queues, reviewer lag and aging paths. Never scale on worker-count alone.

## Capacity tiers
| Stage | Target registered / actively posting | Data path | Gate |
| --- | --- | --- | --- |
| V1.5 | 250-1,000 / 100-250 | GitHub Issue input + cursor microbatches + incremental inbox; strict bounded API queries | 100-worker real input pilot + 24h synthetic soak |
| V2 | 10,000 / 1,000-5,000 | durable DB+outbox, managed queue groups, immutable segment archive, GitHub hash anchors | 1,000 active simulated workers + 72h burst/chaos |
| V3 scale-out | 100,000+ registered / 10,000+ active target | horizontally partitioned worker projections and storage; separate app credentials, quotas and queues | independent bench proof under workload and budget; no guaranteed ChatGPT runtime capacity |

No absolute upper cap is implied; the physical limit is infrastructure provision, budgets, provider quotas, contention and real executor availability. A registered dormant chat costs primarily metadata; actively executing model work consumes platform-specific resources the Studio cannot allocate by fiat.

## Partitioning / fairness
- Preserve ordering **within job**, not across unrelated jobs. Use consistent-hash partition ring keyed on job_id (initially 64 logical partitions in test; configurable), assign per-partition consumer leases with generation fencing. Partition count and process count are independent.
- Each consumer polls small batches, commits idempotent projections and advances durable sequence cursor. Retry missing predecessor before applying a later dependent transition. Horizontal scale increases consumers until DB/queue becomes limiting.
- Cross-job dependency DAG has explicit edges and pinning; a high-priority event may be processed quickly but cannot bypass its prerequisite. Use weighted fairness per department and protected capacity for BLOCKED/QA_REQUEST/CI_FAILURE without starving ordinary work.
- Hot single-job contention remains serialized by job design; split giant jobs into approved child jobs with narrow source ownership, not unsafe parallel mutations to one job record.
- API quota management: one shared GitHub App rate-budget meter per installation, ETag and conditional fetches, batch reconciliation per repo, exponential backoff on secondary limits, independent fail-closed credential domains.

## Failure table
| Failure | Intended detection | Correct recovery |
| --- | --- | --- |
| GitHub webhook timeout/missed event | signed delivery ID and cursor gap | redelivery + overlapping API reconciliation |
| Repeated webhook / queue redelivery | unique delivery/event table constraint | no-op existing effect, preserve provenance |
| Database committed, queue publish failed | outbox pending age | retry publisher, no lost accepted event |
| Projection committed, queue ACK lost | effect-idempotency row | re-deliver then idempotent ACK |
| Worker never resumes | unread inbox age | mark WAITING_FOR_EXECUTOR; notify owner, never impersonate a chat |
| Worker changes assignment | epoch mismatch | fence old ACK/lease; reroute pending under new epoch after approval |
| CI artifact expires | artifact index + digest not independently verified | keep CI metadata only; status NOT_RUN / missing artifact |
| Broken source SHA/QA mismatch | exact-source verification | invalidate review; route rework |
| Partition/consumer outage | backlog time, cursor, DLQ | replay shard from last durable cursor |
| GitHub unavailable | API errors and store lag | accept durable input only where authenticated; defer GitHub state mutation and reconcile afterward |
| Private Oversight case leaked | confidential information detector / access audit | quarantine, incident response, revoke credentials; never repost case to production |
| Unapproved worker coalition | message rate/authority anomaly | flag independent review; no behavioral intent inference required |
| Archive manifest divergence | segment hash chain and signed remote anchor | freeze publishing; compare original segments, restore from immutable backup |
| Unbounded cost / model retries | budget gate and per-job rate meter | stop new executor dispatch, preserve queued work |

## Verification matrix
1. **100 simultaneous events** into one workgroup; all accepted event IDs recover, one effect each.
2. **1,000 and 10,000 workers** with mixed job distributions and controlled skew; assess hot-job tail latency and storage amplification.
3. **100x bursts** compared with steady hourly submission, with source CI and QA traffic mixed in.
4. **100% webhook duplicate replay** plus reordered events; no duplicate job transitions or acknowledgments.
5. **Crash-point injection** at ingress before commit, after commit before queue publish, after projection before ACK and during archive.
6. **Reassignment during delivery**; old assignment_epoch cannot ACK, receive privileged source material or hold write lease.
7. **Concurrent branch/path ownership**; intersecting protected paths cannot both pass source-write gate.
8. **CI SHA flip** after reviewer approval; prior PASS invalidated for the new SHA.
9. **Action cancellation, provider 429/403, token rotation and API outage**; work stays recoverable with gap reports.
10. **Archive corruption and checkpoint replays**; end-to-end chain and signed manifest detection.
11. **Prompt-injection and spoofed "owner" text**; no action from untrusted worker prose.
12. **Oversight access negative test**; production identity cannot decrypt/read confidential complaint.
13. **Manual ordinary chat never receives a wake-up**; queued messages remain available until its next supported invocation.
14. **Cost-throttle**; no unapproved model billing, source writes or auto merges.

## Required measured report
For each phase publish worker count, active attempts, partitions, event rate, arrival histogram, P50/P95/P99 ingest and inbox lag, queue oldest age, replay duplicates, orphan count, DB CPU/locks/index size, memory, GitHub API calls and secondary throttles, daily storage growth, source CI queue, QA completion lag, model invocation count and explicit PASS/FAIL/NOT_RUN. Attach raw benchmark configuration, commit SHAs and immutable artifact digests; do not report performance numbers as achieved until the tests run.

## GitHub Actions role at scale
Use Actions for CI, static analysis, review packet creation, batch-mirror validation, scheduled reconciliation and owner-approved deployment gates. Do not put every chat message through a serialized Actions invocation. A monitored gateway and durable queue handle hot ingestion. GitHub workflow concurrency groups can still protect small exclusive operations; they are not substitute FIFO queue storage. Use repository_dispatch only for verified cross-repo evidence triggers, and independently read back actual source runs before permitting QA transitions.

External documentation:
- GitHub webhook best practices: https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks
- GitHub Actions concurrency and pending-run queue: https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
- SQS FIFO grouping: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-fifo-queue-message-identifiers.html
