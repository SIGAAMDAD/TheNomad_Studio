# Legacy ten-slot scheduler removal

The old S00-S09 scheduler, V1 JSON job/slot/feature registries, scheduled GitHub Actions dispatch, old Work-task worker prompts and Issue-comment broker have been removed from `main` through [PR #36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36) (merge commit `4e1d1604208f5ace27207f2ebfa1faff79a64fa6`). Follow-on [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37) removed remaining obsolete intake components and policy references.

## Current runtime

- `src/studio/` implements project-scoped worker IDs, shifts, fenced leases, APSW transactions, GitHub command outbox, receipts, reconciliations and the authenticated operator API.
- `CONTRACTS/` and `schema/` define frozen interface and persistence contracts.
- Source implementation belongs in approved game/framework repositories, never the Studio controller. `src/studio/app/source_policy.py` enforces owner-selected source routes at the trusted source-revision boundary.
- GitHub `studio/runtime` and `studio/comms` preserve distinct operational evidence. Their existing history was not deleted or rewritten.
- Browser workers execute only when their human-initialized conversation has an actually running response. The coordinator can publish commands but cannot wake a dormant ChatGPT browser conversation.

## Verification and residual external work

Source workflows are `.github/workflows/studio-validation.yml` and `.github/workflows/studio-v4-skill-check.yml`. The negative regression `tests/acceptance/test_no_legacy_scheduler.py` rejects reintroduction of the scheduler. External installations (old ChatGPT scheduled tasks or Apps Script triggers) require separate verified deactivation; see [Decommission status](LEGACY_DECOMMISSION_STATUS.md).
