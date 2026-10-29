# WG-AU: Creative change and publication enforcement (S139–S141)

## Authority and limits

This module is a **local, project-neutral control-plane guard** compiled against
frozen `nomad_studio.contracts.v1` from the pinned Studio A–S baseline. It uses
`build_component(deps: Mapping[str, object]) -> Component` and implements exactly
three *candidate* operations: `creative.prepare_change`,
`creative.verify_constraints`, `creative.check_build`. It does **not** register
new privileged gateway operations, mutate provider state, write SQL, change an
owner policy, sign an owner grant, or run game-domain parsers. An `ACCEPTED`
result is a **local unsigned gate receipt**, never authorization for an arbitrary
connector to perform a remote write.

The supplied R10 reference rules govern behavior. Policy snapshots must be
**current, effective policies** resolved by the policy authority, including the
most restrictive ancestor and tag rule and intersections of BOUNDED constraints.
WG-AU does not invent an independent policy resolver or accept a client-supplied
policy. Impacts include trusted dependency expansion. Unknown impacts, bindings,
providers, constraints, QA, readback, or build checks fail closed.

### Public interface and payload

The component accepts frozen `CommandEnvelope` and `VerifiedIdentity`, produced
by the trusted gateway, and returns frozen `Outcome`. `domain` must be `creative`.
`identity.scopes` must include the exact public operation. `worker_id`, `job_id`,
`assignment_epoch`, and `source_sha` in the envelope must match the payload.

Required payload keys:

| Key | Contract |
| --- | --- |
| `stage` | One of the stages below, assigned to its public operation |
| `project_id`, `job_id`, `worker_id`, `source_repo`, `source_ref` | Nonempty, exact scope identifiers |
| `source_base_sha` | Exact 40-character source SHA, pinned to claim/assignment |
| `candidate_digest` | Exact 64-character lowercase artifact SHA-256 |
| `assignment_epoch` | Nonnegative integer, checked against trusted assignment |
| `policy_revision`, `policy_digest`, `policy_epoch` | Exact current policy identity from independent policy snapshot |
| `declared_node_ids` | Nonempty unique node IDs |
| `declared_paths` | Nonempty normalized source-relative paths |

Optional stage-dependent fields: `grant_id`, `release_grant_id`,
`target_revision` (required for provider readback / QA), and
`build_manifest_digest` (64-character digest required for build/release). A
client-supplied `owner_approved`, `verified`, fake signature, or policy object is
ignored and never grants authority.

### Stage dispatch and required evidence

| Public operation | Stage | Gate required (cumulative, in addition to identity, policy, assignment and effective nodes) |
| --- | --- | --- |
| `creative.prepare_change` | `job.enqueue` | Approved feature, mapped declared nodes, scoped job |
| `creative.prepare_change` | `scheduler.claim` | Fresh assignment/worker authority epoch; locked mutation needs exact exception |
| `creative.prepare_change` | `preview.prepare` | Pure proposal only; no protected write or provider effect |
| `creative.prepare_change` | `publication.prepare` | Independent diff/impact, exact paths, permission probe, bounded parser; protected exact candidate grant |
| `creative.verify_constraints` | `publication.dispatch` | All prepare gates plus per-stage exact grant and single-use reservation |
| `creative.verify_constraints` | `publication.readback` | Fresh diff/policy plus independently observed exact provider revision |
| `creative.verify_constraints` | `review.verdict` | Readback plus independent reviewer QA verdict pinned to candidate/policy/revision |
| `creative.verify_constraints` | `review.merge_intent` | All above plus refreshed grant and single-use reservation |
| `creative.check_build` | `game_build.promote` | Independent diff/readback/QA plus exact build conformance, current grant and reservation |
| `creative.check_build` | `release.accept` | Exact build conformance plus an independent owner `RELEASE` grant pinned to build manifest; protected scopes also need an exception/acceptance grant |

`LOCKED` scopes require a current `EXCEPTION` for each mutation stage.
`REVIEW_REQUIRED` scopes require a current exact-candidate `ACCEPTANCE` at
publication and subsequent stages. `BOUNDED` scopes require a typed parser proof
covering all affected bounded nodes; `UNKNOWN` is not accepted. `DELEGATED`
scopes still require verified assignment, source ownership, provider protection,
independent impact, QA, and a per-effect `EXECUTION` grant. A final `RELEASE`
grant is mandatory for all releases. Protected `release.accept` additionally
requires `grant_id` for the creative exception/acceptance and
`release_grant_id` for the exact build release decision.

