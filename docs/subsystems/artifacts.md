# WG-K: Game Documents and Binary Artifact Contracts

Version: **NOMAD/CMD/1**, **NOMAD/QUERY/1**; frozen subsystem: `artifacts`. Owner: WG-K. This implementation is a local component, **not a deployed provider or signed attestation service**.

## Entry point and dependency seams

```python
from nomad_studio.artifacts.api import build_component
component = build_component({
    "store": single_writer_state_store,
    "authorize": live_epoch_authorization_callback,
    "folder_registry": verified_folder_id_mapping,
    "readback": trusted_read_only_provider_adapter,
    "format_tools": registered_format_validators,
    "review": review_component,
    "publication": publication_component,
})
```

`build_component(deps: Mapping[str, object]) -> Component` returns an object with `name='artifacts'`, `execute(command, identity)`, `query(query, identity)`, . **WG-A** alone initializes the single pinned V1 schema; WG-K opens no SQL connection. No network calls run within `StateStore.transact`. Every accepted internal mutation has an event with actor, correlation, subject hash and event receipt. Provider adapters must not accept worker-supplied bytes as a readback.

| Dependency | Required behavior | Unavailable behavior |
| --- | --- | --- |
| `store` (WG-A) | `StateStore.read`, `transact`, `StateTx.fetch_*`, `execute`, `append_event`; the Studio V1 initial schema includes artifact tables | `BLOCKED/NOT_CONFIGURED` |
| `authorize(identity, scope, artifact_id)` (WG-B) | Trusted live-epoch/scope authorization, returning **literal `True`** only when authorized | `BLOCKED/NOT_CONFIGURED`; errors `UNKNOWN` |
| `folder_registry` (WG-J/WG-S owner-controlled data) | Mapping `document:<domain>` and `asset:<kind>` to exact, approved Drive **parent IDs**; never infer folder IDs | `BLOCKED/NOT_CONFIGURED` on unknown Drive folder |
| `readback.describe(manifest)` | Provider read-only metadata: `provider`, `object_id`, `revision`, `parent_id`, `size`, optional `sha256`. Source must be independent of author declaration and exact-revision bound | `BLOCKED/NOT_CONFIGURED` or `UNKNOWN/PROVIDER_UNKNOWN` |
| `readback.read_exact(manifest)` | Actual provider bytes (`bytes` or iterable of `bytes`), immutable revision binding; independent second metadata read detects races | `UNKNOWN/PROVIDER_UNKNOWN`; hash/size mismatch `CONFLICT/SOURCE_SHA_MISMATCH` |
| `format_tools` | Mapping `kind` or `.extension` to trusted validation callable `(actual_bytes, typed_manifest) -> {'valid': True, 'tool_version': '<exact-version>'}`; tool must genuinely inspect bytes | `NOT_RUN/BLOCKED_MISSING_TOOL`; invalid structure `DENIED/INVALID_FORMAT` |
| `review` (WG-I) | `Component.execute(review.request_qa)` and `Component.query(review.verdict)` with independent SHA-bound verdict + review identity | Attachment intent retained; `NOT_RUN/NOT_CONFIGURED`; no publication |
| `publication` (WG-J) | `Component.execute(publication.prepare)` only, with QA receipt, exact bytes hash and expected revision | `NOT_RUN/NOT_CONFIGURED`; no publication |
| `now_us` | Optional trusted monotonic-enough UTC microsecond source; default `time.time_ns()//1000` | Uses default |
| `max_bytes` | Optional positive integer byte cap (default **256 MiB**) for in-memory structural/tool validators | `BLOCKED/BACKPRESSURE` for larger objects |

The integrator must bind **real connector credentials only in provider ports**, not manifests or prompt text, and reconcile sibling authorization requirements: ordinary artifact workers do not gain head/owner/QA publication rights through this component. WG-K cannot mint a trusted identity. `review` and `publication` calls use the inbound `VerifiedIdentity` and may reject scopes; alternate service delegation requires an independently authorized integration policy, not a forged identity.

## Closed payloads

Commands require the frozen envelope: `version='NOMAD/CMD/1'`, `domain='artifacts'`, `request_id`, `idempotency_key`, operation `<domain>.<verb>`, and trusted gateway `VerifiedIdentity`. The `actor_claim`, worker ID and source SHA in the envelope are *not* authentication evidence. The identity must be unexpired, carry the operation scope, and pass `authorize` (including epoch and resource checks). Replaying an idempotency key with different operation/body/principal/job/epoch is `CONFLICT/IDEMPOTENCY_CONFLICT`.

