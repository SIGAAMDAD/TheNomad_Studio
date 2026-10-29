# WG-AQ / S127-S129: provisional Oversight operations

**Source class:** `PROVISIONAL_SOURCE`, clean-room reference implementation. No authenticated private Oversight source, key custody, confidential storage, live API admission, signed notices or owner issuer is present. This module does not claim those capabilities. Its intended target is the independent Oversight repository, *not* the Studio game/control domain.

## Operation surface

- `oversight_operations.api.build_component(deps)` returns an isolated `OperationsService`. **Does not** register `hr.audit` / `hr.recommend_restriction` in any gateway. P2 owns published admission and remapping.
- `intake(SealedIntake, now)`: only opaque identifiers, hashes, retention category, numeric severity and category token. No case narrative, reporter identity, private key, plaintext, ciphertext content, worker wake or source data is imported. Duplicate event + identical envelope is idempotent; event/ID collision is `CONFLICT`; bounded queue full returns `BACKPRESSURE` and asks caller to retain its already sealed receipt and retry. The local in-memory store is **not** a durable intake store.
- `next_case(principal, now)` and `route(principal, case_id, investigator_candidates, now)`: independent case ACL port required. Candidate queue sorts by risk + squared aging to avoid starvation. Assignment requires verified custody, key availability, active investigator, matching clearance, no subject conflict, free capacity and case-specific investigator authorization. Missing reviewer preserves `PENDING` and owner alert; lease/epoch observation for investigator is input data for P2 to authenticate.
- `revoke_investigator`, `case_status`: exact case-scope authorization; no unprivileged listing or narrative exposure.
- `audit(now)`, `host_status`, `record_fault`: deterministic audit tick, overdue case checks, host failure, source collision, missing key and restore failure counters. Aggregated `AuditSummary` has rolling one-hour intake count, oldest age, triage p50/95/99 (when measured), high-risk count, coverage and pending alerts. No case ID in aggregate. Manual clock is injected; no background cron/watch assumption.
- `deliver_alerts(now)`, `acknowledge_alert`: separate explicit owner alert port. Idempotent alert IDs, repeated attempts and `SENT_UNACKNOWLEDGED` until independent ACK proof. Without sender: `UNSUPPORTED`. A successful mock callback is **not** proof of delivery to a human or real owner.
- `stage_archive`, `verify_archive`, `verify_restore`, `archive_publication_readiness`, `authorize_retention_deletion`: see archive gate below. All are pure/in-process operations; there is **no actual publication or deletion**.
- `request_p0_isolation`: requires permitted P0 trigger, high severity, verified custody, verified policy proof, narrow `WORKER_ID`/`JOB_LEASE` scope, exact preapproved action (`REQUEST_LEASE_FENCE`/`REQUEST_ACCESS_REVIEW`), approved action reference and near-term expiration. Returns request `PENDING` only; no gateway call, owner grant or irreversible change occurs. Ordinary communication events are incapable of fleet isolation.
- `recommend_restriction`: permitted existing notice action names only, **advisory** without signing or enforcement. Never emits a fabricated `signed-enforcement.v1` object.
- `simulate_capacity(workers=500, events=800, queue_cap=600)`: measured local monotonic ingest latency p50/95/99, accepted/backpressured, maximum queue depth and observed bottleneck. Synthetic logical workers only; `actual_host_sessions_observed=false`.

## Dependency ports (P2 must source-authorize these)

All receive exactly the positional arguments below and must return the literal Python `True` to approve. Missing, false, or exception means DENY / NOT_VERIFIED / PENDING (as appropriate). The code never imports private HR modules.

| Injection key | Call shape | Restriction |
|---|---|---|
| `authorize_case` | `(principal, case_id, action, now)` | Independent authenticated case-scoped ACL, current epoch, revocation, purpose binding; **mandatory** for privileged case actions |
| `verify_sealed_receipt` | `(receipt_sha256, ciphertext_sha256)` | Independent custody/crypto verification; false keeps quarantine-like metadata-only PENDING |
| `private_key_available` | `()` | Readiness observation only; never supply key bytes to the module |
| `verify_retention_policy` | `(RetentionRule,)` | Policy revision, class, legal hold; local `approved` bool alone is insufficient |
| `verify_ciphertext_encryption` | `(case_id, ciphertext_sha256)` | Independent encryption provenance; actual bytes are not decrypted |
| `verify_archive_signature` | `(ArchiveManifest,)` | Separate trusted signing-key verifier for versioned manifest; `signature_ref` text alone carries no authority |
| `verify_restore_witness` | `(RestoreReceipt,)` | Independently isolated restore process, trusted identity, attest exact archive bytes, current witness epoch |
| `verify_p0_policy_proof` | `(P0Proof,)` | Proven deterministic incident rule, trusted custody/evidence, current policy |
| `verify_gateway_preapproval` | `(P0Proof,)` | Independent preapproval for **exact** reversible, narrow gateway action; no execution here |
| `send_owner_alert` | `(alert_id, kind, created_at)` | Explicit opt-in transport; no private case labels, IDs or narratives |
| `verify_alert_ack` | `(alert_id, proof_ref, now)` | Actual independently verified delivery/owner ACK |

