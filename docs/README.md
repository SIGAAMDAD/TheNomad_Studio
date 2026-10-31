# The Nomad Studio documentation

This is the navigation index for the R5R source tree after the two merged cleanup PRs: [#36](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/36) and [#37](https://github.com/SIGAAMDAD/TheNomad_Studio/pull/37).

## Start here

- [System overview](../README.md): component ownership, code layout, verification commands and status.
- [Local source and service setup](SETUP.md): dependencies, environment variables, coordinator startup and validation.
- [Owner deployment and launch acceptance](OWNER_SETUP.md): release authority, genuine live evidence and pre-pilot gates.
- [ChatGPT browser workers](CHATGPT_SETUP.md): manual initialization, worker/shift identity, relay receipts and F-20.
- [Production pipeline](PRODUCTION_PIPELINE.md): tasks, leases, QA, source promotion and error/recovery semantics.
- [Deployment scaffold](../deploy/README.md): container/Compose boundaries, required factory configuration and known packaging gap.
- [Operator cockpit](../ui/README.md): TypeScript UI run/build and API integration.
- [ToolHost](../host/README.md): authorized tool execution and available sandbox platform.

## Source governance and cleanup history

- [Source repository targets](../studio/workforce/SOURCE_REPOSITORIES.md): five current owner-selected game/framework targets.
- [Architecture Enforcer](../studio/workforce/ARCHITECTURE_ENFORCER.md): owner-reviewed source proposal process.
- [Repository maintenance](REPO_MAINTENANCE.md): source/operational branches, retained components and deletion criteria.
- [One-wave cleanup dispositions](ONE_WAVE_CLEANUP_DISPOSITIONS.md): exact disposition of the 14 audit findings.
- [Legacy scheduler removal](LEGACY_SCHEDULER_REMOVAL.md): merged S00-S09 source removal.
- [External decommission status](LEGACY_DECOMMISSION_STATUS.md): unresolved installed triggers/skills and retained external tool dependencies.
- [Historical workgroup evidence](HISTORICAL_SOURCE_EVIDENCE.md): Git retrieval of original handoffs.
- [Source contribution packaging](contribution-packages.md): unchanged frozen ownership packaging tool.

## Present acceptance boundary

Source PR #37 tests passed on its implementation commit. The source tree alone does not establish that the deployment is configured, that external legacy tasks were revoked, or that a dormant browser conversation can be awakened. Live browser continuation F-20 and full provider readback remain separately evidenced acceptance gates.
