# R5R deployment handoff

This folder contains the Python coordinator image and a local Compose scaffold. It does not deploy the browser workforce, the C# ToolHost or the TypeScript cockpit.

## Production factory

The service launches python -m studio.app.cli. Supply STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment plus an installed STUDIO_TRUSTED_PORTS_FACTORY=<trusted_module>:<factory>. The trusted adapter factory must provide authentication, project-bound GitHub source and relay transports, authorization, source/CI readback, review, release and recovery, all sharing the actual APSW writer.

Absent configured capabilities and missing 72-operation bindings fail closed, rather than starting a simulated backend.

## Operating constraints

Keep persistent APSW storage and verified backups. Bind operator HTTP to loopback; place a trusted TLS proxy in front. Optional signed GitHub webhooks are readback hints, not proof of completion. Configure secrets through deployment-secret management, never committed text.

The Compose default restart policy is no. The environment-specific Student profile is optional and is not an AI worker runtime.

## Validation

Before a production pilot, require exact-SHA Python/APSW, TypeScript, .NET, source provenance and control CI with per-language 90% production line-coverage gates, real provider readback and an observed Tier C browser worker execution. See docs/OWNER_SETUP.md and qa/TIER_C_MANUAL_EVIDENCE.md.
