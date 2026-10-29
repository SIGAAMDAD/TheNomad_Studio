# CAB-B runtime boundary: intake and contract

The service methods in `BriefService` and `FeatureContractCompiler` match the
R9 frozen `BriefPort` and `FeatureContractPort` signatures. `ProjectRegistry`
implements `ProjectPort` (see `nomad_studio.organization.api`).

## Required trusted injections from P2 composition

- `state.read(fn)` and `state.write(fn)` are **CAB-B-scoped** transactions.
  `fn(tx)` receives `fetch_one(sql,params)`, `fetch_all(sql,params)` and
  `execute(sql,params)`. `execute` is authorized for CAB-B's **four tables
  only**. The root keeps the durable idempotency/event/outbox writer.
- `trusted_now_us` is a trusted clock. `owner_identity` and
  `authenticated_owner` are **already verified** identity-port `Result`s.
  `Result.record['principal_id']` identifies the authenticated principal and
  `Result.receipt_ref` is a real trusted receipt, not user-entered text.
- `bible.snapshot_active_pair(project_id,feature_scope)` returns the exact
  strict `BiblePairSnapshot` record. `bible.get_clause_bundle(pair_digest,scope)`
  returns a verified record containing `project_id`, `pair_digest`,
  `design_edition_id`, `technical_edition_id`, plus `design_clauses` and
  `technical_clauses`. Each clause has the strict `BibleClause` fields plus
  **immutable clause `text` bytes**, whose UTF-8 SHA-256 equals `text_digest`.
  A CAB-D adapter can expose this representation from a verified annex readback.
- `context_port.build_bundle(project_id,brief_id,pair_digest,None)` must return
  the current authoritative context record as verified by CAB-I. Intake cannot
  read a source repository directly; context facts cannot add requirements.
- `authority.assess_bible_conflict(feature_id,decision_or_diff,pair_digest)`
  must return only verified mandatory clause IDs when a real contradiction is
  detected. Its conflicts must be scoped to this feature/deliverable.
- `identity.verify_owner_decision(proof_ref,'production.amend',subject_digest,
  project_id)` binds the exact amendment payload to owner approval. The
  `subject_digest` is SHA-256 of canonical JSON with feature ID, requested
  changes **excluding** `owner_proof_ref`, original brief digest and pair
  digest. The DDL stores the resulting immutable contract revision. P2/CAB-D
  must ensure proof nonce consumption/expiry atomically at the authority layer.
- Optional `extractor(original_text,verified_clauses)` returns candidate
  deliverables with strict `DeliverableRequirement` fields and `source_quotes`
  (exact quotes from owner brief), or `requirement_refs` (verified mandatory
  Bible clause IDs); `derived_from_deliverable_id` is allowed only for directly
  dependent build/test output. Unknown natural-language requests fail closed
  without a configured semantic extractor. P2 must supply an effective
  extractor for comprehensive natural-language production.

## Owner command routing

`studio.project.register` -> `ProjectRegistry.register_project`.
Feature-submission command -> `BriefService.admit_brief` ->
`FeatureContractCompiler.compile_contract` with the current Bible pair and
CAB-I context. `production.amend` -> `BriefService.amend_brief` -> targeted
contract revision. No external GitHub issue or intermediate department ticket
is accepted. The source ZIP's old `app.py` has **not** been edited in P1;
P2 must replace its old `organization.build_component` registration.

This worker produces no live provider proof. Local unit tests are simulated
CAB-D/CAB-I/CAB-H responses against real frozen SQLite DDL; production
semantic conflict verification, remote identity proof, cold start and full
22-port integration are P2/P3 responsibilities.
