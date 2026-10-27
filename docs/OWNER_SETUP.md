# Owner setup — Studio V3

This guide separates deployed code from actual external permissions and execution proof.

## Step 1 — Review and merge the V3 pull request

Check Python CI for the controller + board + QA integration tests. Review and merge `studio/v3-full-production-chain` into `main`. This does **not** merge game/engine source, change canon or authorize paid inference.

## Step 2 — GitHub permissions and labels

In `TheNomad_Studio → Settings → Actions → General` enable GitHub Actions, and permit reviewed workflows enough repository write permission for `studio/runtime`. The board-scanner workflow declares `contents: write` and `issues: read`, and its jobs use the repository's standard `GITHUB_TOKEN`.

From `Actions` manually run `Studio bootstrap approval labels (manual)` once. It creates (if missing) `studio:approved`, `studio:accepted`, `studio:finalized` and `studio:cancel`. **Do not apply these labels with a bot:** the scanner requires an actual trusted owner label-change actor, initially `SIGAAMDAD`.

## Step 3 — Runtime configuration

On `studio/runtime` (not `main`), verify:
- `studio/config.json` has `auto_dispatch_enabled: true` but `auto_ai_enabled: false` and `source_writes_enabled: false`.
- `studio/slots.json` contains the exact ten existing scheduled task IDs and only permitted enabled worker slots.
- `studio/board.json`, `studio/features.json` and `studio/specs/` exist. Existing `studio/jobs.json`, receipts and checkpoints are never replaced.

The board and feature-state files will be created in runtime during initial deployment. GitHub workflows always read *current* runtime state, not the sample content on `main`.

## Step 4 — Connect the attached GitHub Project (optional but needed for board fields)

Open the actual **GitHub Projects V2** board. Copy its exact URL, for example `https://github.com/users/OWNER/projects/7` or `https://github.com/orgs/ORG/projects/7`. Put it in `studio/runtime/studio/board.json.project_url`. The connector cannot infer the ID of a private project from Issue searches.

Provide a **GitHub Projects-authorized token** through repository `Settings → Secrets and variables → Actions → New repository secret` named `STUDIO_PROJECTS_TOKEN`. It must have access to this particular board and private repository issues. For a user-owned classic PAT this generally requires `project` and `repo` scopes. Prefer a properly scoped GitHub App/token where your account permissions support it; never put the credential in JSON, chat, git commits or outputs.

Set `project_writeback: false` for the first read-only test. Inspect the Actions log for `project_items_read` greater than zero and `project_error: null`. Only then set `project_writeback: true` if you want automated statuses. The Project needs a single-select field named `Status` with options matching the statuses configured in `scripts/board_sync.py`; missing options are skipped, never invented by the workflow.

**Without Projects credentials, repository Issues are fully scanned and admitted.** A GitHub Project Issue card works via the linked issue, but custom Project fields and draft-only cards will not be synchronized. Draft cards are recorded as INBOX if Projects reading is configured; convert a draft to an Issue before approved execution.

## Step 5 — Run the board integration

Open `Actions → Studio board intake (Issues + optional Projects V2) → Run workflow` on `main`. Confirm it:
1. Reads Issues, including existing `#3`, and adds a stable `FEATURE-3` to `studio/runtime/studio/features.json`.
2. Leaves its state INBOX unless you personally add `studio:approved`.
3. Preserves the existing OPS-BOOT-001 and OPS-QA-001 pilot records.
4. Reports no unexpected status, permission or validation errors.

## Step 6 — Install the web intake Skill and keep the Director connected

Install the supplied `nomad-studio-intake/skill.zip` in the ChatGPT Skills UI. It can create a parent Issue/feature spec through the connected GitHub app from requests such as "Studio: build a Sortior Blader enemy across design, GOAP, writing, art, animation, sound and code." It does **not** launch ChatGPT tasks or change model settings by writing a prompt.

S00 Director should follow `studio/DIRECTOR_CONTRACT.md`, check `studio/features.json` at every hourly run, and use the existing role catalog only for actual approved jobs. S01–S09 follow `studio/WORKER_CONTRACT.md`. Keep the existing scheduled identities; change their model/effort only in the ChatGPT interface if needed.

## Step 7 — Full non-code handshake

Create an approved, small real design/writing Issue; then require:
- owner-approved Issue label event, one feature record, and one source-grounded `studio/specs/FEATURE-N.json`;
- deterministic child jobs with distinct owned paths and explicit QA;
- actual Sxx artifact saved, immutable SUBMITTED receipt, successful reconciliation;
- independent QA report with `review_result.verdict` and matching `subject_ref`;
- PASS readback or REWORK + revision loop, followed by an OWNER_REVIEW state.

Do not infer that everything ran simply because a scheduled task is enabled.

## Step 8 — Engineering source freeze and real asset tools

Before enabling `source_writes_enabled`, pin and approve the exact October full-source state of the game, framework, Terrain3D and relevant Godot/SDK versions. Verify the actual builds, Godot editor parity, Clang/C# compilation and integration tests on suitable runners. GPU tests need real compatible GPU hardware. The Studio control-plane CI does not establish these capabilities.

Similarly, verify available image, mesh, rig, animation, sound/voice, FMOD and asset authoring tools in the scheduled environment. A missing tool is a capability BLOCKED state, not a successful deliverable.

## Step 9 — Owner acceptance and finalization

After all child jobs pass QA and required CI, the feature becomes READY_FOR_OWNER. You apply `studio:accepted` to the parent Issue. That permits the machine to record OWNER_ACCEPTED, but does not auto-merge code or promote canon. After authorized final publication and exact readback, record `finalization_evidence` and apply `studio:finalized`. Only then is the parent FINALIZED.

## Operating costs and limitations

- The 15-minute GitHub Action scans are deterministic, but they consume applicable GitHub Actions minutes on a private repository.
- Ordinary scheduled ChatGPT tasks still run on their own schedule. A GitHub event does not immediately spawn an ordinary ChatGPT chat.
- No paid model API, Codex or Work fallback is configured. Stronger review models must be selected through supported account/task controls and may consume a separate allowance.
- Existing Google Drive legacy tickets require careful ID/revision migration. This deployment intentionally avoids overwriting their original documents.

## Optional Google Drive request intake

To also initiate feature requests by placing documents in Drive:

1. Create an owner-controlled Google Drive folder specifically for Studio requests.
2. Open Google Apps Script, copy integrations/apps-script/DriveIntake.gs into the script, and set Script Properties STUDIO_DRIVE_INTAKE_FOLDER_ID (exact folder ID), STUDIO_GITHUB_ISSUES_TOKEN (a fine-grained token with Issues write on TheNomad_Studio), and STUDIO_GITHUB_REPO.
3. Run studioInstallDriveIntakeTrigger once and authorize the script. The script will scan every 15 minutes.
4. Place a Google Doc with a name beginning [STUDIO] inside that folder. The script creates one deduplicated Studio GitHub Issue linking the original document.
5. Approve its scope in GitHub using the trusted owner label; Drive file creation alone never authorizes production.

This integration is shipped as Apps Script source, not installed into your Google account. It requires your script/token authorization. It never edits the source document or bypasses independent QA.
