# WG-AS — Generic artifact change impact and semantic inspector ports

**Scope:** S133–S135, Studio V1 R10, local `LOCAL_SOURCE` candidate. The operation name `creative.impact` is a P2 proposal; it is **not** automatically added to the gateway. This module is project-neutral. It never interprets, edits, or authors gameplay, assets, scenes, audio, engine content, or project-specific artifacts.

## Public interface

`nomad_studio.creative_authority.impact.api.build_component(deps: Mapping[str, object]) -> Component` implements the frozen A–S `Component` protocol. `execute` always returns `DENIED / READ_ONLY_COMPONENT`. `query` accepts a gateway-verified `VerifiedIdentity` with scope `creative.impact:read`, non-expired using the required injected `clock_us()` integer clock, and a `QueryEnvelope(version="NOMAD/QUERY/1", domain="creative", operation="creative.impact")`. Its `payload` is exactly:

```python
{
    "intent": {"version": "NOMAD/CREATIVE/CHANGE-INTENT/1", "intent_id": "...", ...},
    "candidate_ref": "immutable-or-CAS-bound-provider-reference",
    "expected_candidate_sha": "40-lowercase-hex-source-revision",
    "claimed_paths": ["optional/worker/claimed/path"],
}
```

`intent` follows the R10 reference `creative_change_intent_v1` fields; its `source_base_sha` pins the independently read base revision, `policy_revision` pins the independently read current policy, and `candidate_digest` pins the actual independently observed complete candidate manifest. `claimed_paths` is optional diagnostic input; when present it must equal the observed changed path set. An omitted changed path is rejected. Worker-declared nodes are *not* treated as the complete impact set.

`deps` contains `artifact_reader: ArtifactReader`, `graph_reader: GraphReader`, `inspector_registry: InspectorRegistry` (optional; absent leads to unknown semantic impacts), `verifier_principal: str` (local externally configured verifier label; not authenticated by this library), and `clock_us: Callable[[], int]`. These are typed Protocols in `ports.py`. There are no GitHub, Drive, network, SQL, ledger, or publishing imports or write methods. External implementers must independently verify provider read permissions, ownership and capability; a Protocol or `independent_readback=True` alone is not a credential/proof of independence.

An `INSPECTED` outcome contains `report`, an **unsigned** reference-receipt-shaped mapping, and explicit `signed=False`, `admitted=False`. `INSPECTED` means the isolated analysis ran, **not** a permission to publish. A missing/untrusted real provider is `UNSUPPORTED`, stale/malicious inputs are `DENIED`, and a parser gap on an otherwise valid independent diff creates an `UNKNOWN_IMPACT` report with a hold gate.

## Source readback and deterministic candidate digest

Both the base (addressed by its exact 40-hex revision) and the candidate (addressed by the `candidate_ref`) are fetched from a separately injected read-only reader. Each snapshot must be complete, bound to the requested repository/ref, accompanied by a non-empty provider evidence reference, and return the pinned exact source revision. Only `file` artifacts with actual `bytes` are supported in this local implementation; independently compute SHA-256 for every blob. A returned SHA-256 mismatch is `STALE_CONTENT_HASH`. Missing bytes, unsafe kinds, an incomplete tree, a compromised scope, and provider errors deny/return unsupported.

The candidate digest is `sha256(canonical_json({"repository": repo, "source_sha": candidate_sha, "files": [{"path": p, "kind": "file", "sha256": blob_sha256}, ...] }))`, with files lexicographically sorted and JSON encoded using sorted keys, no whitespace, ASCII escaping, and no NaN. This is the engine's **local candidate manifest digest**, not a Git object ID and not an authenticated owner acceptance. A trusted provider adapter must attest that its immutable revision and bytes are linked to the actual source/Drive content revision. The proposed intent uses a 40-hex source revision per the R10 reference; non-Git resources need an explicit P2 versioned mapping/CAS adapter before live use. The actual provider does not exist in WG-AS.

`path()` refuses absolute, traversal, empty/dot segments, backslashes, percent encoding, control characters, non-NFC Unicode and drive colon syntax. Path case-fold collisions are rejected rather than silently resolved. Symlink and submodule kinds fail closed rather than allowing traversal. Rename detection pairs unique same-content deleted/added paths only for annotation; old and new paths both remain impacted. Deletion is visible. A changed path never disappears merely because the worker's declared list omitted it.

## Graph and semantic processing

The current injected `GraphSnapshot` contains approved nodes, exact resource/path bindings, parent hierarchy, tag membership, explicit directed dependency edges, required QA scenarios and current owner rules. The source has no hardcoded project graph or project schemas. Touched nodes are the union of bindings for all observed changed paths **plus** parser-reported bound/registered nodes; transitive downstream dependencies are followed to a fixed point. Transitive nodes receive required edge scenarios plus `revalidate:<node_id>` QA requests. Cycles terminate via a visited set. Rule inheritance follows owner-approved parent and tag policies. `LOCKED` blocks even when a child delegates; `REVIEW_REQUIRED` holds; `BOUNDED` requires explicit trusted parser validation for each bounded rule; contradictory typed ranges/enumerations deny. No rule means default owner review. Qualitative nodes require subjective review; unprobed runtime nodes require a runtime probe. No silently invented numeric values, authorizations, overrides, or trusted scenario results.

`SemanticInspector.inspect(path, before, after, change_kind, bound_node_ids)` is an **optional integrator-registered** format adapter. It returns the nodes it checked, affected node IDs, bounded rule validations, independent evidence ref, missing probes and QA tests. Unknown format, missing parser or parser error returns `UNKNOWN_IMPACT` (or an unsupported port failure). A parser with `COMPLETE` coverage plus verified mapped nodes and no incompatible rules permits `COMPLETE_TYPED`, whose gate is only `ELIGIBLE_FOR_FURTHER_GATES`. If the parser is partial, a rule requires review, or the impact is qualitative, the gate is `HOLD_REVIEW`. Explicit locked/contradictory rules return `CONFLICT / DENY` except where unknown/unmapped paths require the stronger uncertainty disclosure (`UNKNOWN_IMPACT / HOLD_REVIEW`); the integrator must still deny promotion for that report. No owner exception execution exists in WG-AS.

## Integration contract and limitations

P2 must verify the current canonical policy registry, bind trusted current revision/digest, bind real scoped provider readback, validate the proposed schema against gateway source and trust contracts, register and authenticate semantic inspectors, and decide whether `creative.impact` may be query-admitted. No WG-AS code writes the shared gateway, policy registry, state schema, CI, source service, or provider connections. If adopting real Drive, binary or content services, provide immutable content/revision readback and correct path/object binding; initial unsupported objects and formats remain UNKNOWN or UNSUPPORTED, not 'complete'. P3 should independently rerun adversarial tests and source/provider-side negative authorization. P4 requires real host/provider evidence and separately authorized deployment.

The `tests/creative_authority/impact/` suite is deterministic and hermetic: source/graph/parser fakes have no network capability and verify real operations, denial behavior and transitive QA. Its success is **LOCAL_MOCK_TEST_PASS**, not live provider proof or a signed owner decision.
