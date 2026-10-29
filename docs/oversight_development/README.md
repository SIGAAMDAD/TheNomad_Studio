# WG-AN — Oversight Development (S118–S120)

Status: **PROVISIONAL_SOURCE** for The Nomad Studio V1 R10 single P1 implementation wave. This package is standalone and project-neutral. It is **not** an authenticated private Oversight repository patch. `PRIVATE_REPO_INTEGRATION_NOT_VERIFIED`.

## Implemented behavior

**S118 — Competency review.** `oversight_development/competency.py` evaluates independently verified artifact evidence against a cohort-specific and role/specialty-specific `CompetencyPolicy`. All six dimensions are required: validated tool operation, source correctness, repeatability, rework attribution, checkpoint integrity, and peer handoff clarity. Policy requires configurable minimum samples, distinct artifacts, distinct UTC observation days, maximum sample age, per-dimension observations, and passing fraction. Each sample is pinned to SHA-256 and an evidence reference. An injected read-only verifier must return a matching observed digest, independently attested per-dimension rubric measurements, and an independent observation reference; if source SHA is asserted it must be independently observed too. Caller-supplied pass/fail assertions are not counted unless the trusted receipt confirms them. All observed proof references are included in deterministic evidence digests.

Incomparable cohorts, stale observations, unknown proof, and provider failures never become worker-negative findings. Verified environmental outages, source conflicts and role mismatches are recorded as excluded (with source attribution reference) and removed from worker-scored denominators. Reviews fail closed to `INSUFFICIENT_EVIDENCE` or `UNSUPPORTED` until thresholds are met. A review returns per-dimension measurements and explicit deficits; it never assigns global personality, seniority or comparative-worker scores. A changed cohort, policy revision, assignment epoch or artifact changes the digest.

**S119 — Coaching/requalification.** `coaching.py` proposes an immutable bounded plan with evidence baseline, deficit dimensions, allowed action enums, exact fixture references, minimum retest observations and fractions, due checkpoint, and deterministic plan ID. Actions are recommendations: improve instructions, update skill references, schedule requalification, or request persona-style review. A new sufficient review can produce `REQUALIFIED_ON_EVIDENCE` and `OWNER_REVIEW_ONLY`, but cannot change assignment, role, permissions or workforce status. A stale epoch/policy or replayed baseline is rejected. `recommendations.py` projects local recommendations to the fields/enums of the bundled `NOMAD/HR/RECOMMENDATION/1` reference; `effect_status` is always `PROPOSED`, `requires_owner` is true, and no case/signature is fabricated.

**S120 — Persona and conduct.** `persona.py` reads the current snapshot from an injected read-only Studio WG-AG port and checks current version/digest before producing a **style-only** delta and exact proposed next version/digest. The proposed request is not applied, approved, signed, or silently written. Allowed presentation keys (candidate mapping) are `skepticism`, `exploration`, `directness`, `formality` with integer range 0–100. Any permission/role/launch/tool/test-policy key is denied; all changes require later owner review. `conduct.py` classifies only independently verified observed behaviors: professional skepticism and technical exploration route to technical review, documented unprofessional messages to contextual conduct review, verified forgery/unauthorized changes to security review. There is no intention, emotion, mental-state, personality-risk, or worker popularity inference. No route enacts a sanction.

## Local provisional facade

```python
from oversight_development.api import build_component

component = build_component({
    "evidence_verifier": synthetic_or_real_readonly_verifier,
    "persona_registry_reader": synthetic_or_real_readonly_registry_reader,
})
review_result = component.execute("hr.review_worker", typed_review_request)
coaching_result = component.execute("hr.coaching_plan", typed_coaching_request)
```

The only **proposed** public operation names are `hr.review_worker` and `hr.coaching_plan`; the integrator must explicitly bind and authorize them in the independent private Oversight host. The returned `DevelopmentResult` and `CompetencyReview` are local types, not a Studio `Component.execute(CommandEnvelope, VerifiedIdentity)` implementation. `DevelopmentComponent` additionally exposes pure helper methods `propose_persona`, `assess_conduct`, `requalify`; these are **not** gateway operations. Unknown operations are `UNSUPPORTED`. The facade has no `StateStore`, SQL connection, secret, network transport, writer, or launch handler.

