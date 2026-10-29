# WG-AP: Independent investigations, evidence custody and appeals

**The Nomad Studio V1 / R10, scopes S124, S125, S126**  
**Submission class:** `PROVISIONAL_SOURCE` (clean-room Oversight reference implementation).  
**Production evidence:** `PRIVATE_REPO_INTEGRATION_NOT_VERIFIED`.

## Source authority and boundaries

This module was implemented from nonsecret `REFERENCES/REFERENCE_HR_V12` and `REFERENCES/REFERENCE_HR_V13` documents in the same R10 handoff. It is **not** a patch to a verified current private Oversight checkout. Its `InMemoryCaseStore` is synthetic, metadata-only and not encrypted, durable, key-backed, externally authenticated or approved to hold real cases. Do not load confidential case material into this source. No production signing, plaintext case access, provider operation, GitHub/Drive/ledger write, game functionality or gateway registration occurs.

The frozen Studio A–S archive `BASELINES/Nomad_Studio_Greenfield_Consolidated_Source.zip` is reference-only, verified SHA-256:
`43d6bc111a88287cacbedbed83d1be2b301dad0a095bc30065d3b5909aee4178`.

## Internal contracts

`build_component(deps: Mapping[str, object]) -> InvestigationService` is the private-boundary equivalent of Studio's `Component` facade; it does **not** implement or alter `Component.execute/query` from the frozen Studio contract. `InvestigationService` presents methods implementing the integration-candidate operations:

| Candidate operation | Method / result |
|---|---|
| `hr.assign_investigator` | `assign_investigator`: candidate identity and independent eligibility verification; no self, subject, reporter, or evidence-author assignment |
| `hr.record_evidence` | `record_evidence`: sealed manifest + trusted source receipt, hash/revision/event/time/verifier match and scope authorization; append-only evidence and custody records |
| `hr.propose_finding` | `propose_finding`: separated fact/inference/allegation/counter refs, evidence sufficiency and explicit NOT_SUPPORTED; informational record only |
| `hr.appeal` | `appeal`: distinct immutable appeal chain; add new sealed evidence, assign independent appeal reviewer, resolve and separately ratify owner-sensitive overturn/amend |

Additional case service methods (not claimed gateway operations): `open_case`, `query_status`, `transition`, `mark_blocked`, `resume`, `record_custody`, `second_review`, `dismiss_no_evidence`, `add_appeal_evidence`, `assign_appeal_reviewer`, `resolve_appeal`, `ratify_appeal`, `owner_disposition`, `audit_integrity`, `public_metrics` and fail-closed `request_signed_notice`.

Evidence submission is **not itself a verifier**. The trusted `SourceVerificationPort` supplies `SourceReceipt`, which must match independently on evidence ID, content SHA-256, immutable source ref/revision/event ID/occurred_at, origin, author and access scope. It also supplies classification, verifier principal and receipt expiry. The trusted `IntakeVerificationPort` also binds sealed intake to reporter/subject identity, immutable source timestamp, policy revision, verifier identity and expiry; an unbound intake or expired verification is rejected. The trusted `SealedCustodyPort` validates sealed manifest, key state, cryptographic integrity and expiry; the local service never receives plaintext and cannot verify ciphertext without the port. Inputs are metadata IDs and SHA digests, never confidential narratives.

`Principal` results only from an injected `IdentityPort`, not a client assertion. Every mutation (even idempotent retries) requires current `AuthorizationPort` approval; evidence uses its own scope check. `EligibilityPort` verifies current investigator or appeal reviewer credentials, specialty/training/lease, revocation and independence beyond the local conflict validator. `ClockPort` supplies trusted time and receipt expiry checks. `OwnerDecisionPort` verifies exact case-chain hash, decision scope, policy revision, expiry and issuer externally. No ordinary chat text or mock identity proves those properties in production.

## Case state machine

The normal progression is:

`SEALED_RECEIVED -> SCREENED -> TRIAGED -> INVESTIGATING -> EVIDENCE_READY -> FINDINGS_DRAFT -> SECOND_REVIEW -> OWNER_DISPOSITION -> CLOSED`.

