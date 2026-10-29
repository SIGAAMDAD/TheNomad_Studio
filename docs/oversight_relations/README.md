# WG-AO Agent Relations and Mediation — provisional private-domain source

**Status:** `PROVISIONAL_SOURCE`; `PRIVATE_REPO_INTEGRATION_NOT_VERIFIED`. This module is an independently runnable clean-room implementation of S121–S123, not a patch to authenticated private Oversight, a cryptographic custody system, a case deployment, or an admitted Studio operation.

## Operations and invariants

`build_component(deps: Mapping[str,object]) -> RelationsComponent` exposes a **candidate**, project-neutral `hr.mediation` private port. The method surface is:

| Method | Effect in candidate local store | Denial/failure invariant |
| --- | --- | --- |
| `request(actor, request_id, issue)` | Creates a private opaque-referenced referral or mediation session; idempotent by request ID + fingerprint | Requires authenticating case-access port; altered replay is `CONFLICT`; absent store `UNSUPPORTED` |
| `private_read(actor, session_id)` | Authenticated private projection; no original evidence plaintext | Revoked access `DENIED`; missing port `UNSUPPORTED` |
| `studio_query(...)` | None | Always `DENIED`, even to HR-like strings |
| `assign_mediator(actor, session_id)` | Binds independent qualified, case-authorized mediator or records blocked state | Excludes both parties, accountable head, case conflict and case role conflicts; no roster `UNSUPPORTED` |
| `participate(actor, session_id, choice)` | Appends acceptance/decline (no forced participation) | Affected party only; conflicting repeat `CONFLICT` |
| `respond(actor, session_id, evidence, kind)` | Adds opaque fact-record/counterstatement references | Affected party only; no plaintext, duplicate is `REPLAY` |
| `set_working_contract(actor, session_id, terms, followup_ref)` | Stores allowlisted mutual process commitments and time-bounded checkpoint reference | Active mediator and two acceptances required; no punitive terms |
| `verify_notice(actor, session_id, receipt_ref)` | Stores independently verified receipt, **never delivers a notice** | Missing/unverified notice provider cannot advance |
| `close(actor, session_id, resolved)` | Resolves only complete mutual agreement or records unresolved referral | No sanctions, case disposition, revocation or privileged gateway effects |
| `appeal(actor, session_id, appeal_id, evidence)` | Immutable pending external-review appeal descriptor; different qualified reviewer | Affected party only, post disposition, independent reviewer; preserve prior history |

Technical/design disagreements go to the accountable production head. A single communication clash or unsourced persona assertiveness stays out of mediation. Recurring verified impact is eligible for mediation; targeted conduct is sent to confidential HR triage; verified identity impersonation to independent incident review. Security issues go to a validated emergency gate **review** only when both an independent evidence assessment and an injected preauthorized gate validator affirm the exact case. Otherwise the route stays `SECURITY_REVIEW_PENDING`. The module cannot perform a stop or signed sanction.

No mental state, emotion, intent, diagnosis, or personality score appears in the structured model. Evidence is a content digest + private handle. `private_read` returns references and process metadata only, and only with live access at the private port. No confidential narrative, case plaintext, personnel details, or reports are emitted to normal Studio query.

## Required typed injected ports

- `store`: `RelationsStore` (`create/read/update` with atomic CAS, idempotent request-index and authenticated encrypted Oversight custody for real use). `TestOnlyMemoryStore` is **synthetic, nonencrypted and unsuitable for real case data**.
- `case_access`: `CaseAccess.permit(authenticated_principal, case_id, operation)`. The adapter, not worker text, must authenticate identity/session/capability and check current authorization every time. The clean-room code cannot establish a real authenticated principal.
- `evidence_authority`: `EvidenceAuthority.assess(IssueReport)`. Assessment must derive from independently verified source revisions and custody, never case reporter's claims. Failing/absent provider downgrades to `CLAIMED`.
- `mediator_registry`: `MediatorRegistry.candidates(case_id)` backed by current confidential case grants/qualifications/conflict declarations. Missing/broken registry denies assignment/appeal.
- `emergency_gate`: `EmergencyGate.validate(issue, assessment)` returns gate approval reference only; production trusted source must enforce fresh policy, preauthorization and crypto; no enforcement implementation here.
- `notice_authority`: `NoticeAuthority.verify_delivery(case_id, receipt_ref, parties)` for a real, trusted confidential delivery receipt; receipt verification does not sign or dispatch notices.

