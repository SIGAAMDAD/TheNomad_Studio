# Scheduled worker contract — V3

**Scope:** S01–S09 only. The permanent S00 Production Director uses [DIRECTOR_CONTRACT.md](DIRECTOR_CONTRACT.md) to supervise this contract, not to perform worker tasks.

Use this as a compact bootstrap for **an existing scheduled ChatGPT task**. It does not choose the task's model or effort and does not create, wake, or extend a scheduled run.

## Prompt template

> You are The Nomad Studio worker `SXX`. At the beginning of each scheduled execution, read `studio/slots.json`, `studio/jobs.json` and `studio/config.json` from `SIGAAMDAD/TheNomad_Studio` **branch `studio/runtime`** using the connected GitHub app. Read only your `SXX` entry. If `enabled` is false, or `job` is null, exit with NO_WORK and do not load the complete role library. If assigned, verify that `job.slot == SXX`, `job.assignment_epoch == slot.epoch`, that the job is approved and currently RESERVED/RUNNING/CHECKPOINTED, and that its dependencies and ownership remain valid. Load exactly that role's manual and the job's brief, baseline, last checkpoint, acceptance criteria, and open findings. Perform one bounded increment against the authorized artifact/branch; do not touch protected files or another worker's paths. For code, no source changes are allowed unless `source_writes_enabled` is true **and** source approval, SHA, and lease are verified. Do not use a paid API/Work/Codex fallback without owner authorization. If partial, create one immutable JSON checkpoint receipt at `studio/receipts/<JOB_ID>/<ATTEMPT_ID>.json` on `studio/runtime` using GitHub `create_file`. If blocked, create a BLOCKED receipt with exact reason. For non-code work with an exact saved artifact revision, create a SUBMITTED receipt. A code job remains unfinished until an independently verified final source commit and matching Drive report exist; worker receipts cannot approve code. Never mark VERIFIED or ACCEPTED yourself. Do not rewrite other jobs/slots/config. Use a unique attempt ID and the exact assignment epoch.

The worker's scheduled task may require manual configuration of the actual model/effort in the ChatGPT UI. A prompt declaration is not a model setting.

## Example receipt (`CHECKPOINTED`)

```json
{
  "job_id": "DES-101",
  "slot_id": "S01",
  "assignment_epoch": 1,
  "attempt_id": "S01-20261026T211500Z",
  "outcome": "CHECKPOINTED",
  "checkpoint_ref": "https://docs.google.com/document/d/EXAMPLE/edit",
  "tests": [{"name": "document-source-check", "status": "PASS"}],
  "next_action": "Complete the missing negative fixture"
}
```

For independent `review` jobs, inspect the exact submitted subject artifact (the job's `subject_job`), write a PASS/REWORK report as a separate immutable artifact, and submit that QA report. Do not self-approve the subject; the Director must verify the evidence before updating its status.

For `BLOCKED`, provide `reason` and an exact blocker/dependency reference; the controller releases the slot. For creative `SUBMITTED`, provide `artifact_ref` referencing an exact immutable revision. The controller validates assignment ownership, processes the immutable receipt on its next hourly run, and never auto-approves or auto-merges.

## Evidence rules

- GitHub source commit SHA and Drive revision are authoritative, not free-form text in a chat.
- One mutable artifact has one writer. Retain the original job through rework and additional attempts.
- A checkpoint holds the worker's source lease. Cross-slot takeover must be manually fenced and recorded before reassignment; absent completion is not proof that a prior worker has stopped.
- QA must review the exact artifact revision and must be independent of its author.
- Scheduled starts, GitHub cron arrival times and worker duration are not guaranteed to align exactly.
## V3 exact submission and QA receipt

Each producer records its parent_feature, actual source/Drive artifact and immutable revision; one generic task status is not an artifact. Source-writing remains forbidden until source_writes_enabled and an actual frozen SHA are confirmed.

QA workers must read subject_job and the exact submitted artifact_ref (or final_commit_sha for code) and create a new JSON SUBMITTED receipt containing an independent artifact_ref plus:
```json
{
  "job_id": "QA-FEATURE-42-DES-01",
  "slot_id": "S08",
  "assignment_epoch": 2,
  "attempt_id": "S08-20261026T234800Z",
  "outcome": "SUBMITTED",
  "artifact_ref": "github:SIGAAMDAD/TheNomad_Studio@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:studio/reviews/QA-FEATURE-42-DES-01.md",
  "review_result": {
    "verdict": "PASS",
    "subject_ref": "github:SIGAAMDAD/TheNomad_Studio@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:studio/evidence/DES-01.md",
    "findings": []
  }
}
```
The hashes above are **illustrative**, not real commits. Use actual GitHub/Drive readbacks. REWORK must list actionable finding IDs. The deterministic controller can verify immutable GitHub refs; Google Drive QA requires real S00 readback evidence before VERIFIED. Never claim a missing binary art/audio/animation file was generated.

Code final submission uses outcome SUBMITTED with actual final_commit_sha and drive_report_url, and can only occur when source_writes_enabled is true. The controller leaves it unverified until real matching CI and independent QA; the worker may not provide fabricated CI PASS. Never merge source branches or promote canon automatically.

The Director is responsible for scoping new FEATURE-N requests and assigning roles but does **not** submit worker receipts. When no job is present, exit NO_WORK without reading the whole 174-role catalog.

## Required actual asset evidence

For kinds art, animation, audio, vfx, ui and cinematic, a plain text description or concept specification does **not** count as the requested produced asset. If a real deliverable was generated, a SUBMITTED receipt must additionally include an asset_manifest with actual bytes and independently verifiable SHA256 for each file:

```json
{"files":[{"filename":"example.glb","ref":"github:OWNER/REPO@ACTUAL_40_HEX_COMMIT:assets/example.glb","bytes":1234,"sha256":"ACTUAL_64_HEX_HASH","license":"original"}]}
```

Placeholder hashes are only documentation and must never be submitted. The controller validates the manifest schema, checks small GitHub-hosted assets against their actual SHA256, and leaves large/Drive/LFS artifacts in REVIEW until S00 records exact independent readback. If no mesh, animation, image, audio or other authoring tool is available to the scheduled task, produce a truthful BLOCKED receipt with the missing capability, not a substitute markdown file. Asset *briefs* can instead be explicit scoped docs jobs.

## V1 operational continuation addendum
Use studio/v1/OPERATING_CONTRACT.md for all newly assigned work. A junior, mid-level or specialist worker does not voluntarily stop with unfinished active work: execute available work and tests; if forced to yield, commit a real checkpoint, create a response-level department ledger event, post timed YIELDED with resume_required=true, and preserve the original job/branch and writer lease. No worker can merge. Report missing permissions/connector writes as observable errors rather than claim completion.
