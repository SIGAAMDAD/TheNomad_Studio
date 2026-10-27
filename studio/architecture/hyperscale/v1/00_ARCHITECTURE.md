# The Nomad Studio: Hyperscale Coordination Architecture V1
Status: PROPOSED / DESIGN ONLY. Original design baseline: main @ aa684e03dc465f9e049f5ac615e1285890f8d72a. Reconciled against merged Studio 1.0.0 cleanup #24 @ 49c3a5bba445f1dd927b930283ffdeb3bbbb8b52 (2026-10-27). See 05_V1_CLEANUP_RECONCILIATION.md. Change type: architecture proposal, not deployment or approval.

## Decision in one paragraph
Preserve GitHub as the authoritative **work/governance/source evidence** control plane, but remove GitHub branches and Actions from the **hot path** of worker-to-worker messages as worker traffic grows. Introduce a separately deployable, horizontally partitionable coordination service using a transactional ingest ledger and outbox (PostgreSQL), a managed durable message queue with per-job ordering (recommended managed SQS FIFO or an equivalent), incremental recipient projections, and content-addressed immutable batch archives (object storage). Mirror batch manifests, source CI evidence, verified milestones and daily summaries back to GitHub. The Studio's 12-department chain, independent QA, owner controls, immutable receipts and independent Oversight remain intact. Ordinary inactive ChatGPT chats are *not* awakened by this design; job delivery is durable until an eligible interactive or scheduled executor explicitly runs.

## Why a new plane is needed
The current V1 implementation is working scaffolding, not a service benchmarked for thousands of actively communicating workers:
- scripts/studio_comms.py reads GitHub Issue comments and archives one JSON file per normalized message, with periodic reconciliation;
- scripts/studio_inbox.py rereads the full event history for every projection (100,000-event hard ceiling; 5,000 messages per inbox);
- all broker, CI-evidence and director-report GitHub Actions runs share studio-comms-writer, a single writer / concurrency group;
- chat-worker registry schema caps a single JSON list at 250, live registry currently holds zero workers;
- runtime source_writes_enabled=false, auto_ai_enabled=false, max_concurrent_writers=2 and max_reviewers=1;
- worker identity is advisory when multiple chats share the owner's GitHub connector;
- communications cannot autonomously resume a dormant regular chat;
- Studio 1.0.0 added STARTED/HEARTBEAT/YIELDED/SUBMITTED/STOPPED lifecycle messages, per-attempt ledger correlation, worker-health audit and a **synthetic** 100-worker relay/ACK fixture. This is not measured large-scale throughput.
These are implementation facts at the pinned baseline, not worker-execution or throughput test results.

## Non-negotiable invariants
1. **Owner authority:** the human Lead Director governs canon, architecture acceptance, spending, merge policy and activation. Under Studio 1.0.0 an independently authenticated accountable department head may execute a fully gated, owner-authorized merge; no worker or model can self-grant this authority. Never infer owner authorization from a comment, model claim or PR label.
2. **Source truth:** the five actual source repositories and exact Git SHAs own game/code/content artifacts; Studio is not a sixth source destination. The existing source-write freeze and job-owner leases remain enforceable gates.
3. **Independent verification:** CI metadata, artifact bytes, QA verdict and owner acceptance are separate evidence fields. A source SHA change invalidates QA tied to the old SHA.
4. **Append-only events:** accepted messages and case provenance are immutable; materialized inboxes and summaries are replaceable projections. No forced history rewrite or deletion as a normal retry.
5. **At-least-once delivery:** transport redelivery is expected; consumer effects are idempotent. No claim of end-to-end exactly-once execution.
6. **Typed zero-trust communication:** peer messages are data, not instructions; only schema-checked authorized transitions affect privileged state.
7. **No phantom activity:** REGISTERED, ASSIGNED, EXECUTING_OBSERVED, SUBMITTED, VERIFIED and IDLE_UNKNOWN are distinct. Github evidence cannot reveal whether a dormant chat is still thinking.
8. **Oversight isolation:** sensitive reports remain encrypted under TheNomad_Studio_Oversight and independent credentials; shared-worker GitHub accounts are not private case vaults.