Supporting states: `BLOCKED_ACCESS`, `BLOCKED_INDEPENDENCE`, `WAITING_EVIDENCE`, `WAITING_OWNER` via protected `mark_blocked`/`resume`; `DISMISSED_NO_EVIDENCE` for explicitly unsupported cases; `APPEALED` for an immutable appeal chain; `REOPENED` when the appeal warrants re-investigation. After reopening, a new investigator must be assigned before returning to `INVESTIGATING`. Rejected independent reviews block, rather than being counted as acceptance. An adverse finding never applies a workforce action; the final case transition requires independently verified owner disposition.

Evidence classifications match the bundled `hr_case_event_v1` reference: `CLAIMED`, `OBSERVED`, `CORROBORATED`, `INFERRED`, `DISPUTED`, `INVALID_UNREADABLE`. `INVALID_UNREADABLE` receipts are rejected. A `SUPPORTED` finding requires at least one trusted `CORROBORATED` reference or independent `OBSERVED` records from at least two distinct source origins; evidence from duplicate source origins does not multiply proof. Unresolved counterclaims or corrected facts block `SUPPORTED`. All counterrefs must be explicitly acknowledged. `NOT_SUPPORTED` records an explicit insufficient-evidence outcome and never creates a sanction. These are conservative P1 reference rules; P2 must reconcile them with the actual authoritative policy clauses and source verification semantics.

## Local integrity and replay model

A `Case` has two SHA-256-linked ledgers: case events and custody events. Entries store metadata digests and chain pointers; they do not include case body text. `InMemoryCaseStore` uses deep-copy-on-write under a lock, checks both chains on every access, and ties every recorded evidence reference to its custody entry and event metadata. Tampering with a stored source revision, manifest or evidence entry triggers `INTEGRITY_FAILURE` in local tests. SHA-256 hash chaining is tamper-*evident* only in the presence of an independently protected root; it is not a cryptographic signature or immutable archive. A privileged attacker able to rewrite all state could regenerate this chain; P2 requires trusted signatures/anchors and encrypted durable custody.

Case IDs are opaque random `CASE-` followed by 32 lowercase hexadecimal characters. Intake IDs are deduplicated, conflicting repetitions reject, and a repeated command ID with identical intent returns the first result without a second mutation after rechecking the actor's current authorization. Conflicting command-ID reuse rejects. The provisional in-memory store will not survive a process restart; P2 must preserve intake and command deduplication across durable restarts and enforce compare-and-swap generations at the real storage port.

`query_status` returns only opaque case ID, state and non-confidential counts/digest to a case-authorized identity. `public_metrics` exposes only total case count. No raw evidence, source URI, reporter, narrative, case body, key, credential or signer material is returned in normal status. Case identifiers and status themselves are restricted and must not appear in generic Studio reports. Production privacy assurance requires real lower-privilege/side-channel tests with independently scoped identities.

## Decision and appeal separation

`second_review` is by a current independent principal, distinct from original investigator, case parties and evidence authors. Appeal reviewer must also be distinct from the original second reviewer, finding author and previous appeal reviewers/appellants. The `EligibilityPort` can deny stale or unavailable reviewer credentials. `resolve_appeal` records `CONFIRM`, `AMEND`, `OVERTURN`, `INSUFFICIENT_EVIDENCE`, or `NEEDS_OWNER`, retaining old events. `AMEND`/`OVERTURN`/`NEEDS_OWNER` cannot become an enacted state change before the owner port ratifies the exact current case-chain hash. The service's re-open outcome clears the superseded finding/review pointers while retaining the original event/custody chains and appeals.

Owner decisions here are *verified references only*, never minted owner authority. Any `NO_ACTION`, `REVIEW_ONLY` or `REFER_TO_GATEWAY` disposition only records an Oversight recommendation/referral; it does not grant a permission or update Studio. `request_signed_notice` deliberately returns `UNSUPPORTED` in P1 because the current canonical private `signed-enforcement.v1` schema, signing keys, issuer registry, expiry/epoch policy and verification code were not supplied. P2 must adapt the private existing `{payload, signature_b64}` object and verified issuer semantics, without substituting the incompatible `enforcement-notice.v1` / `signature_ref` format. Only a trusted signer/verification port may issue actual notices after P2 private-source review.

