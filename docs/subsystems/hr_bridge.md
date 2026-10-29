# Studio HR Bridge — WG-AL S112/S113/S114

**Version:** Studio V1 R10, `LOCAL_SOURCE` on pinned A–S baseline
`43d6bc111a88287cacbedbed83d1be2b301dad0a095bc30065d3b5909aee4178`.
**Status:** Local adapter and deterministic tests implemented. No public gateway route,
private Oversight verification, live host, authorized owner channel, CI, or deployment verified.

## Boundary and authority

`build_component(deps: Mapping[str, object]) -> Component` exports the existing
`Component.execute(CommandEnvelope, VerifiedIdentity)` and
`Component.query(QueryEnvelope, VerifiedIdentity)` methods. **This is an internal facade,
not an admitted public API.** Only P2 integration may register trusted routes and injected
providers. `src/nomad_studio/contracts/v1.py`, the existing 19 components and the
signed-notice codec remain unchanged.

The bridge observes the **Studio's** worker, job, assignment epoch, persona digest,
registry revision and launch ticket through `registry_port.snapshot`. It neither owns
nor modifies that registry. The launch ticket is returned to authorized queries as a
SHA-256 digest only. Persona revisions have no authority to affect privileges, capability,
assignment status, QA or owner decisions.

The independent **Oversight** service retains all sealed cases, evidence narratives,
investigator notes and cryptographic secrets. The bridge accepts no case ID or prose fields
for recommendations and cannot read/decrypt a case. Independent HR records must never
be copied into Studio SQLite; only redacted operational metadata lives in
`hrb_observations`, `hrb_recommendations` and `hrb_notices` (one Studio StateStore writer).
No game-domain skill or engine logic is imported or implemented.

## Internal commands (version `NOMAD/CMD/1`, domain `hr_bridge`)

Caller identity is a gateway-created `VerifiedIdentity`, never a request's `actor_claim`.
All commands require a nonempty trusted `request_id`, `idempotency_key`, `worker_id`,
`job_id` and integer `assignment_epoch`, plus current registered worker snapshot.

| Operation | Required scope | Closed `payload` | Effect |
|---|---|---|---|
| `submit_observation` | `hr_bridge:observe` | `{ "event_id": "event-1", "source_event_ref": "ref-1" }` | Verifies external evidence witness and sends `HR/OBSERVATION/1` to independent intake (only if injected); deduplicates event ID |
| `record_recommendation` | `hr_bridge:recommendation` | Exactly `version`, `recommendation_id`, `subject_worker_id`, `observed_evidence_digest`, `recommendation`, `reason_class`, `current_assignment_epoch`, `policy_revision`, `requires_owner`, `effect_status`, `notice_id` | Verifies Oversight recommendation witness, stores minimal owner-pending category and notice ID; **no privilege effect** |
| `apply_notice` | `hr_bridge:enforce` | `{ "notice": { "claims": { ... }, "key_id": "...", "signature": "..." } }` | Validates frozen Studio signed-notice shape and independently authenticated owner approval; applies only a restriction through fenced enforcement port, requires exact readback |

Observation witness fields: `event_id`, `observed_at_us`, `subject_worker_id`,
`source_event_ref`, `observer_principal`, `verification_status=INDEPENDENTLY_VERIFIED`,
`affected_job_id`, `assignment_epoch`, `evidence_sha256`, `incident_category`,
`severity_claim`, `public_summary_code`. The public summary is an enumerated code
rather than arbitrary narrative. Observation submission is **an external effect** only
when P2 supplies the authenticated, separately authorized `observation_intake_port`.
Without it, return `UNSUPPORTED` before a state write.

Recommendations follow the nonsecret V13 `NOMAD/HR/RECOMMENDATION/1` core shape,
with the *additional Studio projection field* `notice_id`; the private Oversight
integration must explicitly map a verified reference to this value. They are proposals,
not clearance, retirement, reinstatement or suspension commands.

