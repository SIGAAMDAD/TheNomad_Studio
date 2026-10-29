# Owner command wire reference (R9)

Exactly: `command_id`, `operation`, `owner_principal_id`, `request_digest`, `auth_proof_ref`, `idempotency_key`, `issued_at_us`; optional `project_id`, `feature_id`. Do not place a `payload` field in the record: the trusted P2 ingress stores the immutable body separately by `request_digest`. Source of identity: CAB-H `IdentityPort.authenticate_owner`, never the model.

| Owner intent | Operation |
|---|---|
| Submit a brief | `production.submit_brief` |
| Current feature status | `production.status` |
| Inspect tasks/cost/critical path | `production.inspect` |
| Scoped change and rework | `production.request_changes` |
| Pause/resume/cancel | `production.pause`, `production.resume`, `production.cancel` |
| Studio status | `studio.status` |

All 16 owner operations are defined by frozen R9 `OwnerCommand` schema. Other operations (project/bible/cabal/protected effects) are routed to their respective responsible ports by P2; this skill cannot authenticate or execute them independently.

Accepted command means persisted inbox plus outbox event; it does not mean the feature reached QA_READY. A successful QA_READY requires CAB-E signed receipt tied to matching exact tested source commit and the original feature contract digest. `Result.status` reports operation outcome; `FeatureRun.status` reports persistent lifecycle state.
