# Studio resilience and efficient shift execution — source-grounded patch program

**State:** PROPOSED ONLY — no new service, model automation, credential, source write or protected merge authorized by this plan.

**Evidence:** uploaded Studio and Oversight `main` snapshots, matched Studio plan blob to live main `505d8e59003e5d5c938c46bc056370ca2a2802fa` (2026-10-27). Existing V1 remains authoritative. Both uploaded suites passed locally (**167 Studio**, **23 Oversight**) but do not prove high-concurrency operation.

This is an **additive hardening delta** to the existing ten-workgroup / four-wave migration, not a replacement. `hardening_patches.v1.json` is the authoritative machine-readable patch list. No requirement here permits editing live Studio runtime or real Oversight secrets without separate owner approval.

## Necessary architectural additions

- Verified, independently authenticated worker and execution-host identity; explicit owner/department-head decision proofs.
- Durable cross-system source commit / CI / Studio receipt reconciliation, including orphan patch recovery.
- Explicit shift executor capability contracts, admission budgets, terminal states and deterministic stop/drain.
- Versioned resume capsules to reduce repeated context loading without allowing stale state to authorize actions.
- Fail-closed assignment epoch delivery and source-path worktree leases.
- Real CI artifact-byte provenance and pinned action/toolchain dependencies.
- Independently keyed Oversight intake and owner-approval readback, not merely signed URL strings.
- Restorable archives, immutable external anchors, retention policies and an auditor watchdog in a different failure domain.
- Actual chaos, failure-injection, source-to-QA and cost/load gates before scaling.

## Patch dependency outline

`H00 -> H01 -> (H02, H03, H05) -> H04`

`H01 + H03 -> H06 -> H07`

`H01 + H03 + H06 -> H08 -> H09`

`H02 + H03 -> H10`, `H01 + H03 -> H11`, `H03 + H06 + H08 -> H12`

`H00, H06, H08-H12 -> H13 -> H14`; `H02-H12` also gate H14.

Workgroup IDs below match existing `workgroups.json`; **only WG-I modifies existing scripts/workflows** during authorized final integration. WG-I's H13 includes a narrow P0 repair pass; truly emergency fixes require a separate source-approved hotfix, not a parallel unreviewed edit.

### H00 — Baseline truth, P0 defects and regression inventory

**Owner:** WG-A · **Wave:** 0 · **Priority:** BLOCKING · **Depends:** None

**Evidence**
- scripts/studio.py:285-320 — record(jobs,slots,receipt) reads unbound config in valid code SUBMITTED path
- scripts/studio_inbox.py:103-117 — ACK eligibility uses current routing, not original recipient assignment epoch
- scripts/studio.py:100-110 — scheduler validates exactly ten S00-S09 slots; CW registry is separate
- tests/test_studio.py and tests/test_studio_inbox.py — baseline suite green does not prove these positive and stale-epoch paths

**Objective:** Freeze the current main/runtime SHA and establish executable regression fixtures for known correctness and ownership gaps before enabling a mass shift.

