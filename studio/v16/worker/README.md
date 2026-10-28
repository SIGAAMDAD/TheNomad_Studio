# RUN-V16 manual High worker kernel (WG-B)

**Only manually opened GPT-6 High chats execute Studio AI jobs.** One assistant response is one bounded attempt/shift, possibly containing many internal tool calls and batched GitHub/Drive relay messages. No GitHub PR, Issue label, SQL entry, Drive event, hourly task or GitHub Action starts an AI chat. Older W01–W06 and S00–S09 identities are historical specialties, not scheduled model runners. This directory is a **prompt/registry/tooling implementation**, not an identity service, gateway or database.

## Single execution kernel

1. **Intake and authorization.** A human opens the High chat and supplies an approved bounded job or verified checkpoint. Select one of 174 exact role IDs, assigned head and one manual specialty. Check the job's repository, baseline SHA, owned paths, protected paths, accepted deliverables, independent reviewer, budget and architecture decisions. A job file, a role name, a connector or a human-readable approval claim cannot authenticate grants. If privileged work requires external validation, request an isolated authenticated gateway with `AUTH-V16/1`; absent it, work **LOCAL_PATCH_ONLY**.
2. **Before any GitHub/Drive connector call:** Present a specific access preflight including provider, exact repo/ref/path or Drive file/folder, operation (READ or WRITE) and reason; obtain the owner’s explicit confirmation before making the first call. Consent is platform resource access, **not** source-write/merge/canon authority. If uploaded files suffice, make **zero** connector calls; `run-startup` has empty `access_preflight` and `external_connector_called:false`. Never use shared owner/admin connector credentials to publish. If no external access is needed, do not ask for it.
3. **Snapshot and resume.** Verify input archive SHA256; record independently observed Git SHA only if retrieved by trusted readback. An archive's reported Git SHA is a claim. Load the `RUN-V16/1` startup schema, only the assigned specialist manual and relevant changed files (not all 174 manuals). For a continuation, validate old checkpoint digest and compare authenticated assignment epoch, lease generation, current source baseline and exact owned path list before any external write. Every new response gets a new attempt ID; do not pretend a continued chat ran in the background.
4. **Work.** Use a local worktree based on the exact snapshot. Perform all feasible authorized work, group edits and tests locally; no per-file GitHub/Drive calls. Junior: bounded fixtures/scaffolding and narrow files; midlevel: bounded completed features; senior debugger: reproducible failures and scoped repair; senior integrator: **staging worktree only** with independently QA-verified upstream SHA; independent QA: **source read-only** at exact subject SHA and no self-review. All must respect allowed paths and preserve ABI/serialization/source provenance. Heads coordinate dependency DAG, do not invent authority. Escalate architecture/canon/budget changes to owner, not peer improvisation.
5. **Validate and package.** Run targeted then full available checks; report commands, actual exit codes, hashes and every NOT_RUN test. Verify changed paths against ownership. Before isolated gateway deployment, deliver a binary patch or edited source archive and source SHA manifest for independently authenticated owner/head publication. After gateway deployment, use exact `PUBLISH-V16/1` intent only with independently verified principal, writer lease, owner grant and provider readback. Workers never merge to canonical branches. For QA, produce PASS/REWORK independent finding; worker's own tests are not independent QA.
6. **Durable attempt/relay.** Produce a response-level audit report card: `JOB | WORKER | ATTEMPT | STARTED/END OBSERVED | DONE | OPEN/TODO | BLOCKED_BY | SOURCE_BASE | CHECKPOINT_SHA256 | TESTS | LEDGER | RELAY | NEXT_ACTOR | CONTINUE/COMPLETE`. Publish a verified `RUN-V16/RECEIPT/1` and a compact `RUN-V16/RESUME/1` where durable infrastructure is authorized; without verified provider writes, output local files plus `LEDGER_WRITE_PENDING` and `RELAY_NOT_SENT`. Do not fabricate wall time, host attestation, lease, elapsed duration, GitHub commit, ACK, QA or external status. Send only addressed handoffs through an approved relay; no broadcast.
7. **Terminate accurately.** `SUBMITTED` only after every accepted active item is implemented/tested to available gates and its artifact has a verifiable checkpoint. `YIELDED` with `resume_required:true` for unfinished feasible work at an execution boundary; `BLOCKED` for an unmet dependency. Keep the assignment open until a verified external controller closes it. Preserve receipts; an attempt is not a continuing agent outside a response.

## One-screen continuation capsule

`RUN-V16/RESUME/1` carries `{job_id, worker_id, attempt_id, assignment_epoch, lease_generation, source_repo, source_base_sha, source_snapshot_sha256, owned_path_prefixes, checkpoint_sha256, changed_paths, tests_evidence_sha256, uncompleted_acceptance_ids, blocked_dependency_ids, next_action}`. Resume from exact hashes and reverify external authority; do not repeat the entire chat or role roster. No full traces, secret credentials, Oversight case details or hallucinated host IDs.

## Who starts what

Human owner opens chat -> department head assigns a verified bounded job -> human opens the worker chat with its specific prompt -> worker returns local outputs and evidence -> independent human/head QA and authorized staging publisher perform readback -> owner controls deployment and canon. All management and specialist roles can be exercised within a manual chat; no 1:1 paid task identity is required.

Frozen authority/seams: [`AUTH-V16`](../contracts/AUTH-V16.md), [`RUN-V16`](../contracts/RUN-V16.md), [`PUBLISH-V16`](../contracts/PUBLISH-V16.md), [`legacy-retirement`](../contracts/legacy-retirement.v1.json). WG-C/D/F/G own the database, gateway, runtime and provider code; this worker directory implements none of those services. All historical W/slot labels and receipts remain archive-only and are not converted into live triggers.
# Multi-job response service

The manual host can call `manual_kernel.run_bounded_response` with an authenticated
`ResponseAccess` client. Load the pinned role brief once per invocation. Each new
invocation has a new response ID; each claim is durably associated with it before
work begins. Complete and checkpoint eligible jobs until a real STOP/DRAIN/PAUSE,
no eligible work, or a bounded resource limit. Completing one job does not end the
response. Controls are checked between jobs; they do not interrupt a running tool.

The service records authenticated control observations and every attempted job.
A missing outcome stays UNKNOWN. Report cards separate reported evidence from
independent QA and retain partial failures. A resource boundary produces YIELD
with the actual resume capsule; only an observed authenticated STOP produces a
STOP receipt. Workers receive no SQLite path, database handle or service secret.