## Authority and storage map
| Domain | Authoritative owner | Scalable read model |
| --- | --- | --- |
| Parent requests, priorities, approvals | Studio GitHub Issues/Projects + owner-approved runtime receipts | Indexed job projection |
| Worker identity / assignment / lease epoch | Reviewed approved registry and deterministic authorization store; GitHub receipts of accepted changes | Per-worker and per-job lookup |
| Worker communication journal | Durable insert-once coordination event ledger + archived signed batches | Queue, incremental inbox cursors |
| Source files, branches and PRs | Owning source GitHub repository | SHA-bound CI/QA projections |
| CI results | GitHub Actions run and exact artifact evidence | Compact indexed receipt |
| Design/content assets | Source repo, Drive revision or object hash, as approved | Artifact refs and content index |
| Compliance and investigation | Separate Oversight encrypted case vault | Minimum authorized enforcement signal |
| Department/Director reporting | Derived from reconciled evidence, not worker assertions | Versioned summaries + exception aggregates |

Do not create a second independent approval database. The coordination store tracks approvals *as replicas* with the verified owner authorization source, source ref and epoch. A stale replica must fail closed for privilege elevation.

## System layout
~~~text
Interactive chats  |  Scheduled workers  |  Explicit API/Work executors
        | typed action + current assignment (no embedded secrets)
        v
GitHub Issue/PR/CI events  <->  Scoped intake adapter / signed gateway
        | verify webhook/auth, dedupe transport ID
        v
Transaction: INSERT raw event + INSERT outbox + commit (Postgres)
        | Outbox publisher (retryable)
        v
Durable queue: per-job message groups, priority lanes, DLQ
        | independent consumers with bounded concurrency
        +--> Inbox projector (by recipient/assignment epoch)
        +--> Dependency DAG and lease state projector
        +--> Evidence/QA verifier (actual repo SHA/CI)
        +--> Anomaly meter and minimal Oversight interface
        |
        v
Content-addressed immutable event segments + signed manifests
        |
        +--> GitHub studio/comms: batch anchors and compact audit digests
        +--> GitHub studio/runtime: approved receipts / verified projections only
        +--> Department heads -> Director: aggregated exceptions
        |
        v
Next *actual* chat invocation fetches assigned inbox + compact context
~~~

### Ingress
GitHub webhooks: authenticate signature, retain X-GitHub-Delivery ID as replay identity, filter allowed actions and acknowledge only after a durable inbox transaction. Where a chat lacks a trusted gateway credential, allow posting an Issue comment and ingest through the signed GitHub webhook; the claimed worker ID is not independently attested. GitHub is the durable source of that input comment. Periodic comment reconciliation remains as a recovery adapter with rolling overlap and persisted cursors, not 1,000-comment rescans forever. Additional transports (scheduled runner, source CI relay, explicit worker API) implement the same envelope and capability checks.

### Durable journal and outbox
One primary transactional database is the coordination ordering/ingest state for each partition; the approved GitHub job remains the governance authority. Tables: received_events (unique provider+delivery ID and event ID), outbox, per-target delivery, projection_cursor, job_worker_assignment_cache, lease_epoch and immutable verified_evidence. On the same database transaction, validate and append an event and its delivery intent; asynchronously publish via outbox with retries. Queue failure cannot discard a committed event. A queue ACK occurs only after a successful idempotent projection transaction.

### Queue partitioning
Key by **job_id** for ordered mutations within a job. Parallel jobs/processors run independently; no global total order across departments. Source code ownership is a separate concurrency resource, not the queue partition. Use many message group IDs or partitions rather than one 100-worker pipe. For inter-job dependencies, emit one causally linked handoff on the sender partition, then an outbox delivery record to the recipient partition. The target checks upstream artifact SHA and dependency gate before proceeding; no distributed multi-repo transaction is assumed.