| Operation | Strict input payload | Success result and guarantees |
| --- | --- | --- |
| `execute(artifacts.register)` | `{"manifest": <ArtifactManifest>}` | `ACCEPTED/REGISTERED`, immutable manifest digest, not yet byte-verified |
| `execute(artifacts.verify_bytes)` | `{"artifact_id": string}` | `ACCEPTED/BYTES_VERIFIED` and SHA-256/size from independent provider readback; no format approval |
| `execute(artifacts.validate_format)` | `{"artifact_id": string}` | `ACCEPTED/FORMAT_VALIDATED` only after byte proof, second fresh readback, structural/tool validation |
| `execute(artifacts.attach)` | `{"artifact_id": string, "job_id": string}`; `command.job_id` must match | Durable unique `(artifact_id,job_id)` attachment, SHA-bound QA request intent, then `review.request_qa` *outside* transaction. Missing review = `NOT_RUN` with saved local intent |
| `execute(artifacts.proof)` | `{"artifact_id": string, "job_id": string}`; `command.job_id` must match | Only after `review.verdict` returns independent `PASS` matching exact SHA, job, artifact and QA request: `publication.prepare` intent, never `dispatch`/`merge`. Result `PUBLICATION_INTENT_PREPARED` means **not published** |
| `query(artifacts.proof)` | `{"artifact_id": string}` | Historical manifest/verification evidence and bounded attachment receipts, `qa_verdict='NOT_ASSERTED'`; no claim of current provider status or independent QA PASS |

Required scopes: `artifacts:register`, `artifacts:verify`, `artifacts:attach`, `artifacts:publish_intent`, and `artifacts:read` (query). These are WG-K-local scope strings; WG-B must enforce live authorization separately. All input payloads reject unrecognized keys, including worker-written `validation_passed`, `file_bytes`, `qa_pass` or arbitrary receipts.

### ArtifactManifest (required and optional)

```json
{
  "artifact_id": "terrain-cliff-v003",
  "kind": "mesh",
  "provider": "drive",
  "object_id": "actual-provider-file-id",
  "revision": "actual-provider-revision",
  "parent_id": "verified-mesh-folder-id",
  "filename": "terrain-cliff.glb",
  "size": 182432,
  "sha256": "64-lowercase-hexadecimal-digest-of-exact-bytes",
  "mime": "model/gltf-binary",
  "license": "SPDX:CC0-1.0",
  "origin": "artist-generated:approved-source-reference"
}
```

Schema is a **closed object**. Required: `artifact_id`, `kind`, `provider`, `object_id`, `revision`, `filename`, positive `size`, lowercase `sha256`, `mime`, `license`, `origin`. `parent_id` is mandatory for Drive and forbidden for GitHub/LFS. For document kind, `document_domain` is mandatory; for FMOD kinds `fmod_contract` is mandatory. No field in manifest is considered independently true before verification. Asset revisions require a **new `artifact_id`**: replacing registered manifest content is a conflict. `source_sha256` in publication intent is the verified byte digest, not a Git commit SHA.

