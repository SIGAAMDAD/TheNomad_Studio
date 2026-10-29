---
name: nomad-studio-command
description: Submit authenticated owner production briefs, inspect exact feature status and project scope, request targeted changes, pause, resume, or cancel Nomad Studio R9 feature runs. Use when the studio owner operates a live configured Nomad Studio through its trusted owner command entrypoint. Never mint owner credentials or claim provider effects without receipts.
---

# Nomad Studio Owner Command

Operate only against a configured CAB-H-authenticated P2 ingress implementing the frozen `CommandPort`; ordinary chat text is not an authenticated command. Retrieve live status before changing a feature. Use the exact R9 `OwnerCommand` operation strings and strict schema, as described in `references/commands.md`.

1. Verify which project and feature the owner intends; do not infer feature ID from prose.
2. Ask the trusted gateway to authenticate the owner's proof with CAB-H; derive `request_digest` from the immutable request body at that gateway. Never construct, impersonate or accept a self-declared owner identity.
3. For a brief, submit `production.submit_brief` through the verified entrypoint. The intake is durable and asynchronous. Report pending/blocked and actual feature ID only from the resulting receipt.
4. For status/inspection, fetch live `get_feature_status` or `get_studio_status` through authenticated gateway. Report branch SHA, phase revision, graph revision, task completion, cost and timing gaps as present. Do not claim QA_READY unless quality receipt and matching tested SHA are visible.
5. For change/pause/resume/cancel, use current expected revision (CAS) and a fresh unique idempotency key. Send fine-grained change scope in immutable gateway request body; preserve original brief and automatically replan only affected dependencies. A retry must reuse exactly the same key and same request digest.
6. On `CONFLICT`, refetch status and obtain owner intent against the new revision. On `UNKNOWN_EXTERNAL`, reconcile the provider readback before retry. On `BLOCKED_LOCAL`/`PROVIDER_NOT_VERIFIED`, report the observed missing capability without speculating.
7. Never approve bible activation, constitution/budget expansion, mainline merge or release publication without their separate verified owner proof, even when the feature's ordinary creative decisions are autonomous.

The command skill does not connect a ChatGPT account to host credentials by itself; integration and permission fencing belong to CAB-H/P2. Do not replace native receipts with model-written summaries or mock provider logs.
