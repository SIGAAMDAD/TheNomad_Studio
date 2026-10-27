# Deployment boundary, security, service operations and human approvals

## Deployment topology recommendation
Initial production mode remains GitHub V1 until validated. The candidate external service is **Nomad Coordination Service**: one containerized API/ingress tier, one queue/outbox daemon, N independently scalable projector replicas, one primary transactional PostgreSQL instance with suitable backup/PITR, one managed FIFO queue service and versioned immutable blob storage. Redis may be an expendable cache, not the accepted-event journal. AWS SQS FIFO is one recommended managed queue; NATS JetStream and other durable queue products may be evaluated against the same ordering/recovery tests. Avoid building custom consensus protocols.

Run hot ingress outside GitHub Actions. Issue comments remain optional low-trust producer transport; a single externally authenticated gateway handles direct machine producers. A separate well-scoped GitHub App obtains read access to owner-approved jobs and writes only signed audit/manifest projections permitted by policy. No general owner token available to untrusted worker prompts. Studio runtime and Oversight keys must not be mounted into the same general worker process.

## Isolation domains
- **Control plane:** GitHub Studio main governance, runtime receipts and owner decisions; strict Source Registry cross-check.
- **Data plane:** transport records, queue and inbox projectors. Unprivileged workers only read their addressed messages via an authorization-enforcing API.
- **Source CI:** each owning repo runs its own build/analysis and emits exact-SHA evidence; core receives minimal signed receipt and validates source run via API.
- **Oversight:** physically/logically separate key custody, encryption and evidence store, distinct operational account and permissions; only signed minimal enforcement notices may cross to Studio.
- **Executor:** actual ChatGPT user/scheduled/Work/API execution occurs only through available supported product/API capabilities and explicit owner budget approvals. Queues store jobs; they do not execute prompts.

## Minimal endpoint vocabulary (proposed; freeze in Wave 0)
- POST /v2/events: authenticated typed ingress; idempotency-key header plus source provenance. Returns durable receipt ID and accepted offset after commit; rejected messages emit machine code.
- GET /v2/workers/{worker_id}/inbox?after_cursor=...&limit=...: bounded messages assigned to worker's current epoch; requires independent worker identity in privileged mode.
- POST /v2/deliveries/{delivery_id}/ack: effect-idempotent acknowledgment tied to source event, recipient identity and epoch; no code approval.
- GET /v2/jobs/{job_id}/summary: compact state/blocked dependencies/CI proof identifiers from read-only projection.
- GET /v2/departments/{department_id}/pulse: evidence-anchored exception aggregate with paging.
- POST /v2/leases/acquire|release: privileged deterministic controller only; compare-and-swap epoch and verified job authorization; reject from ordinary workers.
- POST /v2/oversight/notices: private service-to-service only; signed owner-scope enforcement requests and replay check, no raw case content.
API versioning and event field schemas require executable JSON schema and negative tests, not only this markdown.

## Production launch blockers
1. Approved owner architecture proposal and current cleanup baseline reconciliation.
2. Actual GitHub App installation and credential readback, including a proof that ordinary worker credentials cannot read Oversight case materials or write privileged runtime files.
3. Validated durable storage backup/restore and inaccessible append-only archive external anchor.
4. End-to-end two-worker pilot: message -> accepted -> inbox -> ACK -> source artifact -> CI -> independent QA -> handoff; no self-approval.
5. Independent QA of a fresh exact integrated commit after every architecture-affecting merge.
6. Measured load trial at proposed tier with cost ceilings and actual rate-limit evidence.
7. Owner-approved deployment/rollback and operational runbook, secret rotation and incident procedures.
8. Keep source_writes_enabled, auto_ai_enabled and reviewer/writer capacities unchanged until separately authorized.

## Studio-owner decision log
- Choose whether to authorize a separate service/repository and cloud infrastructure provisioning or stay with GitHub-only V1.5 for the first pilot.
- Approve per-worker identity issuance mechanism (the current shared owner GitHub connector is insufficient for privileged independent authentication).
- Approve the budget ceilings for storage, queue, test runners and optional model execution.
- Authorize the one integrator that is allowed to edit existing scripts/workflows after cleanup finishes.
- Decide promotion/merge and independent Oversight credential administration.

This proposal does not provision infrastructure, create GitHub Apps, change secrets, schedule recurring paid agents, activate automatic AI, modify source branches or merge protected code.