Allowed signed actions are strictly the original frozen Studio codec's
`HOLD_JOB`, `QUARANTINE_WORKER` and `REVOKE_WORKER_CAPABILITY`. No new
`GRANT_*`, arbitrary HR action, retirement or reinstatement is admitted. The
separate private Oversight reference also documents an `enforcement-notice.v1`
transport, while the private `signed-enforcement.v1` has a different wire shape.
These are **not interchangeable**. P2 must verify private source bytes and implement
a formally reviewed authenticated translation to the existing Studio codec or refuse
to activate the bridge. The local HMAC fake in tests proves only fixture behavior.

## Internal queries (version `NOMAD/QUERY/1`, domain `hr_bridge`)

All queries require `hr_bridge:owner_read`. All are side-effect free.

| Operation | Payload and selector | Result |
|---|---|---|
| `pending_actions` | empty payload, `limit` 1–100 | `actions` with **only** `notice_id` and `action_category` for owner-pending recommendations |
| `review_summary` | empty payload; exact `worker_id` | `HR/REVIEW-SUMMARY/1`: `sample_count`, verified observation count, independent QA count or `NOT_MEASURED`, capability count or `NOT_MEASURED`, source revision; from trusted metrics port |
| `worker_projection` | payload `{ "assignment_epoch": 7 }`; exact `worker_id`, `job_id` | Current registry status, assignment epoch, persona revision digest, registry revision, SHA-256 of launch ticket; no ticket plaintext |

Operations such as `get_case`, `read_evidence_narrative`, `decrypt_case`,
`approve_worker_privilege`, arbitrary SQL, generic oversight requests or public
unregistered routes always return `DENIED / NOT_SUPPORTED`, even to an owner reader.

## Injected ports and missing-provider semantics

| `deps` key | Interface / independent authority | Missing or inconclusive |
|---|---|---|
| `store` | Frozen `StateStore.read` and `transact`, owned by Studio | `UNSUPPORTED` / `UNKNOWN` on store failure |
| `registry_port` | `WorkerRegistryPort.snapshot` returning immutable `WorkerSnapshot` | `UNSUPPORTED`, stale/reassigned/inactive `DENIED` |
| `evidence_port` | `EvidencePort.verify` returning exact `EvidenceWitness` | `UNSUPPORTED` / `DENIED EVIDENCE_UNVERIFIED` |
| `observation_intake_port` | `ObservationIntakePort.submit` returning independently readback-verified `ObservationAck` | `UNSUPPORTED`, ambiguous send `UNKNOWN` |
| `recommendation_port` | `RecommendationPort.verify` returning `RecommendationWitness` | `UNSUPPORTED` / `DENIED RECOMMENDATION_UNVERIFIED` |
| `oversight_backend` | Frozen `OversightBackend.verify_notice` / `Verification(valid_signature, revoked)` from independent trust domain | `UNSUPPORTED`, timeout `UNKNOWN`; forged/revoked `DENIED` |
| `owner_approval_port` | `OwnerApprovalPort.check` returning exact hash/target/action/epoch/expiry/approval-ID binding | `UNSUPPORTED`; absence/expired/forged `DENIED` |
| `enforcement_port` | Frozen `EnforcementPort.apply_if_current` returning exact `EnforcementReceipt` with readback | `UNSUPPORTED`, timeout/ambiguous `UNKNOWN`; stale/revoked `DENIED` |
| `metrics_port` | Trusted aggregate `MetricsPort.snapshot` | `UNSUPPORTED` / unmeasured counters `NOT_MEASURED` |
| `clock_us` | Integer trusted microsecond clock | `UNKNOWN CLOCK_UNAVAILABLE` |
| `trusted_issuer_ids`, `trusted_key_ids` | Integrator-configured issuer/key allowlists | Empty allowlist denies all notice application |

