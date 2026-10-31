# Owner deployment and launch acceptance - R5R

## Authority

The human owner authorizes project scope, Canon, architecture, source integrations, protected merges, releases and spending. Reviewed Studio source belongs on `main`; `studio/runtime` and `studio/comms` are separate operational GitHub branches. The authenticated APSW coordinator owns project-scoped machine state and command/lease transitions.

The source cleanup was merged through [PR #36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36) and [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37). The frozen operator API has 72 operations; the catalog has 174 roles across 12 departments. Existing source review checks do not certify a live deployment.

## Deployment acceptance sequence

1. Record exact reviewed `main` SHA. Verify full Python/APSW, TypeScript, .NET ToolHost, skill and source-provenance CI for the actual deployment revision, including the separate >=90% production coverage checks.
2. Provision persistent APSW storage and verified blob/source roots. Exercise backup, restore and restart checkpoint reconciliation.
3. Install a real `STUDIO_TRUSTED_PORTS_FACTORY` with owner-proof, project bindings, GitHub relay/source/CI readback, scope authorization, independent QA, command outbox publisher and release/effect adapters. Configure `STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment`.
4. Install `CONTRACTS/` and `schema/` with the running Python service. The current Dockerfile needs correction to package both; the supplied Compose file alone is not a complete production deployment.
5. Bind operator HTTP to loopback behind TLS. Configure the React cockpit separately. Deploy ToolHost separately on a platform with verified sandbox support (current implemented backend: Linux bubblewrap/userns/prlimit; Windows execution fails closed).
6. Confirm the five owner-approved repository/branch routes in `src/studio/app/source_policy.py` and reject unauthorized source targets and stale source revisions.
7. Manually initialize browser worker conversations, register them in the correct project, and verify actual shift/relay/receipt behavior.

## Pre-pilot and release gates

Exercise project isolation, wrong principal/recipient/lease, duplicate commands, stale revisions, partial writes, lost acknowledgments, rate limits, remote readback, recovery after restart, exact-revision QA independence and owner-gated publication. Persist failing checks as FAIL or NOT_RUN; do not convert a service setup placeholder into production evidence.

**F-20 live browser gate:** An existing ordinary browser conversation must process two successive addressed GitHub commands in one continuing response and publish two authentic, remotely read-back receipts. Until this occurs, keep browser acceptance `NOT_RUN` or `BLOCKED` with its observed evidence.

**External legacy decommission:** Deleting source does not revoke external Apps Script triggers or installed skills/tasks. Check those installations separately; see [Decommission status](LEGACY_DECOMMISSION_STATUS.md).

See [Service setup](SETUP.md), [Browser setup](CHATGPT_SETUP.md), [Production pipeline](PRODUCTION_PIPELINE.md), and [Repository maintenance](REPO_MAINTENANCE.md).
