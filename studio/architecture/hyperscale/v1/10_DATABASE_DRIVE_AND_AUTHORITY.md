# Persistent coordination database and Drive integration — proposed V2

Status: DESIGN ONLY. Baseline: Studio main 5b3654318aa040797f896bcc3640b05f7f6df8a0, live Studio Drive BL-00/01/05/07/08/10 on 2026-10-27. No database, Drive migration, or paid executor is activated.

## Authority and division of labor
- PostgreSQL: sole live writer for job runtime transitions, shifts, attempts, current assignments, source leases, dependency readiness, deliverable receipts, notification cursors and real cost after an approved cutover. Append-only event ledger + outbox; indexed read projections for worker inboxes and Director reports.
- GitHub: owner-approved work scope (Issues/Projects), code and compiled assets at exact commit SHA, CI and independent QA evidence, approved architecture and immutably anchored batch receipts. Do not replace code repositories with SQL or Drive.
- Google Drive: prose authoring and review for lore, narrative, dialogue, quest plans, design reports and technical specifications. DB records document IDs, revision IDs, accepted export hash, source refs, owner, job, QA state and approval, not long-form document contents.
- NomadMain: executable validated quest/dialogue/content data generated from approved source docs, with exact source SHA. A draft Doc is not executable content or canon.
- Oversight: separate encrypted case store with independent keys; its confidential content and access credentials do not belong in Production SQL or general Studio Drive.

Existing Drive Studio root: https://drive.google.com/drive/folders/1GgNw3YXgMZqDzKa6SE87blM62e_ulE3I. It is currently an individually owned folder, not a team Shared Drive; available metadata shows owner-only access. If Workspace Shared Drives are available, assess a controlled team-ownership migration for continuity, preserving every file ID, approval and existing link where possible. Do not move existing files without a verified owner decision.

## Existing Drive structure — preserve
00_BASELINES: approved policies and canon constraints.
00_CONTROL: live accountable role/contract view.
01_MANIFESTS: governance register, historic work queue and manifests.
02_INBOX: bounded work briefs.
03_WORKING: independent draft prose and plans.
04_SUBMISSIONS: submitted revisions under review.
05_QA: independent editorial/technical findings.
06_ACCEPTED: owner-approved immutable semantic revisions.
08_REPORTS: readable current production summary.
09_ASSET_LIBRARY: immutable content-addressed masters/rights.
10_BUILD_ARTIFACTS: CI logs, traces and large diagnostics.
12_AUTOMATION: schemas and controlled dispatch references.
13_RELEASES: final frozen provenance and release packages.
Propose discipline subfolders NARRATIVE, LORE, QUESTS, GAME_DESIGN, TECHNICAL_SPECS in WORKING/SUBMISSIONS/QA/ACCEPTED only after owner-approved folder mapping. There is no need for a duplicate canonical Gazetteer or one Document per tiny event.

## Critical split-brain migration gate
Current BL-00 and BL-08 still name STUDIO_PENDING_WORK_QUEUE__LIVE and STUDIO_ROLE_ASSIGNMENTS__LIVE in Drive. Existing GitHub runtime jobs also exist. A third active SQL queue would silently produce competing authorities. Before production cutover, file an owner-approved BASELINE_CHANGE_ID covering BL-00/05/07/08/10 and a frozen authority revision. Run a shadow import and compare GitHub+Drive changes. At a signed cutover watermark elect only PostgreSQL for live job state, turn Drive live queues into clearly marked READ-ONLY projections, and keep GitHub/owner approvals authoritative for scope. Do not edit the old baselines just because this design exists.

## Minimal PostgreSQL schema domains
jobs, job_dependencies, policy_revisions, worker_principals, worker_assignments, studio_shifts, execution_attempts, resource_leases, received_events, outbox, delivery_effects, projection_cursors, document_refs, document_revision_snapshots, source_evidence, qa_verdicts, usage_budgets, notification_receipts, storage_segments and audit_anchors.
- Unique event ID / transport delivery ID and per-job sequence; each accepted event and outbox intent committed in one transaction.
- ACK effect key includes event_id + recipient_worker_id + original assignment_epoch.
- External GitHub commit/Drive document edit is a saga: PREPARED -> EXTERNAL_COMMITTED -> independently read back -> RECONCILED or QUARANTINED. A process crash between source commit and ledger write must not duplicate the patch.
- Shift/attempt admission checks current signed policy, owner scope, spending budget, executor attestation, source SHA and lease generation. No ordinary chat is awakened merely because SQL holds work.
- SQL event archive is recoverable, not an infinite hot table. Cold segments and signed external hash anchors require backup, restore/retention test and immutable source refs.

## Efficient Drive synchronization
Use Drive changes.getStartPageToken and changes.list as a persistent change cursor. Optional expiring changes.watch notifications signal work but do not themselves establish revision state; reconcile missed notifications through the cursor. Cache file_id + revisionId + normalized section index + exported content SHA256 in SQL. On each worker attempt fetch only changed sections and relevant accepted source refs, not entire baselines again. Use Google Docs batchUpdate with writeControl.requiredRevisionId and a per-document writer lease; fail on concurrent revision rather than silently replacing another worker's prose.

Every new deliverable follows the already binding BL-01/BL-05 fields: stable DOCUMENT_ID, KIND, owner, source refs, parent job, status PROPOSAL/WORKING/REVIEW/VERIFIED/ACCEPTED and exact approval ref. Freeze submitted content at a specific revision and exported content hash. QA binds to that hash; a changed Doc invalidates review. Canon requires explicit owner acceptance. After acceptance publish separately versioned immutable export and, when gameplay-facing, compile schema-valid content into NomadMain.

## Operational efficiency and guardrails
Saving large drafts to Drive reduces GitHub source request traffic but does not guarantee fewer Google API calls or lower model token cost. Measure Drive calls, cache hit ratio, changed sections, cold-start tokens, document conflict rate and review latency. Never put actor passwords, OAuth tokens, KMS keys, confidential Oversight case data or full binary archives into Google Docs. General owner-connected Drive access is not independent worker identity; use a scoped gateway/credentials for privileged automation.

## Acceptance tests
- Shadow import gives equal approved job statuses without two live writers.
- Duplicate webhook and source/Drive API retry yield one valid state effect.
- Lost event/DB crash after external source commit recovers exact original SHA.
- Two concurrent Doc writers: one revision conflict, no lost content.
- Accepted canon remains frozen, independent QA and owner approval tied to exact accepted hash.
- Invalid/revoked scope or missing approval blocks SQL/Drive/GitHub mutation.
- Restore a snapshot, cursors, archive segments and notification dedup state; no lost acknowledged event.

References: https://developers.google.com/workspace/drive/api/guides/manage-changes ; https://developers.google.com/workspace/docs/api/how-tos/best-practices ; https://support.google.com/a/users/answer/7212025
