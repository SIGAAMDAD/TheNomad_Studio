# WG-G — GitHub / Drive / CI / Artifact Integration Handoff

## Frozen contract map

WG-G owns `src/studio/integrations/**`, `tooling/connectors/**`, `tests/unit/g/**` only. These modules implement the G-owned business service surface and do not register HTTP routes, mutate APSW directly, launch browser chats, create Git branches, or invoke model inference APIs.

| Frozen operation | Code entry | Response fields |
|---|---|---|
| `listArtifacts` | `GOperatorPorts.list_artifacts(project_id=...)` | `artifacts: [{artifact_id,kind,content_digest}]` |
| `getArtifact` | `GOperatorPorts.get_artifact(project_id=...,artifact_id=...)` | `artifact_id,content_digest,kind` |
| `attachConnector` | `GOperatorPorts.attach_connector(...request_id,provider_key,credential_ref)` | `account_id,state` |
| `runBuild` | `GOperatorPorts.run_build(...request_id,source_revision_id,build_target)` | `ci_id,state` |
| `reconcileEffect` | `GOperatorPorts.reconcile_effect(...request_id,effect_id,readback,expected_state)` | `effect_id,state` |
| `observeEffect` | `GOperatorPorts.observe_effect(...effect_id,readback)` | `effect_id,state` |

Additional G-owned provider adapters: `GitHub` immutable relay readback/publishing, `GitHubSource` source commit/file readback, `Drive` immutable project-folder byte upload/reconciliation, `GitHubActions` pinned workflow dispatch/CI observations, `ArtifactStore` immutable local CAS and metadata, `PackageVerifier` resource hash checks, `RetryPolicy` deterministic poll backoff.

## Provider configuration and transport

- J/A must supply authenticated, project-authorized `ProjectBinding`; account/token identity must be resolved from trusted local configuration, **never from a browser chat envelope**.
- Github relay configuration: existing repository owner/name, existing branch ref, strictly `.nomad/relays/<project_id>`. Separate existing GitHub source repo/ref can be configured for `GitHubSource` and CI. No branch-create, force-push, or inference worker APIs exist.
- G's `GitHub.publish` requires a C-recorded durable effect intent before outbound PUT. It is immutable create-only. It reads back bytes at the GitHub **commit SHA returned by the provider** and checks exact canonical envelope/path/hash. Timeout is reconciled by the same path and digest, with `UNKNOWN` retained if remote publication is not proven.
- G's `GitHub.scan_since` consumes `verified_ref_sha`, emits a bounded page with `next_offset`, and sets `complete_cursor` **only** when all new paths and bytes were verified. C/I must retain the old cursor until the last page. Merge/rewrite/overwrite/truncation during scanning results in UNKNOWN and requires replay/reconciliation. Durable cursor persistence belongs to I/C.
- `GitHubChannel.path` builds deterministic `mail`, `receipts`, `handoffs`, `reports` paths. `validate_relay` requires canonical sorted-key compact UTF-8 JSON; SHA-256 is calculated over the exact bytes. I must still enforce registered task/lease/Canon/snapshot/recipient relationships and strong owner-policy isolation. Shared GitHub account authorship is not browser-chat identity proof.
- Drive adapter requires an **existing configured project folder ID**, a provider credential reference resolved outside chat, and an exact stable intent ID. Upload places immutable original bytes in a new Drive file and checks file's project appProperty, folder membership, actual remote bytes and SHA. On timeout it looks up the exact project+intent and does not create an alternate file silently. A project's Drive documents may be native draft files elsewhere; the adapter publishes original immutable binary snapshots, not Google Docs editor mutation.
- `GitHubActions` must point to an already-configured `workflow_dispatch` workflow on the authorized source ref. The workflow must implement `run-name: nomad:${{ inputs.nomad_intent_id }}`, accept inputs `nomad_intent_id` and `nomad_expected_sha`, check the actual checkout SHA against the input, and produce build artifact evidence. A 204 workflow dispatch is *only* request acceptance, not a run ID or PASS. G accepts a run only with pinned source SHA, workflow path and unique intent-bearing display title.
- `GOperatorPorts.run_build` obtains a C-owned prepared effect plus an **atomic first-dispatch claim**. Identical idempotent retries reuse `ci_id`; they never dispatch twice. The caller keeps a local CI intent `UNKNOWN` until an actual uniquely correlated run is observed. C must implement request ID + body hash deduplication and state CAS.
- H executes actual installed per-project tools; G does not spawn a process. B supplies package/install/grant state. `PackageVerifier.verify` checks every pinned resource/entrypoint on disk. G records artifact validator acceptance only when a real validator callback ran; E/L independently check submission and acceptance lineage. M alone stages/promotes the canonical integration/release.

## Missing cross-worker and live acceptance

All tests here are **Tier A offline**. No authenticated live GitHub, Drive, Actions, APSW runtime, browser relay, .NET tool runner, game project CI, independent QA or actual release test was executed by this workgroup. Each remains `NOT_RUN`, not PASS. C/J/I/B/H/M/L must supply their respective runtime implementations before integration tests can be run.

## Dependency evaluation (REQ-037)

Provider adapters use Python 3.11+ standard library (`urllib`, `hashlib`, `json`, `base64`, `pathlib`, `unittest`) so this worker introduces **zero external runtime packages**. This avoids an additional SDK lock/version/license/security surface for the small API endpoints used here. Python's version is pinned by the canonical application's deployment environment, owned by M. Real provider clean-install/SDK comparative benchmarks were **NOT_RUN** (no configured external providers); no dependency performance claim is made. APSW remains exclusive to C, FastAPI to J, .NET 10 process execution to H. No provider-model inference dependency exists.

## Run offline tests

From integration root: `PYTHONPATH=src python -m unittest discover -s tests/unit/g -v`

Source-only syntax check: `python -m compileall -q src/studio/integrations tests/unit/g tooling/connectors`