### Port boundary

- `EvidencePort.verify(EvidenceRef) -> VerificationReceipt` for conduct artifacts and `verify_sample(CompetencySample) -> VerificationReceipt` for independent competency-artifact and metric observations: read-only observation of artifact SHA, matching source SHA when asserted, typed rubric measurements, and receipt reference. A missing/erroring provider is `UNSUPPORTED`; never accept worker text claiming `VERIFIED` as cryptographic authority. Local synthetic fixtures test deterministic behavior only.
- `PersonaRegistryPort.read_current(worker_id) -> PersonaSnapshot | None`: read-only authoritative snapshot. A missing or stale snapshot denies proposal. Must be wired to the current Studio AG registry after schema reconciliation.
- No direct AK, AL, AG, AJ or AM imports are present. P2 controls authenticated boundary joins, private case custody and policy.

### Denials and invariants

- Fail closed on absent/untrusted verification, low sample size, insufficient cohort comparability, bad observed hash, duplicate evidence/sample, insufficient dimension coverage, invalid epoch, stale persona revision or replayed requalification.
- Require traceable environmental attribution and keep such observations out of worker reliability scoring.
- Do not synthesize a signature, credential, owner decision, HR finding, confidential case, or production effect.
- Persona deltas cannot add capabilities/roles/launch permissions, alter tests or relax required QA.
- Ordinary construction, query, review, coaching and draft persona calls make **no external writes**. The tests use an adversarial unused external writer port to confirm the facade ignores it.
- No game-domain knowledge or engines are imported; game expertise stays in independently scoped skills/repositories.

## Local test invocation

From the **root of the extracted WG-AN source archive**:

```sh
PYTHONPATH=. python -m unittest discover -s tests/oversight_development -p 'test_*.py' -v
```

The `test_*.py` suite exercises comparable evidence, six mandatory rubric dimensions, insufficient samples, host/source attribution, wrong SHA, provider fault, deterministic digest/replay, coaching bounds, new evidence requalification, stale epoch, permission-widening denials, no registry write, conduct routing, no game imports, and no implicit external write. See `test_execution.log` for the exact local execution outcome.

## Source authority and P2 integration requests

Reference authority was bundled nonsecret HR V12/V13; exact pinned hashes are in `WG_AN_MANIFEST.json` accompanying this source archive. The included frozen Studio A–S baseline ZIP was SHA-256 verified separately but is *read-only* and is not an Oversight private baseline.

1. **P2-AN-01** (AK Oversight secure source): independently authenticate the current private Oversight checkout and SHA, inspect canonical case custody and signed enforcement verifier; align typed recommendation format and source audit provenance. This P1 package never signs or writes private case data.
2. **P2-AN-02** (AG Studio persona registry): bind `read_current` to its authoritative version/digest projection under a trusted gateway; reconcile allowed style key names/types and owner approval workflow. Do not let this module patch AG profile objects directly.
3. **P2-AN-03** (AJ/AL/AK evidence and bridge): bind independent authenticated observation of exact artifact/source hashes and minimal operational worker identity/epoch; distinguish provider outages from worker faults. Preserve Oversight-to-Studio signed minimal notice constraints and no plaintext case projection.
4. **P2-AN-04** (Oversight/Studio gateway & owner): add requested operation registration/authorization only under separate owner-ratified governance; map result DTOs to the frozen `NOMAD/CMD/1`/`NOMAD/QUERY/1` envelopes and actual Oversight auth. Reject all unauthorized personnel side effects.
5. **P2-AN-05** (P3/P4 QA): independently run private repo integration, real provider failure/replay checks, source admission negative tests, signed notice/custody tests and live host tests before release claims.

External gates: actual private repository source integration, canonical signed notice verification, protected confidential custody, authenticated owner approval, Studio AG registry live readback, real trusted artifact verification, actual gateway admission, external provider negative probes and production rollout are all **NOT_RUN**. `PRIVATE_REPO_INTEGRATION_NOT_VERIFIED`.