`kind`: `mesh`, `audio`, `fmod_bank`, `fmod_project`, `animation`, `image`, `vfx`, `ui`, `build`, `document`; `provider`: `drive`, `github`, `lfs`. MIME is checked against kind + extension. Supported formats: `.glb`, `.gltf`, `.obj`, `.fbx`, `.wav`, `.ogg`, `.flac`, `.bank`, `.fspro`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.exr`, `.tscn`, `.tres`, `.svg`, `.zip`, `.pck`, `.md`, `.txt`, `.json`, `.pdf`, `.docx`. All paths/IDs must be bounded; GitHub locator cannot traverse parent components.

Drive document parents are looked up under `document:design`, `document:narrative`, `document:gameplay`, `document:environment`, `document:audio`, `document:fmod`, `document:art`, `document:animation`, `document:ui`, `document:vfx`, `document:qa`, `document:production`, `document:engineering`, `document:publishing`. Drive asset parents use `asset:<kind>`. **Folder ID values are deployment-specific configuration**, not hardcoded; a generic dump or migration directory fails parent scope verification. `revision` must equal actual provider-observed content revision at verification, attach, and publication preparation. GitHub/LFS are readbacked exact locators; WG-F/WG-J own provider adapters and any stronger Git/ref handling.

`fmod_contract` is a closed object with `bank_name` for `.bank`, `project_name` for `.fspro`, required nonempty `events` entries `{path: "event:/...", guid?: string}`, `parameters` (typed `continuous`, `discrete`, `labeled` with bounded range/default or labels/default), and `audio_review_required: true`. This captures **intended** FMOD event/bank/parameter bindings; it is **not** evidence that a bank/project exists, links, plays, or was independently listened to. The actual FMOD tooling and independent audio QA are mandatory external gates.

### Validation matrix

| Format | Built-in checks | External semantic validator |
| --- | --- | --- |
| PNG | Signature, bounded chunk structure, per-chunk CRC, required IHDR/IDAT/IEND | Optional beyond structural acceptance |
| PCM WAV | RIFF decoding through standard `wave`, channels/rate/samples | Optional beyond structural acceptance |
| Markdown, plain text, JSON | Nonempty UTF-8; JSON parse | Optional |
| ZIP, DOCX | Safe archive paths/CRC; DOCX core entries | Additional project/content QA may be needed |
| GLB/glTF | GLB v2 binary boundaries, JSON asset header, optional BIN; glTF v2 JSON | **Required** mesh/animation semantics |
| OGG, FLAC, JPEG, WebP, PDF, SVG, Godot scenes | Signature/text check when implemented | **Required** format-specific registered tool |
| FMOD bank/project, FBX/OBJ, EXR, PCK, VFX, build output | Basic expected extension/MIME | **Required** appropriate registered tool |

A file of Markdown prose named `*.glb`, `*.wav`, or `*.bank` cannot pass by declaring it to be an asset. The tool port must run real validators; no built-in FMOD bank validator is fabricated. Missing validator results in `NOT_RUN`, not `PASS`. Tool identity/version is recorded, but real tool trust/sandboxing is a deployment responsibility.

## Durable SQLite schema and events

Artifact tables are part of the pinned Studio V1 initial schema. All tables are module namespaced:

- `artifacts_manifests(artifact_id PRIMARY KEY, manifest_json, manifest_sha256, state, producer_principal, byte_proof_sha256, validator, evidence_json, created_us, verified_us, validated_us)` with `REGISTERED -> VERIFIED -> VALIDATED`.
- `artifacts_requests(idempotency_key PRIMARY KEY, operation, input_sha256, outcome_json)`; successful internal transitions retain result, digest, request ownership.
- `artifacts_attachments(attachment_id PRIMARY KEY, artifact_id FK, job_id, actor_principal, subject_sha256, qa_intent_json, publication_intent_json, qa_receipt_id, publication_receipt_id, created_us)`, with unique `(artifact_id,job_id)`.

Events: `REGISTERED`, `BYTES_VERIFIED`, `FORMAT_VALIDATED`, `ATTACHED_QA_PENDING`, `QA_REQUESTED`, `PUBLICATION_INTENT_PREPARED`; all via StateTx append_event. Attachment request/prepare ports use deterministic remote idempotency keys derived from the attachment ID. An ambiguous request is retried/reconciled at the sibling port using its own durable idempotency policy; WG-K does not treat a timeout as success. This component neither dispatches nor marks provider publication `VERIFIED`.

## Error and recovery contract

- Missing/extra closed fields: `DENIED/INVALID_SCHEMA`; wrong envelope: `DENIED/MALFORMED_REQUEST`.
- Missing/invalid scope: `DENIED/INVALID_SCOPE`; expired identity `DENIED/EXPIRED_GRANT`; authorizer unavailable `BLOCKED/NOT_CONFIGURED`.
- Wrong registered Drive parent: `DENIED/INVALID_SCOPE`; missing folder ID: `BLOCKED/NOT_CONFIGURED`.
- Actual bytes are zero, size/hash differs: `CONFLICT/SOURCE_SHA_MISMATCH` (zero **declared** size: `INVALID_SCHEMA`).
- Provider metadata revision/parent/locator mismatches: `CONFLICT/DOCUMENT_CONFLICT`; metadata or provider outage: `UNKNOWN/PROVIDER_UNKNOWN`.
- Missing format tool: `NOT_RUN/BLOCKED_MISSING_TOOL`; crashing registered tool `NOT_RUN/TOOL_FAILED`; invalid bytes/tool evidence: `DENIED/INVALID_FORMAT`.
- Unreviewed source or changed QA subject: `BLOCKED/QA_PENDING` or `CONFLICT/DOCUMENT_CONFLICT`; reviewer identical to author: `DENIED/SELF_REVIEW`.
- Outbound review/publication unavailable: `NOT_RUN/NOT_CONFIGURED`; unknown effect: `UNKNOWN/PROVIDER_UNKNOWN`; no success inferred from ambiguous calls.
- The SHA-256 is always computed from **actual provider-returned bytes** and checked after metadata readback. The provider must enforce immutable revision reads; a second metadata read catches common races. WG-J must provide conditional publication, readback reconciliation, and provider quota handling.

## Known limits / external acceptance NOT_RUN

This bundle has no real authenticated Google Drive/GitHub/LFS reads, FMOD Studio instance or validator, mesh semantic validator, independent QA personnel, external publisher or live ChatGPT connector. Such checks require deployment and remain **NOT_RUN**. Real local test fixtures and mocks demonstrate the WG-K state machine only. There is no claim of 500/800-chat execution, actual web-chat capability, production publication, studio-wide integration, canonical game document placement, or real audio listening review.

The default in-memory byte validation cap is 256 MiB per asset and can be configured; oversized builds/banks need a trusted streaming/sandbox validator adapter and larger enforced quota at integration. MIME is extension-based plus structural signatures, not MIME from a network header alone. Licensing and origin are mandatory **claims** and require separate provenance review. Historical `query(artifacts.proof)` exposes last verification evidence, not live freshness; `attach` and publication preparation do fresh readback. Folder mapping, epoch grants, real cloud revisions and format tools must be configured by the integrator before production.
