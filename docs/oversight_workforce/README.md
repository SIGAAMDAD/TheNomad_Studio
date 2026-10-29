# WG-AM / S115-S117 — Oversight workforce administration (provisional)

**Implementation classification:** `PROVISIONAL_SOURCE`, `oversight_provisional`, R10, HR/WORKFORCE-RECOMMENDATION/1. Not a patch to an authenticated private Oversight checkout, signed notice producer, case store, Studio gateway, or active worker registry.

## Public, standalone API

Import `build_component(deps: Mapping[str, object])` from `oversight_workforce.api`, producing `WorkforceComponent` (`operation = 'hr.qualify'`). This operation is **proposed**, not registered at any gateway. `recommend(QualificationRequest)` returns `Decision`, `dossier(worker_id)` returns a read-only history, and `handover(Decision)` returns a **suggestion** to fence old leases/revoke tickets and advance the assignment epoch. It does not perform those actions. `WorkforcePlanning` implements `intake`, `suggest_review`, `coverage`, `schedule`, and `backlog`.

All inputs and outputs are Python `@dataclass(frozen=True)` values declared in `contracts.py`. `Lifecycle` supports `JOINER`, `QUALIFY`, `PROBATION`, `ACTIVE_REVIEW`, `ROLE_CHANGE`, `TEMP_RESTRICTION`, `REINSTATEMENT`, `RETIREMENT`. All lifecycle decisions are proposals. A recorded recommendation is *not* a current owner grant or permission to enact a change. The Studio identity reader remains authoritative for active state; dormant JOINER and REINSTATEMENT identities require an existing reserved/inactive Studio identity and cannot reuse a retirement tombstone.

### Injected authorities and fail-closed behavior

| Dependency key | Required contract | Unavailable behavior |
|---|---|---|
| `identity_reader` | `worker_id -> Identity or None`, authoritative Studio snapshot with current role, identity/assignment epochs and persona version/digest | `UNSUPPORTED` |
| `role_reader` | `role_id -> Role or None`, canonical role and required permissions/capabilities | `UNSUPPORTED` |
| `grant_verifier` | `(GrantClaim, Identity, now) -> bool`, current **externally authenticated** exact action/role/privilege/epoch/policy/expiry authorization | `NOT_VERIFIED` |
| `capability_verifier` | `(CapabilityEvidence, now) -> bool`, trusted provider attestation bound to actual receipt and target | `NOT_VERIFIED` for qualification transitions |
| `training_verifier` | `(TrainingEvidence, now) -> bool`, trusted objective evaluator/source attestations | `NOT_VERIFIED` for qualification transitions |
| `clock` | `() -> int`, trusted server Unix timestamp in private integration | local clock by default; not an attested host time source |
| `journal` | `LocalJournal`, isolated local SQLite reference journal for unit tests and bounded metadata only | in-memory local journal |

Passing test lambdas as verifiers checks control flow, **not** cryptographic approval or real platform trust. P2 must replace these with verified, independent private ports. Exceptions from providers return `NOT_VERIFIED`; no fallback authorizations. The module does not import private Oversight security source, hold issuer keys, interpret unsigned enforcement messages as notices, or grant privileges.

Validation checks a canonical role, nonrecyclable identity, status, epochs, persona digest and revision, exact owner grant binding, no implicit privilege escalation, current capability receipts, external evidence attestations, objectively assessed non-self training, evidence/approval expiry and deterministic replay. Missing proof yields `INSUFFICIENT_EVIDENCE`; stale/conflicting/invalid proof denies. Same ID with the same input is idempotent; replaying a previous `RECOMMENDED` decision revalidates current authority. Role change requires exact owner approval for the target privilege set. The dossier contains immutable recommendation metadata and SHA-256 evidence references only, not private case text.

`Decision.verified_facts` enumerates checked evidence categories; `Decision.inferences` explicitly labels recommendation-only status. `Decision.valid_until` is the earliest relevant evidence/grant expiry, not an automatic admission expiration or revalidated production lease. `minimum_permissions` is the proposed role's canonical privileges, not an issued token.

### Workforce planning and review independence

`WorkforcePlanning.intake(ReviewItem)` retains only bounded incident metadata and evidence digest, with dedupe and replay conflict handling. It must only receive information already permitted for the private Oversight authority. `suggest_review` excludes accused subject, originating principal, explicitly conflicting principals, earlier finding authors, expired security training, wrong specialty, inactive reviewers and exhausted capacity. If none qualify it emits `BLOCKED_INDEPENDENCE`, never assigns the accused, author, or an invented reviewer. Actual case ownership and confidential evidence access require WG-AK/private verified case custody and credential checks.

`coverage(Studio identities, approved CoverageNeed targets)` computes deficits by canonical role/department and explicitly rejects duplicate worker IDs. `schedule(identities, admitted_slots, capacity_source)` proposes a stable identity ordering, with capacity supplied by an independently approved policy or measurement. No 500-concurrent-chat assertion is made. Missing approved slots returns `NEEDS_POLICY`; it never opens sessions. `LocalJournal` stores 800-event synthetic queue references with `PENDING/ACKED` cursor, idempotency and exact digest-bound acknowledgment. It is a local SQLite load fixture; production persistence/encryption/restore semantics must be validated by private repo owners.

## Running isolated tests

From the **root of the extracted owned source archive**:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m unittest discover -s tests/oversight_workforce -p 'test_*.py' -v
```

Tests include forged/expired grants, replay after revocation, epoch mismatch, expired and self-assessed training, privilege-escalating request, retired identity reuse, independent reviewer conflicts, bad evidence, lifecycle coverage, no provider effects, a simulated 500-identity roster and 800-local-event queue with measured SQLite page bytes and insert P50/P95/P99. These do not qualify actual live ChatGPT capacity or private case security.

## Required P2 integration seams

1. **Private Oversight source/custody owner (AK):** verify authenticated repo/branch and exact revision, implement confidential encrypted journal and access checks, immutable custody records, case role ownership, policy revision and reference verification. The bundled LocalJournal has no production encrypted-at-rest claim.
2. **Studio roster/role owners (AG/G):** provide read-only verified identity snapshots, stable canonical role catalog, tombstone/epoch reader and current persona digest; prevent retirement ID recycling and stale ticket redemption.
3. **Owner/grant verifier (AH/AK):** integrate independently authenticated owner grants bound to exact action, role, privilege, worker epoch, policy revision and expiry; verify revocation and authorization against issuer and current state.
4. **Tool/skill attestations (AJ):** confirm exact capability receipt source revision, objective training data, evidence retention/expiry and verifier independence. Test mocks may not be used as production evidence.
5. **Studio HR bridge (AL):** translate an independently approved scoped finding to the existing **canonical** private signed-enforcement object via verified private issuer, including signature, expiry, replay, target/assignment/lease epoch and owner grant. HR recommendation alone is not an enforcement action.
6. **Scheduling/owner integration:** provision policy targets, case capacity, independent reviewer roster, approved staff coverage needs, owner-visible handover decision and audited readback. Real session limits and host wake remain `NOT_RUN`.
7. **Security/QA gates:** verify access to private case evidence and owner channels, independent separation, key custody, database backup/restore, current signed notice schema, repeated provider denial, actual 500-shift admission and 800 host session claims before activation.

## Explicitly excluded

No live enforcement, worker registry writes, private case plaintext, role registry ownership, event/lease mutation, secret/signing key, GitHub/Drive/Issues/PRs/ledgers effects, independent QA verdict, human employee classification, model emotion assessment, game-domain implementation, or runtime session spawning. The clean-room source exports no external network client.
