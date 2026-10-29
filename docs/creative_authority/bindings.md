# WG-AV — Project-neutral artifact and property binding ports (R10 P1)

**Owner:** WG-AV, scopes S142, S143, S144. **Facade:**
`nomad_studio.creative_authority.bindings.build_component(deps)`;
`BindingService.inspect(constraint, candidate) -> BindingVerdict`.
**Local source compatibility target:** `NOMAD/CREATIVE/BINDINGS/1`, **not admitted**
for live gateway or production until P2 integration.

## Scope and design

A `BindingRegistry` holds an immutable and content-addressed *snapshot* of
externally registered `ArtifactBinding` descriptors. A descriptor maps stable
owner `node_id` and opaque `binding_id` to an exact artifact URI, artifact
revision, SHA-256, opaque `format_id`, optional `PropertyKey(key, value_type)`,
required static inspection capability and optional runtime capability.
Multiple distinct artifacts may back one node. Duplicate identifiers and
ambiguous property selectors for the same artifact are rejected. This module
has **no facility to approve node creation, change a policy, write a resource,
load an artifact URI, register a gateway operation, or publish a build.**

`OwnerConstraint` references an upstream decision, exact policy revision/digest,
**owner-pinned registry digest** (prevents rebinding an old approval to a new
property or source), mode, `PIN_EXACT`/`QUALITATIVE` decision, expected typed
scalar, required runtime proof flag, acceptance-test IDs, and downstream gate.
`CandidateSnapshot` pins current source revision/hash, current policy, and
binding-registry digest. These are input claims requiring authentication by
other components. A digest match is only a consistency check, never identity
or ownership proof.

The only supported exact property values are finite JSON scalars of type
`STRING`, `ENUM`, `INTEGER`, `NUMBER`, `BOOLEAN`, or `HASH`. `QUALITATIVE`
remains human creative judgment, never a machine-generated pass. New formats
and structured properties are supported through separately reviewed typed
parser/inspector ports, not invented parsers in Studio Python.

## Dependency-injected contracts

```python
from nomad_studio.creative_authority.bindings import (
    ArtifactBinding, BindingRegistry, PropertyKey, OwnerConstraint,
    CandidateSnapshot, DeterministicMockInspector, build_component)

# For local tests only. No external I/O occurs.
artifact = ArtifactBinding(
    binding_id="bind-7", node_id="owner:property-7", format_id="data-table/3",
    artifact_uri="repo://example/config/one", artifact_revision="rev-3",
    source_sha256="a" * 64, static_capability_id="parser.table.v1",
    property_key=PropertyKey("limits.width", "INTEGER"))
registry = BindingRegistry([artifact], snapshot_revision=3)
rule = OwnerConstraint("owner-decision-ref", artifact.node_id,
    artifact.binding_id, 7, "b" * 64, registry.digest,
    "LOCKED", "PIN_EXACT", 40)
candidate = CandidateSnapshot(artifact.artifact_uri, artifact.artifact_revision,
    artifact.source_sha256, 7, rule.policy_digest, registry.digest)
values = {(artifact.artifact_uri, artifact.artifact_revision, artifact.source_sha256,
           artifact.property_key.key, "STATIC"): 41}
mock = DeterministicMockInspector(values)
service = build_component({"registry": registry,
    "inspectors": {"parser.table.v1": mock}})
verdict = service.inspect(rule, candidate)
assert verdict.code == "PINNED_PROPERTY_MUTATED"
assert verdict.gate == "BLOCK_RELEASE"
```

`InspectorPort.inspect(InspectionRequest) -> InspectionObservation` is an
extension seam. Requests are deterministically identified by a digest of
binding, source, revision, property, capability, kind and policy digest.
Observations identify their matching request, exact artifact URI/revision/SHA,
capability, inspection kind, result, observation ID, evidence SHA and test IDs.
The facade rejects stale responses, mismatched provenance, duplicate
observation evidence, type errors and non-observations. It does not accept an
inspector's assertion of `OBSERVED` as independently verified proof of an
owner, private signing authority or a live runtime. `evidence_origin` is
untrusted metadata; **P2 must independently validate evidence bytes, issuer,
source provenance and provider trust.** The local deterministic mock is only
a fixture. `UnsupportedInspector` returns `NOT_TESTED`, never success.

