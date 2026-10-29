# WG-N — Confidential Oversight Enforcement (S40–S42)

**Boundary:** This is the Studio-facing restricted-notice component, not an Oversight case-management service. `nomad_studio.oversight.api.build_component(deps)` implements frozen `Component` (`execute(CommandEnvelope, VerifiedIdentity)` / `query(QueryEnvelope, VerifiedIdentity)`). No implementation imports sister packages. Studio V1 creates the `oversight_*` tables from the one pinned initial schema. The separate private Oversight case database is not merged into this Studio schema.

## Security and trust

- Confidential case narratives, witnesses, evidence, vault private credentials and plaintext case identifiers remain **only on an independent Oversight service/host**. No case-read operation exists on this Studio component. General workers cannot read the private vault or issue/apply/revoke notices using normal worker scopes. Never place cases in Studio SQL, generic event journal, GitHub Issues, shared Drive, telemetry or prompts.
- The separate-host Oversight issuer creates and verifies signed notices with issuer/key IDs, signature, expiry, revocation, exact `(worker_id, job_id, assignment_epoch)`, restrictive action and `case_ref_sha256`. The backend alone validates/decrypts the AEAD-sealed case locator (`enc:v1:<opaque>`). The Studio **does not generate or decrypt case references**. Production cryptography/key custody is supplied by the independent host; the in-memory test backend is explicitly a fake with HMAC and opaque random references, not a production signer or encryption implementation.
- The `VerifiedIdentity` argument must be issued *only* by WG-B's authenticated gateway. `worker_id`, `actor_claim`, and notices in a command body are never authentication. The `oversight:{issue|verify|apply|revoke}` scopes are independently provisioned to trusted service principals (not normal worker/head/QA credentials). The trusted issuer/key allowlists are REQUIRED; absent configuration blocks notice acceptance.
- The injected `enforcement_port` MUST perform a single authority-fenced check of signed notice revocation, target worker/job/epoch, and the conditional restriction effect. It must be idempotent on the signed notice digest and furnish an independently readback-verified receipt. `APPLIED` is accepted only after verified receipt matched byte-for-byte target fields. Revocation before effect blocks it; revocation after effect does **not** silently lift an existing hold. Notice actions are **restrictions only**. `QA_PASS`, owner approval, merge, canon, release and privilege grant are not allowed notice actions.
- Only incident codes, digests, target metadata and status appear in Studio SQL/audit. Exception messages from trusted ports are suppressed. Timeouts preserve UNKNOWN/PENDING_UNKNOWN, and cannot be auto-retried without independent gateway reconciliation.

## Factory dependencies and exact external seams

| Key | Interface | Required for |
| --- | --- | --- |
| `store` | Frozen `StateStore` with `read`, `transact`, `StateTx.append_event` | Issue/revoke/apply durable intents; one WG-A SQLite authority only |
| `oversight_backend` | `OversightBackend` in `ports.py`: `validate_case_reference`, `issue_notice`, `verify_notice`, `revoke_notice` | Separate-host, independently authenticated issuer/vault, active revocation/crypto proof; not WG-B's normal worker account |
| `enforcement_port` | `EnforcementPort.apply_if_current(...) -> EnforcementReceipt` from `ports.py` | Actual authority/gateway restriction effect, epoch/revocation fence, independently verified readback |
| `trusted_issuer_ids` | Iterable of preapproved issuer IDs | Required deny-by-default issuer allowlist |
| `trusted_key_ids` | Iterable of preapproved issuer key IDs | Required deny-by-default key allowlist and rotation |
| `clock_us` | Callable returning trusted UTC microseconds | Optional, defaults to `time.time_ns() // 1000` |
| `max_notice_ttl_us` | Positive integer <= 86,400,000,000 | Optional max TTL, defaults to 24 h |

**Integration change request — authority dispatch mapping:** WG-B and WG-E must agree on an atomic conditional enforcement port: `HOLD_JOB`, `QUARANTINE_WORKER`, `REVOKE_WORKER_CAPABILITY` fenced to the *exact* worker/job/epoch and signed notice digest. The current frozen `Component` operation families do not standardize an equivalent cross-module `apply_if_current` transaction/receipt. Until this adapter and readback are implemented and verified at integration, `apply_notice` is **NOT_CONNECTED**, and no privilege changes are claimed. Do not convert an `ACCEPTED` response from a chat/relay/QA into an authority receipt. The integrator may implement the adapter using supported sister `Component` facade commands and the WG-A transaction service; never directly import sister private implementations. The trusted enforcement adapter must also authenticate its own principal/credential; passing a `VerifiedIdentity` fabricated from client JSON is forbidden.

**Oversight connector:** A production separate trust domain must offer independently verified signing, authenticated AEAD case locator validation, and live revocation reads. The adapter interface is deliberately a port, not a local SQLite vault. TLS/remote isolation, issuer key management and real permissions require deployment verification; absence is `UNKNOWN / NOT_CONNECTED`. When signer IDs or key IDs are unconfigured, `BLOCKED / NOT_CONFIGURED`.

## Closed command schemas

All commands use frozen `version='NOMAD/CMD/1'`, `domain='oversight'`, non-empty `request_id` / `idempotency_key`, and **matching exact** `worker_id`, `job_id` and integer `assignment_epoch` on envelope. `identity` must contain the indicated scope and an unexpired current gateway grant.

