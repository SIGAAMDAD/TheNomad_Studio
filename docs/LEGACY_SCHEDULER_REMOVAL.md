# Legacy ten-slot scheduler removal

The old S00-S09 scheduler, V1 JSON job/slot/feature registries, Work-task-triggered prompts, cron-based dispatch and Issue-comment broker have been removed from the PR #36 implementation tree (not yet merged to `main`).

## Current operational authority

- R5R `src/studio/` owns project-scoped worker identities, shifts, leases, command outbox, receipt reconciliation and APSW state.
- `CONTRACTS/` and `schema/` define the active production interface and initial durable state.
- GitHub source/communications refs and department evidence remain independently verifiable and are not automatically rewritten or deleted by this source cleanup.
- The coordinator publishes commands to registered conversations, but browser work occurs only while an actual response is active.

## Deployment and verification

- Build and test with `.github/workflows/studio-validation.yml` and `tests/acceptance/test_no_legacy_scheduler.py`.
- No old GitHub Actions schedule or ten-slot controller is installed from `main`.
- If independent external scheduled tasks were configured in ChatGPT, their deactivation must be verified in that service separately; removing repository code cannot change those external tasks.
- Historical commits and the operational `studio/runtime` and `studio/comms` branches are preserved as evidence; they are not a live slot scheduler.
