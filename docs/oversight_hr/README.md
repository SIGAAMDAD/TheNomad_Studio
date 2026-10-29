# WG-AK | Independent Oversight HR | provisional V1 implementation

**Submission:** `PROVISIONAL_SOURCE`. **Source authority:** `oversight_provisional` using pinned nonsecret HR v1.2/v1.3 references from Studio V1 R10. **Private repository integration:** `PRIVATE_REPO_INTEGRATION_NOT_VERIFIED`. This is executable clean-room Python and isolated tests, not a verified patch of the private Oversight repository, a deployed confidential intake, or a signing implementation.

## Ownership and purpose

Only `oversight_hr/**`, `tests/oversight_hr/**` and `docs/oversight_hr/**` are owned. The original Studio A-S components, single-writer state/gateway, root composition, protected Oversight scripts/schemas, other parallel workgroups, and all project-specific domain code are unchanged. The module does not import or mutate the Studio codebase or access GitHub/Drive/Issues/PRs/ledgers.

| Scope | Local executable behavior | Excluded operational effect |
| --- | --- | --- |
| S109 | CAS-versioned HR policies, auditable joiner/mover/leaver/restrict/reinstate request/owner decision records, trusted epoch bound, immutable recommendation metadata | No Studio worker ID issuance, roster change, capability grant, ticket creation, or irreversible retirement |
| S110 | Duplicate-safe sealed metadata intake, scoped case access, provenance classes, legal state transitions, investigator/second-review independence, evidence-bound finding, immutable appeal and reopen history | No case decryption, real key custody, automatic punishment, inferred worker psychology, or private case data in Studio |
| S111 | Verified artifact/rework evidence review, bounded coaching/persona proposals, `NOMAD/HR/RECOMMENDATION/1`, allowlisted signed-notice bridge seam, narrow archive integrity manifest | No signature issuance, private deployment, genuine owner issuer, unverified external action, human ChatGPT concurrency proof |

## Usage

Dependencies use Python protocols in `oversight_hr/ports.py`. All ports must come from an independently authenticated service. Call `build_component({})` for an unprivileged facade: it denies privileged operations by default, returns `NOT_CONNECTED` for private access and never spawns threads or performs external writes. To enable isolated tests, inject synthetic fake ports and a journal, as in `tests/oversight_hr/test_hr.py`. Fakes do not establish live identity or signature validity.

```python
from oversight_hr import build_component, MemoryJournal

service = build_component({'journal': MemoryJournal()})
assert service.public_status().data['private_access'] == 'NOT_CONNECTED'
```

To retain metadata across process restarts, the integrator must explicitly construct `SQLiteJournal(private_path)` with a separately scoped **Oversight-only** path. This adapter commits record CAS, event idempotency and hashed audit rows transactionally. It does not connect to the Studio SQLite owner; current unencrypted local SQLite metadata must **not** be used for production confidential records without verified storage encryption, key management, filesystem restrictions, backup and access controls. For production, an alternative injected confidential journal may be required.

All public `Result`s have `status`, `code` and privacy-limited `data`. `Status.OK` in synthetic tests is **only** local operation success; it does not attest to production authorization. Errors are intentionally generic, with no plaintext case data.

## Main methods

`set_policy`, `request_workforce`, `approve_workforce`, `workforce_status`; `intake_sealed`, `case_status`, `audit_case`, `triage`, `assign_investigator`, `record_evidence`, `propose_finding`, `assign_reviewer`, `advance_case`, `record_second_review`, `appeal`, `resolve_appeal`, `reopen_case`; `review_competency`, `propose_persona_revision`, `recommend`; `apply_signed_notice`, `reconcile_notice`; `prepare_archive`, `verify_sealed_archive`, `capacity_summary`.

No public gateway operation is auto-registered. No migration outside the exclusive private module is applied. No new signing format is introduced.

## Authorization gates

- `AccessPort` authenticates and authorizes each scoped operation; `CustodyPort` independently attests sealed intake and case-specific access. Neither an arbitrary ID string nor a worker message conveys privilege.
- `OwnerPort` verifies owner decisions against exact action/subject/policy revision. Missing owner or stale assignment/policy epoch fails closed.
- `EvidencePort` validates immutable source refs, exact digest/revision/collector; a claimed or inferred item is never silently upgraded to corroborated fact. Adverse findings require corroborated evidence and separate review.
- `IndependencePort` must reject subject, reporter, investigator, implicated principal and conflicted reviewer. Missing eligible reviewer leaves case pending.
- `SignedNoticePort` is an injected verifier of the **existing** private `{payload, signature_b64}` object. The local code never signs. It rejects the distinct `signature_ref` transport, wrong allowlist, absent owner grant, expired notices, changed source epoch, stale HR policy and lease epoch, replay, and id collisions. The exact private signed schema must be adapted and independently verified at P2: local required field names are a proposed typed facade only.
- `EnforcementPort` is optional. When explicitly injected, `apply_signed_notice` records an intent before any conditional side effect. `BRIDGE_READBACK_CONFIRMED` requires the provider's trusted readback. Timeout or ambiguous effect yields `PENDING_UNKNOWN`; `reconcile_notice` performs readback only, never a blind retry. No effect port is invoked during ordinary queries or case requests.
- `ArchivePort` independently attests a manifest over sealed archive bytes. The code never stores provided archive bytes, signs a manifest, authorizes deletion, or claims restore/key custody. Hash match alone proves byte integrity, not decryption or recoverability.

## Testing

From the source package root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m unittest discover -s tests/oversight_hr -p 'test_*.py' -v
```

Isolated tests cover fake-service fault injection, sealed intake idempotence/concurrency, replay, missing custody, access denial, conduct/dispute separation, evidence class checks, two independent case reviews, appeals, duplicate tracking, owner CAS, workforce requests, archive tamper detection, SQLite reopen, 500 synthetic worker profiles and 800 HR event requests. The 500/800 exercise is *only* local record processing; `real_chat_capacity=NOT_MEASURED` and live host/provider tests remain `NOT_RUN`.

## Local and external boundaries

The module uses standard-library only. No HTTP, Git, cloud connectors, real private case files, secrets or ordinary ChatGPT host operations are needed or performed. For integrator change requests and pending real verification see `docs/oversight_hr/INTEGRATION.md` and the separate `WG_AK_HANDOFF.md`.
