# WG-AK P2 integration change requests and evidence gates

## Exact required seam owners

| ID | Required owner/source boundary | P2 action required | Current status |
| --- | --- | --- | --- |
| AK-INT-01 | Private Oversight repository owner | Authenticate current checkout and exact HEAD SHA, compare actual `oversight_security.py`, existing signed-enforcement.v1 schema, custody and case schemas to this provisional API; do not assume wire equivalence to enforcement-notice.v1 `signature_ref` format | NOT_VERIFIED |
| AK-INT-02 | Private encrypted case custodian | Implement scoped `CustodyPort` with current key/issuer, encrypted intake, metadata consistency, denied lower-privilege reads, per-case independent access and correct tenant separation | NOT_RUN |
| AK-INT-03 | Oversight owner authorization service | Implement `OwnerPort` and `AccessPort` using authenticated owner issuer/grant revocation, policy/assignment epoch, case role and independent verification; never use a chat text assertion as owner proof | NOT_RUN |
| AK-INT-04 | Trusted source-evidence service | Implement `EvidencePort` and `IndependencePort` with canonical immutable source SHA/revision and current investigator conflict/lease database; denial must be audited | NOT_RUN |
| AK-INT-05 | WG-AL Studio HR bridge + Studio gateway/roster owner | Map verified minimal notice payload, current policy, worker/job/lease epoch and owner grant to the existing controller allowlist; return independently observed readback without case plaintext, adjust local notice fields only after exact private schema review | NOT_RUN |
| AK-INT-06 | WG-AM/AN/AO/AP/AQ private modules | Adapt workforce, coaching, mediation, case handling and archive modules against AK's injected ports; run combined tests after independent integration. No sibling import is assumed in P1 | NOT_RUN |
| AK-INT-07 | Private case journal/database owner | Choose verified encrypted durable store, storage ACLs, key/backup strategy, append-only custody-chain scheme and retention schedule, migrate local reference journal only after source authority review | NOT_RUN |
| AK-INT-08 | Private archive auditor + independent restore witness | Define production manifest signature/custody and restore evidence, test tamper and unauthorized delete negative paths, verify retention policy from owner | NOT_RUN |
| AK-INT-09 | P3 QA and P4 owner | Independent QA: genuine negative identities, real provider branch/connector limitations, authenticated signatures, stale/replayed notice, break-glass fencing, private case encryption/recovery, staged owner activation | NOT_RUN |

## Data crossing boundary

**Studio to Oversight:** bounded operational observation with event/source refs and SHA256, not reporter narrative. **Oversight to Studio:** minimal reviewed recommendation/verified signed notice and opaque status/readback. **Oversight to owner:** case evidence only via independently authenticated private channel. A purported signed notice must be verified over its exact canonical bytes before application. `case_status` and `audit_case` require per-case access and return only whitelisted metadata/hashes even to scoped actors; they are not a plaintext retrieval API.

## Known intentional incompleteness

- No private source SHA, canonical issuer certificate, encryption keys, signing keys, production case data, deployed authenticator or real host worker session proof were supplied.
- Versioned local `NOMAD/HR/RECOMMENDATION/1` records follow the included reference schema field names. The local metadata audit is not a production signed custody chain. Case event journal fields must be mapped to current private `HR/CASE-EVENT/1` schema and validated there at P2 before enabling confidential operations.
- `SQLiteJournal` provides crash-stable local metadata and transactional idempotency, not a production encrypted case system. The in-memory journal is appropriate for deterministic isolated tests only.
- No live provider latency, concurrency or capacity has been observed. 500 profiles/800 events are synthetic unit-load counts and should not be used as ChatGPT worker admission limits.
- All pending gates above are deliberately recorded as `NOT_RUN`/`NOT_VERIFIED` rather than simulated PASS. A private checkout is an integration requirement, not a reason to withhold this executable P1 source.
