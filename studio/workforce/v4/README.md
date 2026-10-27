# Studio V4 — Department-led production skill source

V4 adapts the preserved 174-role source roster without changing the existing V3 GitHub controller.

The human Lead Director initiates interactive requests or approved GitHub Issues. Department heads chain the relevant specialist playbooks, create approved child issues, preserve one author per artifact, independent QA and exact-source evidence. Every substantive response appends a department event under `studio/runtime:studio/ledgers/events/...` or reports `LEDGER_WRITE_PENDING`. GitHub Issues/Projects remain task authority; source repositories/Drive revisions remain deliverable authority. A 23:00 America/Chicago scheduled audit is specified but not activated by this document.

See [department registry](departments.v4.json), [reasoning chain](CHAIN_OF_COMMAND.md), [ledger protocol](LEDGER_EVENTS.md), [review gates](REVIEW_GATES.md), and [daily audit](DAILY_AUDIT.md).

Skill sources are under `skills/rpg-studio-all-roles` and `skills/studio-director`; standalone skill packages are distributed separately.

## Activation

- Review this PR and install V4 master and director skills.
- Amend actual Work and scheduled task instructions to load V4 protocol; simply merging docs cannot change an existing ChatGPT task prompt.
- Preserve current source/AI write gates, worker leases and owner approvals.
- Configure/verify the 23:00 Central scheduled audit and Gmail recipient with authorized task and Gmail tools.
- Extend the deterministic controller to reconcile/issue-register ledger events before treating the nightly report as fully operational.
- Run the included suite; none of these source files is evidence that a real nightly email or worker execution happened.
