# Owner deployment and launch acceptance - R5R

## Authority

The human owner authorizes project scope, Canon, architecture, source integrations, protected merges, releases and spending. Reviewed Studio source belongs on `main`; `studio/runtime` and `studio/comms` are separate operational GitHub branches. The authenticated APSW coordinator owns project-scoped machine state and command/lease transitions.

The source cleanup was merged through [PR #36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36) and [PR #37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37). The frozen operator API has 72 operations; the catalog has 174 roles across 12 departments. Existing source review checks do not certify a live deployment.

## Deployment acceptance sequence

1. Record exact reviewed `main` SHA. Verify full Python/APSW, TypeScript, .NET ToolHost, skill and source-provenance CI for the actual deployment revision, including the separate >=90% production coverage checks.
2. Provision persistent APSW storage and verified blob/source roots. Exercise backup, restore and restart checkpoint reconciliation.
3. Install a real `STUDIO_TRUSTED_PORTS_FACTORY` with owner-proof, project bindings, GitHub relay/source/CI readback, scope authorization, independent QA, command outbox publisher and release/effect adapters. Configure `STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment`.
4. Install `CONTRACTS/` and `schema/` with the running Python service. The Docker image includes verified resources; initialize the empty APSW database explicitly using the bootstrap command in [Deployment](../deploy/README.md). Compose includes the React cockpit behind a loopback-published Caddy TLS gateway; authenticated trusted provider and ToolHost broker setup remain deployment-specific.
5. Keep operator HTTP on loopback behind the Compose Caddy TLS gateway; build the React cockpit in the frontend image. Run `python scripts/verify_live_operator.py --url https://localhost:8443` with an owner-provisioned `STUDIO_OPERATOR_TOKEN` and trusted CA. Deploy ToolHost separately on a platform with verified sandbox support (current implemented backend: Linux bubblewrap/userns/prlimit; Windows execution fails closed).
6. Confirm the five owner-approved repository/branch routes in `src/studio/app/source_policy.py` and reject unauthorized source targets and stale source revisions.
7. Manually initialize browser worker conversations, register them in the correct project, and verify actual shift/relay/receipt behavior.

## Pre-pilot and release gates

Exercise project isolation, wrong principal/recipient/lease, duplicate commands, stale revisions, partial writes, lost acknowledgments, rate limits, remote readback, recovery after restart, exact-revision QA independence and owner-gated publication. Persist failing checks as FAIL or NOT_RUN; do not convert a service setup placeholder into production evidence.

**F-20 live browser gate:** An existing ordinary browser conversation must process two successive addressed GitHub commands in one continuing response and publish two authentic, remotely read-back receipts. Until this occurs, keep browser acceptance `NOT_RUN` or `BLOCKED` with its observed evidence.

**External legacy decommission:** Deleting source does not revoke external Apps Script triggers or installed skills/tasks. Check those installations separately; see [Decommission status](LEGACY_DECOMMISSION_STATUS.md).

See [Service setup](SETUP.md), [Browser setup](CHATGPT_SETUP.md), [Production pipeline](PRODUCTION_PIPELINE.md), and [Repository maintenance](REPO_MAINTENANCE.md).

## Verifiable remote F-20 receipt evidence

For a real browser shift, save a private JSON record with `project_id`, `channel_id`, `shift_id`, `contract_digest`, `repository` (`owner/repo`) and exactly two `pairs`. Each pair provides `command_path`, `command_commit_sha`, `receipt_path` and `receipt_commit_sha`. All paths must refer to actual GitHub files at pinned commits, not mutable branch names. Then run:

```sh
python -m qa.f20_remote --evidence /secure/f20-observed.json
```

The command reads `STUDIO_GITHUB_TOKEN` from the private environment, verifies both sets of remote bytes, matching shift/contract/project identity, distinct commands, distinct receipts, SHA256 digests and ordering. It reports `TWO_REMOTE_RECEIPTS_VERIFIED` and leaves browser continuity for independent owner observation. Record that observation and the authenticated worker binding separately before calling F-20 accepted.

Before the remote verifier runs, load the independent operator-approved scope
into private environment variables `STUDIO_F20_REPOSITORY`,
`STUDIO_F20_PROJECT_ID`, `STUDIO_F20_CHANNEL_ID`,
`STUDIO_F20_SHIFT_ID`, and `STUDIO_F20_CONTRACT_DIGEST`.
The script compares those values against the evidence and refuses a mismatched
repo/project/shift even when both GitHub files are authentic.
