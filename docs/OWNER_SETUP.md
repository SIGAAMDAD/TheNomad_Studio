# Owner deployment and acceptance — R5R

## Authority and repository separation

The owner approves accepted scope, policy, canon and protected source integration. Reviewed Studio code belongs on main. Keep studio/runtime (mutable job state) and studio/comms (immutable-addressed relay evidence) intact. Never merge or overwrite them as implementation branches.

The frozen operator contract contains 72 operations. A green source commit proves tested source behavior, not that external browser conversations execute automatically.

## Trusted deployment

1. Confirm current main SHA and passing Python/APSW, TypeScript/TSX, .NET and control CI, including independent 90% line-coverage thresholds.
2. Provision persistent APSW storage and verify backups and restore.
3. Supply a trusted STUDIO_TRUSTED_PORTS_FACTORY, authenticated project/GitHub providers, principal identity, production authorization, source/CI readback, independent QA and release adapters.
4. Configure STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment. The source refuses incomplete factories and invalid 72-route bindings.
5. Use a reviewed TLS proxy around a loopback-only operator endpoint. Protect secrets outside Git history.
6. Install the cockpit and ToolHost as separately controlled executables, and verify the OS sandbox preflight. The Python container does not launch either.
7. Initialize ordinary browser worker conversations manually and assign exact worker/shift/role identities.

## Pre-pilot

Test assignment and lease fencing, unknown post-write outcomes, pre-write failures, rate-limits, interrupted publishers, restart checkpoints, provider identity, lost and duplicate webhooks, real source readback, independent QA and final owner-gated release.

F-20 requires an already-initialized ordinary browser conversation to receive two successive GitHub commands while a single response continues and to produce two authentic remote receipts. Until observed and independently verified, report SOURCE_CI_PASS with LIVE_BROWSER_ACCEPTANCE_PENDING.

See SETUP.md, CHATGPT_SETUP.md, PRODUCTION_PIPELINE.md and REPO_MAINTENANCE.md.
