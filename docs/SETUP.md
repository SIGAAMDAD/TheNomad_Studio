# R5R source and service setup

This supersedes V2 scheduled-task setup instructions.

1. Use the reviewed main source SHA. Keep studio/runtime and studio/comms as separate operational branches; do not overwrite them from main.
2. Install Python and production APSW, Node/npm for the cockpit and the correct .NET SDK for ToolHost. Follow README.md verification commands.
3. Use STUDIO_COMPOSITION_FACTORY=studio.app.production_factory:build_from_environment and configure STUDIO_TRUSTED_PORTS_FACTORY to a trusted importable factory returning actual production TrustedPorts adapters.
4. Provision durable APSW state, real credentials, project/branch bindings, backup-and-restore procedures, and verified source/QA/release providers. The service rejects missing ports or incomplete 72-command composition.
5. Bind the operator HTTP listener to loopback and expose it only through a reviewed TLS proxy. Signed webhook ingress is optional; event receipt merely wakes independent readback.
6. Authenticate and distinguish each project, worker, shift, command, lease generation and remote mutation. Record durable effect intents before sending external writes; reconcile ambiguous outcomes by exact readback.
7. Run complete Python/APSW, TypeScript, .NET, source-provenance and control workflows on one final SHA. Each production language has a 90% line-coverage gate.
8. Conduct real Tier C browser continuation F-20 before claiming the desired manually-initialized worker pool operates end-to-end.

No scheduled ChatGPT task or model API worker is installed by this process. See OWNER_SETUP.md, CHATGPT_SETUP.md and PRODUCTION_PIPELINE.md.