**Owns / expected touch points:** tests/coordination/contracts/**; tests/studio preflight fixtures under authorized WG-I integration only

**Contracts / changed behavior**
- P0 defect inventory with stable failure IDs, exact source SHA, owner-approved correction path
- V1/V2 compatibility matrix preserving STARTED/HEARTBEAT/YIELDED/SUBMITTED/STOPPED and ledger V4

**Preserves**
- Existing S00-S09 scheduled slots, source-write/auto-AI gates, V4 ledger ownership

**Out of scope**
- No silent edits to live controller in this planning PR; fixes must be independently authorized and reviewed

**Compile/static, runtime and failure injection checks**
- Positive valid source submission (gate on) must not NameError, gate-off must reject; stale ACK and future epoch replay rejected
- Reconciliation failure is detected after source-target import and recoverability tested

**Completion gate:** All P0 repro tests written and linked; source fixes must be merged with exact-SHA CI and independent QA before production pilot

### H01 — Single authority and identity policy snapshot

**Owner:** WG-A · **Wave:** 0 · **Priority:** BLOCKING · **Depends:** H00

**Evidence**
- studio/v1/OPERATING_CONTRACT.md — owner-controlled merge policy and authenticated accountable heads
- scripts/studio_comms.py:272-291 — shared-owner actor identity is not independent worker authentication
- studio/architecture/hyperscale/v1/01_CONTRACTS_AND_SEMANTICS.md — v2 authorization is a proposal

**Objective:** Freeze policy revisions and require cryptographically separable actor, owner approval and shift/assignment epochs for any privileged transition.

**Owns / expected touch points:** services/coordination/contracts/**; tests/coordination/contracts/**

**Contracts / changed behavior**
- PolicySnapshotV2 includes immutable approval ref, verifier, digest, scope, expiry, revocation generation
- WorkerPrincipal, ExecutorAttempt, ShiftAuthorization, SourceLease and OwnerDecision typed schemas
- Capability-matrix policy: observation, inbox read, source write, QA, merge, spending, oversight enforcement

**Preserves**
- GitHub Issues/approved runtime are owner-policy authority; database caches only validated revisions
- Independent review and exact SHA remain separate from successful CI

**Out of scope**
- No default privilege elevation from a model role, Issue label, shared owner token or signed-looking URL

**Compile/static, runtime and failure injection checks**
- Unknown/stale/revoked policy revision or unverified issuer fails closed
- Synthetic owner-forgery and role-spoof messages cannot become approval

**Completion gate:** Independent architecture approval freezes signed policy contract; every privileged adapter has executable negative tests

### H02 — Authenticated ingress and principal-bound event deduplication

**Owner:** WG-B · **Wave:** 1 · **Priority:** BLOCKING · **Depends:** H01

**Evidence**
- scripts/studio_comms.py:336-352 — Issue comment scanner uses bounded per-Issue pages, no durable cursor
- scripts/studio_comms.py:272-309 — actor login and worker_id are distinct trust strengths

**Objective:** Authenticate provider delivery and logical worker where supported, limit abuse, and preserve a recoverable cursor for missed events.

**Owns / expected touch points:** services/coordination/ingress/**; tests/coordination/ingress/**

**Contracts / changed behavior**
- Webhook signature and installation identity verification; binding from principal->worker->assignment_epoch
- Persistent source-event idempotency key and monotonic reconciliation cursor with overlap and gap alerts
- Per-identity rate budget, rejection/quarantine and bounded payload/provenance normalization

**Preserves**
- Untrusted Issue comments remain archive-only without independent actor assertion
- At-least-once input with no lost accepted event under retry

**Out of scope**
- No granting model credentials directly to ordinary prompts or private Oversight case reads

**Compile/static, runtime and failure injection checks**
- Replay, wrong installation, stale credential, spoofed worker and duplicate deliveries rejected or recorded once
- API rate limit, deleted comment, long Issue history and webhook outage recover without dropping accepted inputs

**Completion gate:** No privilege granted by unverified transport identity; missed-delivery replay and source lineage are tested

### H03 — Transactional accepted events and GitHub publication saga

**Owner:** WG-C · **Wave:** 1 · **Priority:** BLOCKING · **Depends:** H01

**Evidence**
- studio/architecture/hyperscale/v1/00_ARCHITECTURE.md — transaction/outbox exists as target, not service
- scripts/studio.py:359-385 — local receipts and GitHub updates are separate operations
- scripts/studio_ci_evidence.py:190-204 — immutable CI receipt files are a separate publication boundary

**Objective:** Implement an accepted-event transaction with an outbox and a convergent reconcile protocol for commits, CI receipts, inbox state and job state across GitHub and the coordinator.

**Owns / expected touch points:** services/coordination/core/**; tests/coordination/core/**

**Contracts / changed behavior**
- Per-job ordered sequence; accepted event insert plus outbox same database commit
- PublicationSaga state machine: PREPARED -> COMMITTED_SOURCE -> VERIFIED_SOURCE -> RECONCILED_STUDIO or RETRY/QUARANTINED
- External side-effect key = job+attempt+operation+source SHA; GitHub compare-and-swap and read-after-write
- Dead-letter queue and monotonic durable cursor with idempotent replay

**Preserves**
- Source repo owns actual commits; GitHub owner approval remains authoritative
- Queue broker remains at least once; never claim exactly-once execution

**Out of scope**
- No automatic force-push, branch rewrite or cross-repo distributed transaction

**Compile/static, runtime and failure injection checks**
- Kill process between source commit and ledger recording, and after DB commit before queue publish
- Double event replay creates one logical effect and no duplicate source patch
- Orphan commit/receipt and GitHub outage reconcile using exact remote SHA

**Completion gate:** All acknowledged accepted IDs recover after crash; no duplicate protected side effects or fabricated VERIFIED state

### H04 — Backup, immutable archives, lifecycle retention and disaster recovery

**Owner:** WG-C · **Wave:** 1 · **Priority:** BLOCKING · **Depends:** H03

**Evidence**
- studio/architecture/hyperscale/v1/04_DEPLOYMENT_AND_SECURITY.md — PITR/backup stated without measured restore gate
- scripts/studio_inbox.py:23-40 — 100,000-event full-history hard ceiling
- scripts/studio_worker_health.py:11-58 — health scan has a finite file ceiling

**Objective:** Remove unbounded filesystem scans and define tested restore, compaction, retention and independent integrity anchoring.

**Owns / expected touch points:** services/coordination/core/storage/**; tests/coordination/core/restore/**

**Contracts / changed behavior**
- PITR and replica failure contract; owner-approved RPO/RTO by failure class before release
- Immutable segment index, signed external anchor, periodic hash reconciliation and manifest reader
- Retention/GC roots pin unresolved jobs, QA, external artifacts, legal hold and Oversight case separation
- Restore procedure for DB, outbox, cursors, archived objects and authority snapshot

**Preserves**
- Accepted-event immutability; retained evidence remains discoverable by stable ID
- No cold archive deletion that invalidates pending deliveries or audit manifests

**Out of scope**
- No custom consensus protocol or unreviewed active-active multi-region topology

**Compile/static, runtime and failure injection checks**
- Restore from corrupted primary and lost object copy; replay overlapping queue delivery idempotently
- Prove cutover restore with no missing acknowledged event IDs and no duplicate effects
- Verify retention dry-run does not delete unresolved or held records

**Completion gate:** A timed, witnessed restore drill passes measured owner-approved RPO/RTO, integrity and privacy checks

### H05 — Deterministic load, concurrency and fault harness

**Owner:** WG-H · **Wave:** 1 · **Priority:** BLOCKING · **Depends:** H01, H03

**Evidence**
- scripts/studio_scale_probe.py — in-memory synthetic projection only, not transport or GitHub benchmark
- tests/test_studio_v1_end_to_end.py — 100 synthetic workers does not prove live shift concurrency

**Objective:** Create a reproducible closed-loop simulator and fault injection harness that tests communication, lease, dispatch, source publication, deadlines and rate budgets.

**Owns / expected touch points:** services/coordination/bench/**; tests/coordination/load/**

**Contracts / changed behavior**
- Seeded 100/1,000/10,000 active attempts and 100,000 dormant identities with hot-job skew
- Fault matrix: crash at transaction seams, DB outage, dropped ACK, GitHub 403/429, queue delay, stale worker, tampered artifact
- Measured report P50/P95/P99, accepted IDs, duplicate protected effects, queue age, cost and recovery

**Preserves**
- No paid model calls, no unapproved source writes; test claims label synthetic distinctly

**Out of scope**
- No inference from synthetic results that 10,000 real ChatGPT chats are available

**Compile/static, runtime and failure injection checks**
- Replay accepted IDs after every injected crash without loss or dual effects
- Provider outage/shift STOP and resume sequence conserves budget and job authority

**Completion gate:** Reproducible seed+config+exact test SHA and signed result; real pilot follows independent review

### H06 — Assignment-epoch inbox and ACK semantics

**Owner:** WG-D · **Wave:** 2 · **Priority:** BLOCKING · **Depends:** H01, H03

**Evidence**
- scripts/studio_inbox.py:103-117 — current registry routing used for ACK validation
- scripts/studio_inbox.py:34-70 — re-reads full archive for each projection

**Objective:** Make recipient delivery an immutable (event, recipient, assignment_epoch) record, with incremental replay and no stale ACK erasing new assignments.

**Owns / expected touch points:** services/coordination/projections/**; tests/coordination/projections/**

**Contracts / changed behavior**
- Unique delivery key (event_id, recipient_worker_id, target_assignment_epoch)
- ACK binds original delivery, currently authenticated recipient and original epoch, with consumer effect idempotency
- Per-worker cursor, indexed projection, bounded page reads and deterministic rebuild

**Preserves**
- ACK means consumed, never QA or owner accepted
- Reassignment never mutates historical source event

**Out of scope**
- No blanket fanout of lifecycle telemetry into inboxes

**Compile/static, runtime and failure injection checks**
- Reassign between receipt and ACK; old ACK, forged recipient and duplicated event cannot clear current pending state
- Replay from cold archive matches incremental current read model

**Completion gate:** Reassignment and 100k-event historical replay pass without unbounded O(all-events) steady-state work

### H07 — Verified project resume capsule and context reuse

**Owner:** WG-D · **Wave:** 2 · **Priority:** HIGH · **Depends:** H01, H03, H06

**Evidence**
- studio/v1/PROMPT_KERNEL_V15.md — each attempt fetches job, registry, role manual, source revision and messages
- studio/workforce/ENGINEERING_PIPELINE.md — compact context packet remains a manual reference

**Objective:** Minimize repeated cold starts with a portable, bounded project capsule built from verified source and ledger state, not from free-form worker memory.

**Owns / expected touch points:** services/coordination/projections/context/**; tests/coordination/projections/context/**

**Contracts / changed behavior**
- ResumeCapsuleV1: job/worker/attempt ancestry, approved scope, repo/base SHA, latest verified checkpoint, ABI/revision refs, TODO/blocker DAG, valid inbox cursor, QA/CI state, expiry
- Source-map/index cache keyed by repo SHA+toolchain+role, with source-delta retrieval and checksum verification
- Atomic capsule publication tied to accepted attempt receipt; stale/rescinded authority invalidates cache

**Preserves**
- Original Git SHA and ledger records remain the authority, capsule is a replaceable hint
- Worker may request missing source when cache is insufficient

**Out of scope**
- No model-generated claim that an unverified capsule is itself source truth

**Compile/static, runtime and failure injection checks**
- Corrupt/expired capsule rejected; changed source SHA and revoked scope trigger cold reconstruction
- Measured context tokens/tool calls to start a job vs baseline; no skipped safety checks

**Completion gate:** Capsule rebuild deterministically from immutable refs, and restart recovers last accepted work without context drift

### H08 — Actual shift executor adapters and admission/budget policy

**Owner:** WG-E · **Wave:** 2 · **Priority:** BLOCKING · **Depends:** H01, H03, H06

**Evidence**
- studio/architecture/hyperscale/v1/07_SHIFT_EXECUTION_CONTRACT.md — design only
- scripts/studio.py:220-265 — ten S-slot allocator reserves jobs but never launches chats

**Objective:** Define supported execution hosts and a fenced, stop-aware session/shift scheduler, separating observations from real host launch/terminal receipts.

**Owns / expected touch points:** services/coordination/scheduler/**; tests/coordination/scheduler/**

**Contracts / changed behavior**
- ExecutionHost interface reports capabilities: start/cancel/status, run_id, attested start/end, idempotency, supported credentials
- Shift controller START/PAUSE/DRAIN/STOP with shift_epoch and auth, capacity by department and role
- Admission transaction reserves estimated spend before launch, reconciles actual usage, denies overage and auto-stops on empty+reconciled jobs
- Unknown/ordinary interactive chat is NEVER claimed as automatable; external Work/API invocation only with explicit owner budget

**Preserves**
- Existing S00-S09 scheduled tasks remain separate from CW logical workers
- STOP commands are authenticated and fenced; queue idle is not clean completion if QA/retry pending

**Out of scope**
- No fake infinite chat, unauthorized Work/API/model launches, or promises of guaranteed automatic restarts

**Compile/static, runtime and failure injection checks**
- Lost host response, launch retry, delayed cancel, clock skew and STOP races do not create duplicate side effects
- Reboot or host outage produces accurate INDETERMINATE and escalation

**Completion gate:** Verified supported executor integration with approved cost ceiling and no side effects for disabled auto_ai/source_writes

### H09 — Isolated source worktrees, writer leases and safe publishing

**Owner:** WG-E · **Wave:** 2 · **Priority:** BLOCKING · **Depends:** H01, H03, H08

**Evidence**
- scripts/source_targets.py — fixed authorized repo routing and work_branch
- scripts/studio.py:74-85 — naive path overlaps and S-slot writer limit; no independent runtime GitHub lease enforcement

**Objective:** Allow independent source changes only through isolated checkouts, signed leases and compare-and-swap publication that CI can actually enforce.

**Owns / expected touch points:** services/coordination/scheduler/source/**; tests/coordination/scheduler/source/**

**Contracts / changed behavior**
- One isolated worktree per job/worker attempt with exact base SHA; owned path/resource classes with conflict detection for generated/ABI and wildcard paths
- Fenced lease token bound to GitHub App identity, target branch and commit SHA; status check verifies before accepting PR/merge
- Commit publication compares branch HEAD to observed base, retries by rebase only with revalidation; orphan worktrees cleaned after durable receipt

**Preserves**
- Source registry and original Terrain3D/gameplay architecture preserved
- QA/CI at exact final SHA; no direct writes to protected default branch by workers

**Out of scope**
- No expanding live writer slots or rewriting shared branches in this design PR

**Compile/static, runtime and failure injection checks**
- Stale lease, overlapping protected paths, branch HEAD moved and unknown GitHub actor fail closed
- Process killed after commit but before receipt still produces one reconciled source patch

**Completion gate:** Two workers cannot publish conflicting protected paths or merge using stale QA/lease evidence

### H10 — CI artifact verification and supply-chain trust

**Owner:** WG-F · **Wave:** 2 · **Priority:** BLOCKING · **Depends:** H01, H02, H03

**Evidence**
- scripts/studio_ci_evidence.py:175-185 — run metadata verified but artifacts_content_verified=False
- .github/workflows/studio-*.yml — third-party Actions predominantly referenced by floating version tags

**Objective:** Attest exact built bytes, source SHA, toolchain and QA reviewer independence before accepting implementation; constrain third-party workflow execution.

**Owns / expected touch points:** services/coordination/github_bridge/**; tests/coordination/github_bridge/**

**Contracts / changed behavior**
- Download and hash required CI artifacts; verify manifest digest, toolchain/inputs, commit SHA, run attempt, size/expiry and trust identity
- Action dependency pinning policy to reviewed immutable commit SHA; least-privilege GitHub App, per-repo/scoped token permissions
- Signed attestations and source diff/AST/SARIF compact review packet keyed by final commit

**Preserves**
- Successful workflow metadata is distinct from passing independent QA or verified artifact bytes
- Actual source repo CI remains owner of compile/test results

**Out of scope**
- No declaring binaries verified merely because an artifact index exists or signature is self-asserted

**Compile/static, runtime and failure injection checks**
- Replace artifact bytes after metadata capture; invalid digest fails despite completed CI run
- Rebuild at changed SHA invalidates preceding QA pass; compromised/revoked CI token cannot publish accepted verification

**Completion gate:** One full source->CI artifact hash->independent QA->owner/authorized head acceptance test at fixed SHA passes

### H11 — Oversight key isolation, owner approval readback and replay

**Owner:** WG-G · **Wave:** 2 · **Priority:** BLOCKING · **Depends:** H01, H03

**Evidence**
- TheNomad_Studio_Oversight/docs/ACCESS_AND_CONFIDENTIALITY.md — owner GitHub connector is not separate case confidentiality
- TheNomad_Studio_Oversight/scripts/oversight_security.py:154-165,193-231 — signed notice checks owner_approval_ref URL format, not current owner approval evidence
- TheNomad_Studio_Oversight/scripts/oversight_cases.py:68-92 — case envelope/receipt storage is local-only

**Objective:** Implement independently keyed confidential intake and deterministic signed enforcement, with actual owner authorization readback and immutable anti-replay state.

**Owns / expected touch points:** services/coordination/oversight_gateway/**; tests/coordination/oversight_gateway/**; Oversight repo changes require separate reviewed source job

**Contracts / changed behavior**
- Independent encryption/decryption key custody with negative read-access test against production credentials
- Verified notice ID, issuer key, approval object digest, owner approval epoch, target assignment and lease generation, expiry, replay table
- External immutable encrypted case archive with restore/retention and minimal operational notice only

**Preserves**
- Production never receives plaintext report contents and allegations never self-trigger punishment
- Lead Director controls invasive enforcement and key rotation

**Out of scope**
- No cloning private Oversight keys into Studio or shared worker prompts

**Compile/static, runtime and failure injection checks**
- Forge plausible owner GitHub URL, replay valid notice, revoke signer, send mismatched job epoch; all denied
- Unauthorized Studio credential cannot decrypt or list case material, including from logs and backups

**Completion gate:** Independent security review confirms approval readback and case isolation before any real confidential intake

### H12 — Independent monitor, incident triggers and evidence-based productivity

**Owner:** WG-J · **Wave:** 2 · **Priority:** HIGH · **Depends:** H03, H06, H08

**Evidence**
- scripts/studio_worker_health.py:41-140 — reads observed GitHub lifecycle, not independently verified process liveness
- .github/workflows/studio-director-pulse.yml — hourly GitHub Action shares provider failure domain
- studio/architecture/hyperscale/v1/08_HOURLY_REPORTING_METRICS.md — statistics are proposed

**Objective:** Detect silent auditor failure and stalled work without treating absent events as proof of inactivity; page owner only on actionable exception.

**Owns / expected touch points:** services/coordination/observability/**; tests/coordination/observability/**

**Contracts / changed behavior**
- External heartbeat/canary freshness monitor independent of main Studio GitHub Actions trigger
- Structured incident taxonomy: NO_EVENT, HOST_INDETERMINATE, CI_FAILED, AUTH_BLOCKED, QUEUE_LAG, REPORT_STALE, SOURCE_CONFLICT
- Deduplicated email/alert receipts with source SHA and last verified time; worker durations must be host-attested or explicitly claimed
- SLO dashboards and per-department throughput/cost, queue saturation, QA backlog and evidence lag

**Preserves**
- Hourly email remains alert/report; it does not itself relaunch inactive chat
- No confidential Oversight case metadata or private credentials in production alerts

**Out of scope**
- No claiming 100 real workers active merely because IDs are registered

**Compile/static, runtime and failure injection checks**
- Disable Director workflow and GitHub API to verify independent monitor detects report freshness loss
- Repeated alerts deduplicate per hour, recoveries send notice, leaked case metadata negative test

**Completion gate:** An external canary failure yields owner-visible alert without duplicating work or masking stale sources

### H13 — V1 correctness repairs and compatibility integration

**Owner:** WG-I · **Wave:** 3 · **Priority:** BLOCKING · **Depends:** H00, H01, H03, H06, H08, H09, H10, H11, H12

**Evidence**
- scripts/studio.py:285-320 — valid code submission missing config argument
- scripts/studio_inbox.py:103-117 — acknowledgement original-assignment epoch gap
- .github/workflows/studio-reconcile.yml and tests/test_studio_reconcile_bundle.py — reconcile import/API regressions

**Objective:** Use one authorized integrator to repair verified V1 paths, connect V2 adapters and prove no hidden authority or evidence regression.

**Owns / expected touch points:** scripts/studio.py; scripts/studio_comms.py; scripts/studio_inbox.py; .github/workflows/studio-*.yml; tests/** under exclusive integration review

**Contracts / changed behavior**
- Legacy V1/V2 versioned adapters and accepted control-plane authority mapping
- Exact-SHA source submission and routed ACK regression fixes with immutable attempt receipts
- Independent reviewed migration plan for current schema 1 and department ledger 4

**Preserves**
- Existing control-plane source flags, V1 worker-life events and V4 departments
- Other chat's baseline and source branches must be freshly re-read before merge

**Out of scope**
- No blind update to live runtime or dual-applying source writes

**Compile/static, runtime and failure injection checks**
- All existing 167 Studio and 23 Oversight snapshot tests plus new positive/negative integration tests pass
- V1->V2 replay produces equivalent intended inboxes under stable authority, with documented corrections

**Completion gate:** Fresh-source independent QA, no unverified runtime gate changes, full integration CI and regression suite green

### H14 — Shadow-to-live cutover, rollback and bounded real pilot

**Owner:** WG-I · **Wave:** 3 · **Priority:** BLOCKING · **Depends:** H02, H03, H04, H05, H06, H07, H08, H09, H10, H11, H12, H13

**Evidence**
- studio/architecture/hyperscale/v1/02_MIGRATION_AND_WORKGROUPS.md — cutover defined, not deployed
- studio/architecture/hyperscale/v1/04_DEPLOYMENT_AND_SECURITY.md — cloud/App creds and recovery are unactivated

**Objective:** Establish a reversible adoption gate from synthetic trace to authorized real shifts; no deployment based solely on design validation.

**Owns / expected touch points:** services/coordination/integration/**; tests/coordination/integration/**; existing V1 entrypoints only after owner review

**Contracts / changed behavior**
- One active projector/runner epoch; read-only shadow migration with source watermark and signed checkpoint
- Owner-reviewed canary stage 2 -> 25 -> 100 active eligible workers and 1k+ simulated, expanding only on measured rates
- Rollback/drain command with source-state reconciliation and consistent commit/QA/ledger receipts

**Preserves**
- Existing Studio runtime source-write and spending restrictions until separately authorized
- No auto-merges from unverified evidence or external token

**Out of scope**
- No extrapolating synthetic 10k IDs into real ChatGPT runtime entitlement

**Compile/static, runtime and failure injection checks**
- Crash and rollback during cutover, in-flight CI/QA and source commit ambiguity; no lost accepted event
- Real two-worker source->QA->handoff->email report validated with token and API budget

**Completion gate:** Independent QA signs integrated exact SHA, owner approves deployment/credentials/budget, and rollback drill passes

## What not to build yet

- No home-grown distributed consensus/Raft, blockchain-based audit chain or multi-region active-active before restore benchmarks demonstrate a real need.
- No dedicated microservice or always-running model per worker; use bounded execution attempts and horizontally pooled deterministic coordination.
- No global FIFO queue across unrelated jobs; guarantee ordering per job and fenced source resource.
- No mandatory per-message Git commit or per-response PR: only validated batch hash anchors and artifact/QA checkpoints belong in GitHub.
- No generic long-term retrieval database unless the SHA-keyed source map and capsule metrics show a need.
- No expectation that an ordinary dormant ChatGPT tab can be programmatically awakened by a GitHub Action or a scheduled email.

## Rollout decision gates

1. **Before first paid unattended shift:** H00/H01/H08 source/identity/budget tests, host capability readback, exact permission isolation, source_writes_enabled/auto_ai_enabled remain off until explicit authorized activation.
2. **Before any privileged source writer:** H03/H06/H09/H10, exact source policy + lease generation + byte hashes + QA independent acceptance, all approved by owner.
3. **Before production confidential complaints:** H11 and separately credentialed Oversight with sealed intake, approval readback and independent security test.
4. **Before 100 active workers:** H05/H12/H13 plus H14 bounded real canary: owner-visible alerts, backpressure, no lost accepted messages and no unverified completions.
5. **Before 1,000+ active workers:** H04 measured restore and retention, actual queue/API-cost long soak, multi-partition skew and reviewer capacity/cost benchmarks; document remaining single points of failure.

**No absolute reliability promise:** safe degradation, reversible state transitions, visible errors and recoverability are verifiable engineering objectives; outages of external model services or GitHub cannot be eliminated by the Studio design.

## Persistent database, Drive, GitHub settings and unique execution identity

Source-reviewed extension: [10_DATABASE_DRIVE_AND_AUTHORITY.md](10_DATABASE_DRIVE_AND_AUTHORITY.md), [11_REPOSITORY_SETTINGS_AND_OVERSIGHT.md](11_REPOSITORY_SETTINGS_AND_OVERSIGHT.md), [12_EXECUTION_ATTEMPT_IDENTITY.md](12_EXECUTION_ATTEMPT_IDENTITY.md).

These are **binding proposed requirements added to H01/H03/H04/H07/H08/H10/H11/H12/H13/H14** in hardening_patches.v1.json, without adding competing workgroups or bypassing protected ownership. No production settings, Google Docs, Drive folder membership, secrets, live queues or source-write permissions were changed.

PostgreSQL owns post-cutover operational state and transactional event/outbox/leases, while GitHub retains work-order/approval/source evidence and Drive retains authoring and accepted canon. Google BL-00 and BL-08 presently identify active Drive queue/role documents; do not run a SQL mutable queue alongside them until an explicit owner-approved BASELINE_CHANGE_ID, shadow comparison and signed cutover chooses exactly one writer.

Existing Drive THE_NOMAD_STUDIO already has 00_BASELINES, 03_WORKING, 04_SUBMISSIONS, 05_QA and 06_ACCEPTED. Use native Docs for narrative/lore/quests/technical specifications, with per-document revision ID, requiredRevisionId conflict protection, content SHA256 and owner canon acceptance before release into NomadMain executable content. The existing root is individually owned, not verified as a Google Shared Drive. Moving content or changing sharing requires independent approval and readback.

Both private Studio and Oversight repositories report protected:false on listed branches. The rulesets API returned plan-restriction 403 and branch-protection API is inaccessible to this connector. The user/admin must choose an eligible GitHub plan and configure branch/ruleset protections, read-only Actions token defaults, independent publisher credentials, and negative tests; never make repositories public or claim unverified rules are enforced.

Each bounded chat response gets a unique execution_attempt_id under stable worker_id, job_id, assignment_epoch and studio_shift_id. The ID is **not** authentication; privileged source and QA claims require a separately attested executor/identity and current lease generation.
