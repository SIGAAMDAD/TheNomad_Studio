# WG-AR — Creative Authority Policy Registry and Inheritance

**Authority:** R10 single-wave P1, S130/S131/S132; Studio baseline `43d6bc111a88287cacbedbed83d1be2b301dad0a095bc30065d3b5909aee4178` (SHA-256). **Status:** locally implemented source with deterministic tests; no production owner-verifier or provider integration evidence. The original A–S modules and their contracts remain byte-identical.

## Owned API and exact operation contract

`nomad_studio.creative_authority.policy.api.build_component(deps: Mapping[str, object]) -> Component` implements frozen `CommandEnvelope`, `QueryEnvelope`, `VerifiedIdentity`, `Outcome` contracts from `nomad_studio.contracts.v1`. `creative.register_node` and `creative.set_rule` are commands; `creative.effective_policy` is read-only. No operation is enrolled in the central gateway by this submission. The `creative_policy_*` tables are part of the single pinned Studio V1 initial schema; there is no per-module migration API.

`build_component` dependency ports:

| Key | Expected dependency | Behavior if unavailable |
|---|---|---|
| `store` | Existing `StateStore` with `read(callback)` and `transact(callback)`; tables from `schema_v1.sql` | `BLOCKED/UNSUPPORTED`, or `UNKNOWN/STORE_OR_PROVIDER_UNKNOWN` if unexpectedly failing |
| `owner_decision_verifier` | Independently configured provider exposing `verify_policy_mutation(MutationIntent, VerifiedIdentity) -> VerifiedOwnerDecision` | `BLOCKED/UNSUPPORTED` (absent); `UNKNOWN/OWNER_VERIFICATION_UNAVAILABLE` (fault) |
| `authority_epoch` | Authoritative callable returning current integer authority epoch | `UNKNOWN/AUTHORITY_UNSUPPORTED` |
| `project_namespaces` | `Mapping[str, Namespace]`, project catalog pinned and configured by integrator | `BLOCKED/UNSUPPORTED` or `BLOCKED/BLOCKED_UNMAPPED` |
| `clock_us` | Injectable integer microsecond clock | No fixed production clock; default `time.time_ns() // 1000` |

The verifier port **must independently authenticate** the owner, issuer, current revocation state, decision operation and exact requested effect. A typed `VerifiedOwnerDecision` returned by a deterministic unit-test fake does **not** demonstrate a production signature. WG-AR requires decision IDs, principal, operation, revision, current authority epoch, request SHA-256, issuer, proof reference and validity window to match exactly, then atomically consumes the unique decision ID. Input `verified: true`, a forged identity string, a chat instruction or a client-owned `owner_decision_ref` alone grants nothing.

## Namespace and node registration

A trusted integration root passes registered project namespaces as `Namespace(namespace_id, uri_prefix, source_registry_digest, owner_source_ref)`. No project, game title, engine, world object or resource identifier is built in. Example **synthetic** namespace: `studio://sample-project`. The namespace must be pinned to a separate trusted catalog (integration task); overlapping namespaces are rejected. The owner-authorized node document follows the reference `NOMAD/CREATIVE/NODE/1` shape: stable `node_id`, kind, parent, revision, binding refs, testability, governance status and optional source manifest SHA-256. An approved root must equal the configured project prefix; children must have an existing parent of the same namespace and a descendant resource path. Duplicate exact repo+resource bindings are rejected; unknown bindings are blocked. The stored node row retains principal and owner decision provenance, while every mutation writes an immutable historical revision record.

A property/asset/component is eligible for resolved policy only when it is `APPROVED`, is not `UNMAPPED`, has at least one exact typed binding, and has an exact source-manifest SHA-256. Qualitative reference nodes are review-only even under a delegated rule. The external source adapter/semantic parser must establish **actual** binding validity before enforcement; this module cannot validate a code AST, art file or integrated build.

## Register and rule mutations

`creative.register_node` command payload:

```json
{
  "expected_revision": 0,
  "policy_digest": "<current 64-character snapshot digest>",
  "owner_decision_ref": "verified-owner-decision-id",
  "node": {"version": "NOMAD/CREATIVE/NODE/1", "node_id": "studio://sample-project", "kind": "project", "testability": "TYPED_STATIC", "parent_id": null, "binding_refs": [], "revision": 1, "governance_status": "APPROVED"}
}
```

`creative.set_rule` has the same three CAS/proof fields plus `rule` in `NOMAD/CREATIVE/POLICY/1` shape. `rule.policy_revision` must be `expected_revision + 1`, its `policy_digest` must equal the current snapshot, and its `owner_decision_ref` must equal the request proof ID. The rule has a permanent `rule_id`, immutable `node_id`, mode (`LOCKED`, `REVIEW_REQUIRED`, `BOUNDED`, `DELEGATED`), impact class and typed condition. Updating an existing locked rule, even to unlock it, requires a **fresh** valid owner decision for its exact new payload. A reused owner decision is denied, including if the request ID changes. An old snapshot CAS is denied and a mid-verification mutation is fenced transactionally.