Store receives immutable `Session` DTOs; `revision` increments by CAS. `TestOnlyMemoryStore` uses a re-entrant lock and is injected in tests only; integrator must replace it with AK-managed confidential encrypted custody. No implicit access to GitHub, Drive, private cases, Studio SQL, host sessions, worker dispatcher or ledger. Every error reports a reason code without copying evidence content.

## Integration requests (P2; owners AK / AP / AL / G)

1. **AK private case/custody owner:** authenticate exact current private checkout SHA, reconcile private case ID schemas, atomic encrypted store + de-duplication + case access + read audit/custody, original signing/notice shapes, key lifecycle. Map `RelationsStore` / `CaseAccess` to native private authority. Reject test store for production. Never import this module into Studio public state.
2. **AP independent investigation/appeal owner:** connect pending `INCIDENT_REVIEW`, `HR_TRIAGE`, unresolved mediation and appeal descriptors to actual sealed intake/appeal ports with auditable revision, reviewer independence and no automatic findings. A candidate pending referral is **not proof of delivery or completed appeal**.
3. **AL Studio-side HR bridge / G operation gateway:** explicitly review `hr.mediation` registration and privacy-minimized readback if authorized. `studio_query()` denies all confidential cases; production head must only receive safe work-referral pointers. No cross-domain plaintext. Signed notices, current epoch, owner grant and replay checks stay in existing private/bridge authority.
4. **AK / trusted emergency gate:** bind actual preapproved deterministic safety rules and independently authenticated proof for `EMERGENCY_GATE_REVIEW`; no automatic intervention from local route decision.
5. **Confidential notice sender:** approved private channel must generate notices and independently verifiable delivery/ACK receipts. This package only checks receipt references supplied to an adapter.
6. **Owner/integrator:** choose and enforce policy-approved retention windows, checkpoint deadlines, participant rights and active confidential channel provider before deployment. This candidate stores an opaque checkpoint reference, not a running clock.

## Independent validation separation

Run from the extracted owned source root: `PYTHONPATH=. python -m unittest discover -s tests/oversight_relations -p 'test_*.py' -v`. Tests use synthetic ports and case digests. Real private checkout, encryption/signatures, owner authentication, host/provider readiness, sealed notice delivery and actual emergency gate probes remain **NOT_RUN**. Isolated suite success does not qualify live security or private deployment.

## Input specs pinned in manifest

- `REFERENCES/REFERENCE_HR_V12/02_HR_PROTOCOLS_AND_DATA_BOUNDARIES.md`
- `REFERENCES/REFERENCE_HR_V12/03_PERSONA_CONDUCT_AND_WORKFORCE_POLICY.md`
- `REFERENCES/REFERENCE_HR_V13/01_HR_ARI_COMPLETE_OPERATING_MODEL.md`
- `REFERENCES/REFERENCE_HR_V13/02_HR_LIFECYCLE_CASES_AND_APPEALS.md`
- `REFERENCES/REFERENCE_HR_V13/06_ENFORCEMENT_GATES_AND_FAILURE_POLICY.md`
- `REFERENCES/REFERENCE_HR_V13/07_CROSS_DOMAIN_CONTRACTS_AND_PATCH_MAP.md`
- `REFERENCES/REFERENCE_HR_V13/SCHEMAS/hr_case_event_v1.json`
- `REFERENCES/REFERENCE_HR_V13/SCHEMAS/hr_recommendation_v1.json`

These are nonsecret reference specifications, not proof of private repository provenance.