Additional optional configuration: integer `queue_cap` (default 1024), `check_interval_seconds` (default 3600). In-memory dictionaries (`cases`, `events`, `alerts`, `archives`) are explicit **local provisional fixtures only**. P2 must substitute private encrypted transactional storage, atomic dedup/CAS, durable retry, external scheduler and real trusted clock before confidential intake activation.

## Archive integrity and retention

1. Validate **independently proven encrypted** ciphertext against the previously sealed custody SHA-256. The test-only `verify_ciphertext_encryption=True` fixture is *not* production cryptography.
2. Build a deterministic `HR/ENCRYPTED-ARCHIVE/1` descriptor digest from opaque case ID, ciphertext digest, policy digest, class and timestamp. Stage bytes in the local test adapter. No plaintext decryption or raw case narrative.
3. Recalculate ciphertext **and manifest** hashes before any independent signature-check port. Wrong hash quarantines. Missing signature verifier stays `NOT_VERIFIED`; rejected signature blocks further progress.
4. Independently verify a restore receipt; witness identity must differ from staging principal, but **identity assurance and key custody are delegated to the verifier**. Mismatch or restore failure quarantines and raises an alert. Machine witness does not claim to be a human witness.
5. Publication readiness requires both archive signature and independent restore. It does not publish externally. Source retention/deletion eligibility additionally needs lifted custody hold, policy class + digest, minimum aging, case-scope authorization and **independently verified owner approval**. The implementation never actually deletes anything and does not treat archive compression as authorization to delete.

## Evidence levels and known limits

- All local `True` verifier callbacks supplied by tests are **simulators**, not private source/issuer/key/provider proof. They intentionally do not issue signatures or grants.
- No real case, reporter, worker session, account credential, signing key, confidential Github data, service network operation, external ledger or disk archive is used.
- Default `build_component({})` has no privileged access, alert delivery, archival publication or gateway action. It can retain metadata-only sealed-reference envelopes in volatile local memory for testing; no production durability claim.
- Queue capacity counts retained records, not actual active host sessions. Backpressure signals upstream sealed-record replay; requires P2 spool/receipt semantics. Missing spool cannot be represented as durable success.
- `hr.audit` and `hr.recommend_restriction` are integration **proposals**, not activated routes.
- Real private case access negative tests, archive at independent off-host witness, retention legal review, real signed notices, actual owner alert ACK and host session limits remain `NOT_RUN`.

## Source references (bundled R10, SHA-256)

- `REFERENCES/REFERENCE_HR_V12/02_HR_PROTOCOLS_AND_DATA_BOUNDARIES.md`: `4b7c36927dbdb73d70de5589954c8ad18c9dcff23b6773506c2f041cf4082654`
- `REFERENCES/REFERENCE_HR_V13/03_HR_DATA_SECURITY_AND_CAPACITY.md`: `4e2fdc7783370ba410dfcd40c7f882d7c16a5079b7f5ad4f0bb3730b653242a8`
- `REFERENCES/REFERENCE_HR_V13/06_ENFORCEMENT_GATES_AND_FAILURE_POLICY.md`: `94bc9fef1f93e76a784fa0ab1f75b661bd23160fdd0d6b7c73fcad6cabfb18ff`
- `REFERENCES/REFERENCE_HR_V13/07_CROSS_DOMAIN_CONTRACTS_AND_PATCH_MAP.md`: `dd82a2df7af35e88192cba2483478366b636484ec165f85958a71fadfc89ed7d`

## Local verification

```sh
PYTHONPATH=. python -m unittest discover -s tests/oversight_operations -p 'test_*.py' -v
PYTHONPATH=. python -m compileall -q oversight_operations
PYTHONPATH=. python -m unittest discover -s tests/oversight_operations -p 'test_*.py' -k 'test_archive_manifest_digest_mutation_fails_closed' -v
```

All tests use fabricated typed metadata, never real encrypted case data or real private keys. Production verification requires P2 source authentication, P3 independent negative tests and P4 owner-authorized activation.