Revisions are global and monotonic per configured store. `creative_policy_head` holds the exact current revision and content digest; `creative_policy_history` records every prior digest, resulting digest, full authored document and decision provenance. `snapshot_digest(rev, namespaces, nodes, rules)` is deterministic canonical SHA-256 over revision, pinned namespace catalog, nodes and rules. Reading a corrupt or reconfigured registry fails with `STATE_INTEGRITY_FAILURE` rather than silently trusting a new digest. State is persisted through the original Studio single-writer `StateStore`, not a new service or side database. A successful mutation returns `ACCEPTED/DESIGN_POLICY_RECORDED`, **not** an artifact or build-approval receipt.

## Effective rule algorithm

For `creative.effective_policy`, supply **either** `node_id` **or** an exact `binding` object containing `repo_id` and `target_ref`. Optional `impact_class`, paired `expected_revision`/`expected_policy_digest`, and optional `assignment_epoch` specify a bounded query. Unknown, multiply mapped or unapproved targets fail closed; stale pins return `CONFLICT/STALE_POLICY`. A missing active rule returns `BLOCKED/REVIEW_REQUIRED` rather than implied delegation.

1. Build the complete, verified parent chain from target to registered namespace root; a missing parent/cycle is blocked.
2. Collect applicable impact rules along that chain, ignoring rules whose explicit `effective_from_epoch` exceeds the caller-supplied epoch. If a future epoch is present but no epoch supplied, block with `EPOCH_UNVERIFIED`.
3. Resolve the strongest applicable mode in order `LOCKED > REVIEW_REQUIRED > BOUNDED > DELEGATED`. Descendant delegation never defeats an ancestor lock.
4. Intersect **all** active bounded constraints: typed numeric minima/maxima, exact values, enum membership and units. No implicit unit conversion, type coercion, string-to-number parsing or arbitrary priority. Empty set/range, incompatible units/types or contradictory exact values -> `BLOCKED/CONFLICT_REQUIRES_OWNER`.
5. Return the ordered rule IDs, ancestry, effective bounds, current revision and policy digest, source manifest hash, and independent `effective_digest` that downstream admission/claim/prepare/merge/release gates can pin. `candidate_constraint_gate=NOT_RUN` and `protected_publication_allowed=false` explicitly record that no candidate diff, source readback, acceptance probe or release gate ran.

Policy resolution identifies constraints; it does not prove that a proposed artifact falls within those constraints. AS/AV/AU and the original source/review components own that independent enforcement. No change intent, special exception, tag-membership mutation, build readback or protected publish operation is implemented or implied by this module.

## Local checks and security properties

`PYTHONPATH=src python -m unittest discover -s tests/creative_authority/policy -p 'test_*.py' -v` exercises the SQL fixture and deterministic owner-verifier mock. Tests cover parent lock precedence; bounded range and enum conflict; exact CAS and immutable digest; owner-only unlock; decision replay; credential epoch revocation; unknown node/binding; duplicate bindings; subjective review; missing real port; malformed constraints; provider outages; tampering; future-epoch fencing; and injected concurrent policy change.

Local test fixtures contain no real credentials or project data. Nothing invokes network providers, GitHub/Drive, editor/engine runtime, private Oversight, a game repository or any external ledger. Tests do not modify original source or root app. No game-specific runtime features are part of this package.

## Integration requests (P2 owner only)

1. Verify the complete pinned Studio V1 initial schema and enforce the StateStore's domain ownership. Enroll `creative.register_node`, `creative.set_rule`, `creative.effective_policy` only after real gateway authorization policy and source ownership checks are installed.
2. Provide the real independently authenticated owner-decision verifier port. Pin trust anchors, issuer, principal, revocation, one-use decision semantics, request digest and policy CAS. Validate producer/consumer canonicalization and reject lowered-privilege direct provider writes by a separate negative test.
3. Source the namespace definitions from a genuinely registered project catalog; authenticate and pin catalog revision and source binding provenance. AV supplies actual parser/resource bindings. Do not assume synthetic namespace registration is real project approval.
4. Define pinned policy snapshot/ancestor/effective-digest DTO integration with AS/AT/AU; ensure downstream gates re-resolve against the latest revision and exact job/assignment/source/candidate digest, not a cached inherited permission.
5. Owner-scoped exceptions and cross-cutting tag membership require separate authenticated authority events/schemas and independent P2 acceptance before use. **No implicit bypass** exists in AR's resolver.
6. P3 must independently run the seven `V13-AR-01..07` acceptance cases against the merged service. P4 production owner identity, protected provider permissions, candidate constraint inspection and actual release checks remain `NOT_RUN`.
