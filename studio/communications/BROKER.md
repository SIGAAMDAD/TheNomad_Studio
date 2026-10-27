# V1 broker, event routing and retention

## Inputs and outputs
Input: structured JSON posted on a Studio job/parent Issue as a comment, or a separately authenticated GitHub App transport in a future revision. No new parent FEATURE issue is created for a message. A fenced message is untrusted until validated.

Output: a broker-normalized JSON record in studio/comms at events/YYYY/MM/DD/<event_id>.json. Its identity is deterministic from (source repository, comment ID). An acknowledgment is a separate event. Each event records the original source, actor login, claimed worker identity, job and lease epoch, source commit SHA where present, addressee, immutable evidence refs, correlation/causation IDs, and bounded summary.

Event kinds: STATUS, CHECKPOINT, DEPENDENCY_REQUEST, DEPENDENCY_RESOLVED, HANDOFF_READY, QA_REQUEST, QA_RESULT, CI_RESULT, BLOCKED, ACK. Messages are data, not privileged commands.

Derived compact context:
- daily/YYYY-MM-DD-v1.md, or v2 for corrections; never overwrite an earlier version;
- snapshots/YYYYMMDDTHHMMSSZ-<digest>.json; immutable, sparse, generated at milestones.
Original events remain intact forever under ordinary operations. An external immutable backup/manifest is required for stronger protection from repository administrators.

## Reconciliation loop
1. Receive issue_comment.created for latency, plus scheduled reconciliation of all job Issue comments for completeness. Never rely on GitHub Actions concurrency to retain all pending events.
2. Deduplicate by (source repository, comment ID). Parse fixed fields; enforce maximum event size and type allowlist.
3. Read the actual studio/runtime job, approved dependencies, source baseline, assignment and writer lease. The proposed chat-worker registry is separate from the existing S00–S09 scheduled slots.
4. Normalize authenticated transport provenance; role and job assertions from the comment alone cannot authenticate a worker.
5. Validate the normalized schema. Verify SHA, CI result, QA independence and owner approval against their actual sources before any authoritative promotion.
6. Batch new events. One broker writer creates a fast-forward commit on studio/comms; on competing head change it re-reads and deduplicates before retry. Never force push or delete.
7. Publish addressed inbox projections and a daily digest only after a successful commit.
8. Fail closed on blocked lease, conflicting messages, missing evidence, invalid identity, sensitive content, or ambiguous authority. Retain a generic rejection code in a protected log; avoid reflecting secrets.

## Semantics
At-least-once delivery, idempotent effects, no promise of global ordering across jobs. A source comment may be changed or deleted before ingest; only ingested events are archived. Message delivery means recorded; ACK means consumed; QA PASS means asserted until independently verified; source complete requires final source commit, Drive receipt, exact-SHA CI, independent QA and owner acceptance where applicable.

Cap forwarding depth, unread backlog and per-job message rate. Prefer compact, evidence-linked events over repeated agent-to-agent discussions. The controller performs no model calls and does not itself launch a regular ChatGPT chat.

## Official GitHub constraints
Commits made with a default GitHub Actions GITHUB_TOKEN do not normally trigger new workflow runs; workflow_dispatch/repository_dispatch are exceptions. Use separately reviewed GitHub App identity when external event chaining truly requires it.
https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow