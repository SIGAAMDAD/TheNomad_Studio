# Proposed V1 deployment and acceptance

NO WORKFLOW ACTIVATION IN THIS CHANGE. Do not change main controller, studio/runtime config, source_writes_enabled, auto_ai_enabled, budgets or permissions by merging documentation alone.

Phase 0: Owner reviews this design and separate Oversight protocol.
Phase 1: Seed minimal studio/comms data branch, no workflow files in its HEAD tree. Approve JSON schemas and synthetic fixtures. Configure force-push/deletion protections, verify actual ruleset and backup policy.
Phase 2: Add deterministic non-spending controller in a separate reviewed PR. Test issue-comment ingest, periodic scan, type rejection, fast-forward retry, dedupe, missed event recovery and daily digest generation.
Phase 3: Introduce chat-worker registry separately from S00–S09 scheduled slots; map existing approved jobs without replacing jobs.json. Pilot two independent workers and one independent QA reviewer.
Phase 4: Attach read-only Director audit summaries. Add scoped, confidential oversight intake only after access/key separation is verified.

Required gates: concurrent inputs preserved; duplicate event idempotent; missing comment recovered; forged owner instruction ignored; wrong lease blocked; source SHA checked; no plaintext complaints archived; no code/source write invoked; digest retains raw references; original messages stay available. Mark every unexecuted test NOT_RUN.

The owner must separately approve changes to workflows, bot credentials, source-write gates, worker activation, oversight key custody and enforcement authority.