`verify_notice` must cryptographically verify the exact received signed bytes and
check live revocation through an independently authenticated provider. The local
bridge repeats verification, worker epoch and owner approval checks after durable
reservation. The `enforcement_port` MUST atomically recheck live worker/job/epoch,
revocation, restriction authority and target while committing the actual effect, then
independently read it back. A positive in-memory fake cannot establish this condition
in production. P2 must also enforce proper issuer/key rollover and on-host credentials.

## Receipts, state changes and failure policy

- **Successful observation:** `ACCEPTED / OBSERVATION_SENT` with event ID and evidence
  digest. One redacted row, receipt hash only. Duplicate event ID with the same proof is
  `OBSERVATION_ALREADY_SENT`; different proof/source is `CONFLICT`.
- **Successful recommendation:** `ACCEPTED / RECOMMENDATION_RECORDED` with notice ID and
  action category. `pending_actions` shows category/ID only. No worker state is mutated.
- **Successful notice:** `ACCEPTED / NOTICE_APPLIED` with notice ID/digest/action category.
  `hrb_notices` contains only signed notice digest, notice ID, target, epoch, action,
  status and hashed effect/owner approval IDs. `DomainEvent(domain=hr_bridge,
  kind=NOTICE_APPLIED)` includes only notice digest, ID and action.
- **Expired, revoked, forged, wrong-target, stale-epoch or missing owner approval:**
  `DENIED` before any state or provider effect. An existing notice digest with a new
  idempotency key is `DENIED / NOTICE_REPLAY`; a conflicting key is `CONFLICT`.
- **Ambiguous intake/enforcement result or missing independent readback:**
  `UNKNOWN`, a durable `PENDING_UNKNOWN` reservation, **no automatic retry** and no
  inferred clearance. A same-key applied notice can return `NOTICE_ALREADY_APPLIED`
  only after a new independent preflight. A real operator reconciliation port is a
  P2 integration request.
- **No Oversight service or owner:** no silent `REINSTATE`, `RETIRE`, privilege
  escalation, case disclosure, success receipt or external write.

### Local test commands

Run on a **fresh extraction of the pinned A–S ZIP plus this WG-AL owned overlay**:

```bash
PYTHONPATH=src python -m unittest discover -s tests/hr_bridge -p 'test_*.py' -v
PYTHONPATH=src python -m unittest discover -s tests/contracts -p 'test_*.py' -v
python -m compileall -q src/nomad_studio/hr_bridge
```

Production signed notices, current private Oversight repo/key custody, real owner
approval, live gateway/registry wiring, external providers and negative permission
checks are **NOT_RUN**, not passed by these synthetic fixtures.

## P2 integration requests (out of WG-AL write ownership)

1. **Studio state/root owner:** include the `hr_bridge_*` tables in the single canonical Studio V1 initial schema, verify its pinned hash, and wire the bridge component only through authenticated ports. No historical SQL migration registry or upgrade path exists.
2. **Gateway/trust and HR bridge owner:** explicitly admit only reviewed internal
   HR endpoints, map independent mTLS/service principal scopes and cap payloads.
   Chat content, worker personas and recommendations do not authenticate HR authority.
3. **Oversight AK/AM–AQ / WG-N owner:** verify actual private
   `signed-enforcement.v1` current bytes, independently authenticated translation,
   revocation and key trust, and atomic `EnforcementPort` readback contract. Never
   send raw complaint text or private case IDs over this port.
4. **AG/AH/B/E owner:** implement independently authenticated registry epoch and
   launch ticket snapshot, owner-approved scope/expiry witness and exactly fenced
   worker/job enforcement, including uncertain-effect reconciliation.
5. **Owner dashboard / state owner:** project the two-field `pending_actions` list
   through a separately authorized owner UI; audit the V1 schema identity and state event binding.
6. **P3/P4 independent tests:** real separate Oversight trust, owner unavailable,
   key rotation/revocation, double issuance, concurrent replay, worker reassignments,
   source/QA authority, provider permission denial and privacy under deployed logs.

No external GitHub/Drive/Issue/PR/ledger, case store, production or cloud effect
is attempted by importing/building the adapter or its local tests.