### Injected trusted ports

`build_component` consumes these dependency keys (interfaces in `ports.py`):

| Key | Required method | Owner for P2 integration |
| --- | --- | --- |
| `policy` | `current(project_id)` | WG-AR effective policy resolver / current authoritative epoch |
| `assignment` | `verify(change, verified_identity)` | Scheduler/trust and exact source/worker/job/scope binding |
| `impact` | `observe(change)` | WG-AS semantic impact + independent source/asset provider diff |
| `constraints` | `verify(change, observed, policy)` | WG-AS trusted typed parser and bounded constraint verifier |
| `permissions` | `verify(change, verified_identity)` | Security/provider ruleset observer, **actual permission checks** |
| `readback` | `verify(change)` | WG-J provider readback against exact revision and candidate |
| `qa` | `verify(change)` | WG-I independent QA with exact source/policy digest |
| `build` | `verify(change)` | Build and AV release conformance readers |
| `grants` | `get(grant_id)` and `reserve_once(grant_id, stage, subject, request_id)` | Owner-authorized decision issuance and atomic grant-consumption storage |
| `clock_us` | `() -> int` | Trusted clock, injected in tests |

All ports return immutable attestation dataclasses in `model.py`; they must be
constructed by **trusted backends** from current authoritative sources, **not**
from command input or an LLM answer. The local tests use deterministic mocks
solely to exercise the gate. They are not service identity, owner signatures,
provider ruleset evidence, or real verification. A missing required port yields
`UNSUPPORTED`; a port exception yields `UNKNOWN`; contradictory or stale proof
is `DENIED`. A real production adapter must verify source-head/CAS and issuer
status independently. The code invokes no provider mutations itself.

`grants.reserve_once` must be a durable atomic issuer-checked conditional write
owned by the real grant authority: reject replay across request IDs, revocation,
expired grants and consumed stages. This module deliberately does not claim its
local receipt is a cryptographic signature, persistence event, or promotion.

### Invariant receipt

A successful call returns:

- `Outcome(state='ACCEPTED', code='CREATIVE_GATE_PASS')`
- `receipt_id`: deterministic SHA-256 of gate input IDs and hashes, policy epoch,
  independent evidence refs and grant ID
- `payload.receipt_kind='LOCAL_GUARD_ONLY'`
- `payload.provider_effect_executed=False`

Receipts are *local decision fingerprints*. P2 must independently verify and
persist any genuine canonical production receipt. The gate has no SQL migration,
external side effect or root `app.py` modification.

### Actual boundary wiring required from P2

1. **Scheduler/job creation and claim:** invoke `job.enqueue` / `scheduler.claim`
   before accepting a source mutation lease; propagate denied outcomes.
2. **Publication:** invoke `publication.prepare` after provider-side tree/asset
   diff, and `publication.dispatch` immediately before the sole scoped remote
   writer with atomic effect reservation. Only the trusted service principal
   may hold actual write privileges. Run `publication.readback` before advancing.
3. **Review and merge:** invoke `review.verdict` on trusted QA output and
   `review.merge_intent` immediately before merging; recheck current policy,
   source head, grant issuer, assignment epoch and exact candidate.
4. **Build/release:** invoke `game_build.promote` and `release.accept` on exact
   compiled/build manifest digests, subject to external conformance probes and
   current owner release acceptance. Project-specific checks belong to
   specialist target repositories, not Studio Python.
5. **Gateway and provider deny-test:** register only through integrator-owned
   gateway/trust policy. Prove by a **real ordinary worker identity** attempting
   a direct protected provider write and observing provider-side denial. A mock
   `ProviderPermissionProof(status='ENFORCED')` cannot satisfy this gate.

### Explicit verification status

P1 covers mock-port unit tests, including replay, revocation, wrong project,
policy/source/epoch mismatch, unknown impact, parser failure, direct-write
*simulation*, readback mismatch, QA/build failure and a no-game-logic check.
**NOT_RUN**: authenticated owner decision issuance, actual GitHub/Drive
permissions negative probe, live provider diff/write/readback, independent QA
and build on a target project, actual native game/tool parser capability,
end-to-end release admission, host authorization and P2/P3/P4 integration.

No private Oversight source was used or modified. This is a `LOCAL_SOURCE`
submission against the pinned Studio baseline; the private-source provisional
`reference_files`/`PRIVATE_REPO_INTEGRATION_NOT_VERIFIED` fields do not apply.