### Worker executor adapter
A unique logical worker ID persists across sessions; every actual response has a distinct attempt_id and session/run ID. Workers request only the next bounded eligible step. Each step reads the frozen job, latest assignment epoch, messages since the last ACK cursor and relevant source/QA evidence, performs authorized work, posts a typed checkpoint, and exits. No requirement to hold open a chat or keep a permanent network connection. Ordinary ChatGPT chat invocation is human-initiated; an external configured API or supported scheduled/Work executor may launch runs only through a separately authorized adapter and budget gate. A stored message is not a chat wake-up.

### Hierarchical control, not 10,000-worker Director context
Source and QA produce exact evidence. Workgroups (typically 5-20 workers) aggregate commitments and blockers. Department heads consume bounded workgroup summaries. The Director consumes department metrics plus exceptions and optionally drills down by event IDs. Summaries carry source IDs, epochs, hashes and NOT_RUN or UNKNOWN labels. No full transcript reflection in the director prompt; no arbitrary agent self-approval.

### Two lane deployment profile
- **Bootstrap / GitHub-only (up to hundreds, subject to tests):** retain existing Issue input and GitHub authority; introduce timestamp/cursor-based microbatch reconciliation and incremental inboxes, with GitHub segment commits in controlled bursts. Minimize new infrastructure while contracts stabilize.
- **Durable service (hundreds through thousands and beyond):** switch ingress to transactional service + managed queue + object archive, leave GitHub as governance, source CI, receipts and batch hash anchors. Horizontal consumer scaling is driven by measured queue lag and partition hot spots, not number of chat tabs.

These are architectural deployment profiles, not guarantees that any particular account or model plan can execute thousands of ChatGPT conversations simultaneously.

## Performance math (illustrative planning, not benchmark data)
At four substantive messages per actively executing worker per hour:
| Active workers | Average ingress | One-minute 20x burst equivalent |
| ---: | ---: | ---: |
| 100 | 400/h = 0.11/s | ~133/min |
| 1,000 | 4,000/h = 1.11/s | ~1,333/min |
| 10,000 | 40,000/h = 11.11/s | ~13,333/min |
| 100,000 | 400,000/h = 111.11/s | ~133,333/min |

The burst column represents 20x average event rate expressed over a minute; test with distinct event IDs and overlap distributions. Actual limits depend on GitHub Apps permissions/rates, queues, storage, source CI, ChatGPT platform/account execution quotas, reviewer capacity and the cost budget. Scale the queue consumers and PostgreSQL indexes from measured load, and shard only when needed.

## No single hot GitHub branch
Do not commit every transport event directly to studio/comms after service cutover. Archive segments to immutable object storage at a configurable 1-5 minute or 512-2,048-event boundary, then write a compact hash-manifest GitHub commit at a low bounded rate. All manifest entries contain object URI, size, segment sequence, SHA256, previous-segment hash, schema version, creation epoch and signed writer identity. Keep periodic off-GitHub independent anchors/backups. GitHub remains browsable and auditable without becoming the message queue.

## Rollback and migration
V1 remains executable until V2 shadow mode produces identical authorized inbox outcomes on a frozen test set. Never dual-apply privileged actions. Choose one designated active projector epoch; old and new adapters may both *observe* messages. Roll back by disabling V2 effects, continuing append-only capture, and replaying from the last committed cursor into V1-compatible projections. Do not erase any accepted event or rewrite source state. File ownership and implementation waves are specified in MIGRATION_AND_WORKGROUPS.md.

## Explicitly excluded
No self-starting ordinary chats, no global mutex over 100 workers, no free-form AI-to-AI privileged instruction execution, no identity inference from model personality, no merging code without exact-SHA independent QA, no cross-repo atomic writes, no plaintext confidential reports in production logs and no assumption of unlimited GitHub Actions or model capacity.