No static file, scene, parser, engine bridge, live host, GitHub, Drive, SQL,
FMOD or game-domain code is imported or executed. A provider can support a
new format by implementing the port and registering its declared capability
upstream without changing this kernel. This is demonstrated with independent
`data-table/3` and `vector-doc/2` mock fixtures.

## Results and release boundaries

| Case | Classification | Local gate/code |
|---|---|---|
| Static exact value observed, no runtime required | `STATIC_ONLY` | `PENDING_EXTERNAL_GATES / LOCAL_CONFORMANCE_ONLY` |
| Static and required runtime probe observed | `INSTRUMENTED` | `PENDING_EXTERNAL_GATES / LOCAL_CONFORMANCE_ONLY` |
| Locked owner exact value mutated | `STATIC_ONLY` | `BLOCK_RELEASE / PINNED_PROPERTY_MUTATED` |
| Inspector absent or required runtime absent | `UNKNOWN` or `STATIC_ONLY` | `BLOCK_RELEASE / REQUIRED_INSPECTION_NOT_TESTED` or `RUNTIME_CAPABILITY_UNSUPPORTED`; `NOT_TESTED` |
| Stale policy, registry, source or property | `UNKNOWN` | `BLOCK_RELEASE / STALE_*` |
| Unknown binding or property | `UNKNOWN` | `BLOCK_RELEASE / BLOCKED_UNMAPPED*` |
| Qualitative mood/style judgment | `HUMAN_REVIEW` | `REQUIRE_HUMAN_REVIEW / SUBJECTIVE_REVIEW_REQUIRED` |
| Explicit review-required policy | static/instrumented | `REQUIRE_HUMAN_REVIEW / EXACT_OWNER_REVIEW_REQUIRED` |
| Missing required acceptance test receipt | static/instrumented | `BLOCK_RELEASE / REQUIRED_TEST_RECEIPTS_MISSING` |

`BindingVerdict.release_authorized` is always `False`. No conformance outcome
can itself grant publication, merge, release, owner approval, or a production
QA pass. This design respects separate upstream authorization and downstream
approval, rather than claiming a local mock is independently verified.

`Provenance` traces the decision ref, policy revision/digest, owner node,
binding, registry digest, source URI/revision/hash, observation IDs, evidence
SHA-256 values, test IDs, and the downstream gate. The calling P2 service
must store authenticated decisions/receipts in the existing authoritative
persistence layer, not in this module.

## P2 integration contract and open requirements

1. **AR policy module:** supply independently authenticated effective owner
   constraints and current policy revision/digest, including exact binding
   registry digest. Do not import sibling P1 code into WG-AV.
2. **Source/asset inventory:** register versioned descriptors and candidate
   hashes only after observed source manifests/diffs and correct repository
   authority. Recompute and pin registry snapshot revision/digest on changes.
3. **External format inspectors:** provide exact static/runtime parser
   capability adapters, signed or independently verified source/test receipts,
   and explicit `NOT_TESTED` for unavailable probes. Reject forged `status`
   and `evidence_origin` strings at the trust boundary.
4. **AS/AU and publication/build gate:** consume `BindingVerdict` and
   `Provenance`, recheck policy/assignment/source epochs and owner exception
   authorization; require independent QA, human creative review and release
   authority. Deny on unresolved impact/unknown proof.
5. **Shared integration:** P2 alone decides schema version ratification,
   gateway/app wiring, StateStore event persistence, CI/build adapters and
   provider permission enforcement; this worker edits none of those.

## Verification status

- Local mock conformance and fail-closed negative tests: executed with
  `PYTHONPATH=src python -m unittest discover -s tests/creative_authority/bindings -p 'test_*.py' -v`.
- Actual external parser/runtime, authentic owner approval, policy service,
  downstream game build integration, provider permission negative tests,
  independent QA and P4 host operations: **NOT_RUN / UNSUPPORTED**.
- No private Oversight source was needed or consulted. This is a Studio
  `LOCAL_SOURCE` artifact on the pinned A–S baseline.