## Dependencies to inject at P2

| Name | Dependency contract | Missing behavior |
|---|---|---|
| `identity` | trusted resolver `resolve(opaque_credential) -> Principal` | `UNSUPPORTED` |
| `authorization` | independently verified case/scope ACL `allow(...)` | `UNSUPPORTED` / `DENIED` |
| `clock` | authoritative UTC-aware time `now()` | `UNSUPPORTED` |
| `sealed_custody` | independent case-key/manifest/signature check `verify_seal(...)` | `UNSUPPORTED` / `DENIED` |
| `intake_verification` | trusted source-bound, unexpired sealed intake with subject/reporter metadata | `UNSUPPORTED` / `DENIED` |
| `source_verification` | independently authenticated `verify_source(...) -> SourceReceipt` | `UNSUPPORTED` / `DENIED` |
| `investigator_eligibility` | current reviewer/investigator credential and conflict check `eligible(...)` | `UNSUPPORTED` / `DENIED` |
| `owner_decision` | issuer-verified, exact-case/revision decision `verify_decision(...)` | `UNSUPPORTED` / `DENIED` |
| `case_store` | atomic, encrypted, durable Oversight case store, custody/audit/replay | local synthetic store only; **never deploy** |

### P2 integration change requests

1. **AP-P2-01**: Obtain and authenticate current private Oversight checkout and exact commit SHA. Compare the reference `HR/CASE/1`, current case/custody store APIs and canonical signed-enforcement contract. Adapt only under a protected integration review; do not treat the clean-room tree as an already merged private source patch.
2. **AP-P2-02**: Replace the synthetic `InMemoryCaseStore` with an encrypted store enforcing current case-specific permissions, durable atomic CAS/deduplication, encrypted manifests, tamper-proof archive roots, independent restoration verification, retention policy and incident audit. Prove denial to lower-privileged actors through real independent auth sessions.
3. **AP-P2-03**: Bind identity/auth, source verification and eligibility to current service-controlled worker/assignment epoch, source revision and investigator/appeal reviewer credentials, training and revocation. Deny stale leases and self-review, including a two-chat same-investigator conflict.
4. **AP-P2-04**: Map confidential event records to current `hr_case_event_v1` and existing signed-notice contracts. Distinguish signed-enforcement `{payload, signature_b64}` from separate `enforcement-notice` transport; no implicit conversion. Implement signer/verifier only in verified private Oversight authority, and test expiry, replay, issuer and target/epoch checks.
5. **AP-P2-05**: Register `hr.assign_investigator`, `hr.record_evidence`, `hr.propose_finding`, `hr.appeal` through the actual authenticated case gateway after access-policy review. Avoid adding unreviewed operations or general employee/case access to Studio query surfaces.
6. **AP-P2-06**: Independently test production backup/restore, case metadata secrecy and side channels, both source and ciphertext tampering, authorized correction flows, owner ratification/readback and failure-restart replay semantics. Signed external case notifications and any restriction enactment require owner-approved P4 activation.

### Explicit unrun external gates

`private_repo_integration`, `production_encryption_and_signatures`, `independent_confidential_access`, `production_provider_permissions`, `real_owner_issuer`, `signed_enforcement_notice_end_to_end`, `durable_restart_replay`, `real_host_worker_lease`, `independent_production_QA`, `P4_deployment`: **NOT_RUN** (no verified current private source, live private keys/case fixtures, production auth issuer, operational host or approval in P1). Isolated mock tests show deterministic local behavior only.

## Reproduce isolated tests

From the extracted owned ZIP root:

```bash
PYTHONPATH=. python -m unittest discover -s tests/oversight_investigations -p 'test_*.py' -v
python -m compileall -q oversight_investigations
```

All 42 unit tests are local, synthetic and standard-library-only. Archive hashes and structural verification are recorded in the adjacent `WG_AP_MANIFEST.json` and `WG_AP_HANDOFF.md` outside this owned source ZIP.
