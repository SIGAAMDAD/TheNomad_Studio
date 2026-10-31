# Studio V4 — Department-led production skill source

The 174-role roster and 12-department structure operate under the R5R authenticated APSW coordinator.

The human Lead Director initiates interactive requests or approved GitHub Issues. Department heads chain the relevant specialist playbooks, create approved child issues, preserve one author per artifact, independent QA and exact-source evidence. Every substantive response appends a department event under `studio/runtime:studio/ledgers/events/...` or reports `LEDGER_WRITE_PENDING`. GitHub Issues/Projects remain task authority; source repositories/Drive revisions remain deliverable authority. The 23:00 America/Chicago reporting requirement remains a separate configured service obligation.

See [department registry](departments.v4.json), [reasoning chain](CHAIN_OF_COMMAND.md), [ledger protocol](LEDGER_EVENTS.md), [review gates](REVIEW_GATES.md), and [daily audit](DAILY_AUDIT.md).

Skill sources are under `skills/rpg-studio-all-roles` and `skills/studio-director`; standalone skill packages are distributed separately.

## Activation

- Verify department and role manifests against `src/studio/organization/catalog`.
- Use the R5R project, worker, lease and receipt APIs for authoritative state; do not use JSON slot schedulers.
- Verify real GitHub relay receipts, source QA, and owner approvals independently.
- Run the V4 skill contract suite and project-level R5R acceptance gates.
