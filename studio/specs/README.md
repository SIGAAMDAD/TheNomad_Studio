# Feature decomposition contracts

A producer/Director creates `FEATURE-N.json` only after reading the owner's issue and actual relevant technical/creative baselines.

- Every spec is linked to its existing parent GitHub Issue.
- The owner grants `studio:approved` on the issue (actor verified through issue events).
- Job IDs are stable, role/manual explicit, source baseline and owned paths exact. The dispatcher never invents roles, source commits, canon or deliverable revisions.
- The deterministic admission tool will create one QA job per productive job, and convert intra-feature dependencies to verified producer QA dependencies.
- An unapproved or malformed spec is not admitted; code work remains BLOCKED until the separate source gate is authorized.
- Never overwrite an active job or change an existing approved scope by resubmitting a spec. Write a reviewed amendment instead.

See `docs/PRODUCTION_PIPELINE.md` for complete flow.