| Operation | Required payload (no extra keys) | Required scope | Result |
| --- | --- | --- | --- |
| `issue_notice` | `{case_ref: 'enc:v1:<opaque>', action, expires_at_us}` | `oversight:issue` | The external issuer validates case ciphertext, signs claims; receipt returns signed `notice` and digest only to authorized caller |
| `verify_notice` | `{notice}` | `oversight:verify` | Remote signature+revocation validation; returns digest only |
| `apply_notice` | `{notice}` | `oversight:apply` | Prepared SQL intent, fresh remote verification, epoch/revocation-fenced effect and independent readback; result contains digest only |
| `revoke_notice` | `{notice, reason_code}` | `oversight:revoke` | Independently revoked and recorded `REVOKED`; reason is enum-only: `CREDENTIAL_COMPROMISE`, `INCIDENT_RECOVERY`, `ISSUER_ROTATION`, `OVERSIGHT_WITHDRAWAL` |

Signed notice is CLOSED shape `{claims, key_id, signature}`; claims contain exactly `notice_id`, `issuer_id`, `worker_id`, `job_id`, `assignment_epoch`, `action`, `scope='worker_job_epoch'`, `issued_at_us`, `expires_at_us`, `case_ref_sha256`. Actions are exactly `HOLD_JOB`, `QUARANTINE_WORKER`, `REVOKE_WORKER_CAPABILITY`. Signature is opaque URL-safe base64 and verified by the independent backend over canonical claims. The backend MUST fail closed for tampering, unrecognized issuer/key, revoked or rotated issuer, credential compromise and expired grant; no signature algorithm or signing secret resides inside Studio source. Notice max TTL is 24h; issue time tolerates at most 60s future skew.

## Queries

Frozen `QueryEnvelope(version='NOMAD/QUERY/1', domain='oversight')`, trusted `oversight:verify` scope:

- `verify_notice`: query payload `{notice, assignment_epoch}` and matching `query.worker_id` / `query.job_id`; returns `NOTICE_VALID` plus digest.
- `notice_status`: query payload `{notice_digest}` (SHA-256 hex); returns a redacted `notice_state` only, never case content.
- `health`: empty payload; returns booleans `backend_connected` and `enforcement_connected`. This indicates injected adapter presence only, **not** live production readiness.

Other operations and unexpected payload fields yield `DENIED / NOT_SUPPORTED` or `DENIED / INVALID_SCHEMA`.

## SQLite schema and journal

The pinned Studio V1 initial schema creates `oversight_notices` (digest, issuer, target worker/job/epoch, action, expiry, state, receipt digest, update time), indexed by target, and `oversight_requests` (idempotency key, operation, request hash, safe outcome/receipt). No signature, encrypted case locator, plaintext, complaint, witness or private incident narrative columns. Module journal events are `ISSUED`, `PREPARED`, `APPLIED`, `REVOKED` (redacted `notice_digest` and outcome), through frozen `StateTx.append_event`. No network calls happen inside SQL transactions.

A command idempotency key reserves a request before external action. Reuse with changed request content is `CONFLICT / IDEMPOTENCY_CONFLICT`. Reuse after acceptance returns the redacted prior outcome; signed tokens are not re-persisted. A prepared but ambiguous effect stays `PENDING_UNKNOWN` and is not automatically replayed. Reconciliation must use remote authoritative effect readback plus current issuer revocation and WG-A's single-writer control, through integrator-owned logic; no current `oversight.reconcile` operation was invented.

## Failure and recoverability

| Condition | Result |
| --- | --- |
| Invalid envelope, notice shape, signature, issuer/key, untrusted action/target | `DENIED`, e.g. `MALFORMED_REQUEST`, `INVALID_SCHEMA`, `INVALID_SIGNATURE`, `INVALID_SCOPE`, `UNTRUSTED_CONNECTOR` |
| Revoked or expired notice | `DENIED / REVOKED_GRANT` or `EXPIRED_GRANT` |
| Stale epoch from enforcement authority | `DENIED / STALE_ASSIGNMENT` |
| Untrusted worker scope or expired identity | `DENIED / INVALID_SCOPE` or `EXPIRED_GRANT` |
| Backend / SQL / enforcement adapter unreachable | `UNKNOWN / NOT_CONNECTED` or `PROVIDER_UNKNOWN` |
| Remote effect timed out, missing/mismatched readback | `UNKNOWN / PROVIDER_UNKNOWN`; persisted `PENDING_UNKNOWN` |
| Missing whitelist / issuer configuration | `BLOCKED / NOT_CONFIGURED` |
| Duplicate effect already verified | `ACCEPTED / NOTICE_ALREADY_APPLIED` with no second call |

**Credential compromise/incident recovery:** Rotate or revoke credentials using the independent issuer, issue `revoke_notice` with a fixed reason code, fence future application at the trusted gateway, and retain only redacted digest/outcome evidence. Existing restrictions require a separate authenticated recovery/owner decision; notice revocation never forges that decision. If remote revocation acknowledgement is missing, do not return success. An unconfirmed effect requires independent readback, not an automatic replay or secret-bearing report.

**Not deploy-ready until:** real separate host and key custody, actual AEAD, cryptographic issuer signature verification, issuer/key rotation/revocation, credential separation, production negative-access verification, gateway's authenticated `VerifiedIdentity`, atomic enforcement and source-independent receipt verification, Studio V1 initial-schema boot, real connector interoperability, and failure/recovery drills. These are external acceptance gates and remain `NOT_RUN` for this workgroup.
