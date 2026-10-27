# The Nomad Studio V2 — deployment and operations

This repository is the **control plane**, not the Godot codebase or the long-form creative archive. Production tasks live as versioned JSON on `studio/runtime`. Source code lives in the three approved repositories; narrative/design documents remain in Google Drive. GitHub scheduled Actions perform deterministic reconciliation, not AI inference.

## Owner deployment steps (in order)

1. **Review and merge the automation PR into `main`.** Do not merge any game/engine/Terrain3D source branches as part of this step. Confirm Actions are enabled under `TheNomad_Studio → Settings → Actions → General`. Allow the repository's `GITHUB_TOKEN` the permissions required by the reviewed `contents: write` reconciliation workflow (org policy may override this).
2. **Validate the controller:** `Actions → Studio control validation → Run workflow`. It runs the Python unit tests, schema/ownership checks, status and a dispatch preview. This tests the controller, not the Godot project.
3. **Initialize `studio/runtime`:** `Actions → Studio hourly reconciliation → Run workflow`. The workflow creates the `studio/runtime` branch when absent, or works only on the existing runtime branch. It processes immutable receipts, plans eligible reservations and commits only `studio/jobs.json` and `studio/slots.json` to `studio/runtime`. The default branch is never changed by the dispatcher.
4. **Edit state on `studio/runtime` with reviewed commits:** Set `auto_dispatch_enabled: true` in `studio/config.json` when ready. Keep `auto_ai_enabled: false` and `source_writes_enabled: false` until separate owner decisions. Enable only the worker entries that have real matching ChatGPT scheduled tasks and approved READY jobs. Never edit `studio/jobs.json` on `main` expecting the runtime controller to import it automatically.
5. **Register one small real design/writing job:** Use `examples/job.example.json` as a field template, add it to the `jobs` array on `studio/runtime`, and set approved READY only when the brief, source references, acceptance, unique writable paths and independent reviewer are real. Do not create a production ticket merely to keep a worker occupied.
6. **Bind scheduled ChatGPT tasks:** Edit the existing tasks' prompts to use the `studio/WORKER_CONTRACT.md` template with an exact slot ID. Select the actual model/effort manually if the UI supports it; the automation interface cannot set reasoning effort. The Production Director is S00; assign distinct specialists to S01..S09 as available. Stagger them at minute :06/:12/:18/:24/:30/:36/:42/:48/:54, but verify actual schedules in the task dashboard. Only the desired active subset should be enabled.
7. **Verify one full cycle:** GitHub Action reserves job → assigned worker reads `studio/runtime` → worker writes actual artifact to its proper system → worker creates immutable receipt in `studio/runtime/studio/receipts/JOB/ATTEMPT.json` → next hourly Action consumes it → independent QA receives the exact revision. Do not consider the studio operational until this succeeds with readbacks.
8. **Freeze engineering sources separately:** Confirm current SHA/build/ABI parity for `SIGAAMDAD/The_Nomad:Nomad3D`, `SIGAAMDAD/NomadFramework:develop` and `SIGAAMDAD/Terrain3D:Terrain3D_NomadRenderer`. Install/review actual build, Clang AST/Roslyn and Godot regression workflows in those source repositories. Only after acceptance should the reviewed source-write gate be changed. The controller's green tests never imply a successful Godot build.
9. **Enable recurring operation:** Scheduled reconciliation runs hourly from the default branch, subject to GitHub schedule delays. Workers continue at their individual hourly schedules. At the end of each day, the Director aggregates verified changes and blockers from the runtime branch and Drive; configure any outbound email notification separately.

## Local commands (from repository root)

```bash
python -m unittest discover -s tests -v
python scripts/studio.py validate
python scripts/studio.py status
python scripts/studio.py dispatch
python scripts/studio.py context --slot S01
python scripts/studio.py process --write       # processes immutable receipts in this checkout
python scripts/studio.py dispatch --write      # persists reservations in this checkout
```

Use `--root /path/to/runtime-checkout` when running a controller copied from the reviewed default branch against `studio/runtime` state. Do not run `--write` in multiple unsynchronized checkouts. GitHub Actions `concurrency` serializes its own runs; a concurrent human push can still produce a non-fast-forward rejection, in which case the next run must reread the actual branch before applying state.

## Security and spending

- Deterministic workflows need no `OPENAI_API_KEY` and spend no OpenAI API tokens. The `ai-manual.yml` bootstrap is an eligibility gate, **not an inference executor**.
- No automatic ChatGPT scheduled-task wake-up is implemented. Ordinary scheduled chats only run at their schedules. GitHub dispatch writes eligibility state for them to read.
- Do not enable unbounded external model API calls. An optional future API worker requires separately approved spending, an environment gate with required reviewers, and dedicated credentials.
- Keep protected game source and owner canon approval under human authority. AI output is not production acceptance.

## Recovery

- If a worker produced a partial result, its `CHECKPOINTED` receipt retains the same job and slot assignment. The next run loads only the checkpoint and outstanding acceptance criteria.
- A `BLOCKED` receipt records a reason and clears the worker assignment. Reactivation requires resolving the actual dependency and changing the job back to READY in a reviewed state change.
- A stale or conflicting receipt stops reconciliation and remains visible in the runtime branch. Fix the evidence or authorization explicitly; never silently mark it successful.
- If a worker is stuck while retaining a CHECKPOINTED assignment, verify from the scheduled-task service that its prior run is finished, then explicitly fence the old claim and reassign in a reviewed runtime state change. No automatic lease stealing is implemented.
- When two repeated targeted fixes fail, escalate to an engineering or narrative lead for diagnosis before another identical retry.

## Capacity and model routing

The initial cap is two writing jobs and one independent QA review concurrently, not nine independent mutable jobs. S06/S07 may handle read-only analysis while code stays gated. Prefer Instant for compact drafting, metadata and simple checks; use Medium/High for hard architectural or code correctness jobs after the context pack has already been narrowed. Monitor successful VERIFIED increments, revision rework rate, idle run count, GitHub Actions usage and actual Work/Codex allowance before expanding.