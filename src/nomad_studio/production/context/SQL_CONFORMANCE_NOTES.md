# R9 CAB-I — frozen SQL conformance and P2 wiring

Source identity: `Nomad_Studio_V1_Consolidated_Source_Candidate.zip`, SHA-256 `237ef363f4206df180b31f1ab8ff147916b9b3660f8f23756eeb31f733356d43`. The canonical R8 `initial_schema_v1.sql` is **not edited**. Test fixture `tests/production/context/fixtures/initial_schema_v1.sql` is an exact copy, used to execute the new CAB-I tests without a P2 integration service.

## Exclusive CAB-I DDL table writers

`v1_archive_refs`, `v1_artifact_manifests`, `v1_asset_cas`, `v1_canon_outcomes`, `v1_context_bundles`, `v1_effect_idempotency`, `v1_effect_intents`, `v1_provider_receipts`, `v1_source_commits`, `v1_source_leases`.

Exactly 10 CAB-I-owned tables. No CAB-I module writes `v1_idempotency`, `v1_outbox`, `v1_events`, other CAB tables, or any schema DDL. P2 must inject a CAB-I-table-restricted serialized writer and independent bounded reader. The tests' SQLite fixture uses `PRAGMA foreign_keys=ON`; the fresh R8 schema must be installed once by P2, with no migrations.

## Frozen foreign-key and scope checks

- `v1_context_bundles(feature_id)` -> `v1_feature_runs`; source SHA and immutable Bible pair digests are stored in CAS and checked against the authoritative feature row and two active edition rows.
- `v1_canon_outcomes(feature_id)` -> `v1_feature_runs`; the source and acceptance are derived from a trusted QA readback, not an agent's self-asserted canon edit.
- `v1_artifact_manifests(feature_id, task_id)` -> `v1_tasks(feature_id,task_id)`; the worker lease epoch, graph revision, active contract, immutable byte proof and exact producer are checked before insert.
- `v1_source_leases(feature_id)` -> `v1_feature_runs`; SQLite unique index prevents identical active normalized path keys, while the service's serialized writer transaction explicitly rejects ancestor/descendant overlaps; expired leases are retired in that same transaction. A conditional provider commit fences the expected branch parent SHA.
- `v1_source_commits(feature_id)` -> `v1_feature_runs`; commit evidence requires exact native provider readback (a fixture provider is synthetic test evidence only).
- `v1_effect_idempotency(effect_id)` and `v1_provider_receipts(effect_id)` -> `v1_effect_intents`; `(provider_id,idempotency_key)` binds a single immutable effect and archived canonical request bytes. New intents with the same key but changed meaning conflict.
- `v1_asset_cas` stores digest, immutable storage reference, byte size and media category. The actual bytes are owned by P2-configured external CAS and independently reread before records are accepted.

## Implementation seams for P2

1. Instantiate `ContextService`, `ProjectAdapterService`, `RepositoryService`, `ArtifactService`, and `EffectService` with explicit trusted capabilities; expose only the exact frozen `ContextPort`, `ProjectAdapterPort`, `RepositoryPort`, `ArtifactPort`, `EffectPort` method names. CAB-I's internal `Result`, `Decision`, `SourceFence` dataclasses are field-compatible with frozen runtime types; P2 must choose a single central exported runtime type before composing the 22 ports.
2. `db.read(sql,params)` returns row mappings, `db.write(callback(tx))` invokes the callback in one serial CAB-I-authorized transaction. P2 must enforce the exact 10 allowed writer table names at the central state boundary, never by trusting an arbitrary SQL string in an agent command.
3. `cas.put(bytes, expected_sha256)` returns a durable storage ref; `cas.get(ref)` returns the same bytes. Persist data outside the SQLite writer transaction and verify hashes before journaling references. CAS must be authenticated and project-scoped where externalized.
4. Repository provider: `project_id(repo)`, `is_protected(repo,branch)`, `head(repo,branch)`, `commit_feature_branch(repo,branch,expected_parent,patches,idempotency_key)`, `readback(repo,branch,receipt_ref)`; must use provider-enforced optimistic compare-and-swap for the exact expected parent, and independently verify native write receipt identity. `scope.allows(run,fence,path)` validates original feature source scope; `scope.verify_interface(run,fence,bundle)` checks exact per-feature ABI/bridge compatibility against the immutable active contract, not a worker assertion. `reviewer.verify(feature,patch_digest,receipt_ref)` must derive verified reviewer independence from CAB-E.
5. Context's `bibles.active_pair/excerpt`, `source.head/project_id/project_snapshot`, and `quality.accepted_outcome` must be genuine CAB-D/CAB-E/readback capabilities. No bible mutation methods are implemented in CAB-I.
6. Artifact `identity.verify_task_author` must cryptographically or provider-natively validate the worker lease/identity. `EffectService` requires `scope.permits`, `owner_auth.verify` and provider methods `provider_for/verify_source_fence/dispatch/observe/verify_native_observation`. The provider observation must be authenticated and must declare `simulated=false` before `COMMITTED` is possible. Source and external provider calls must run outside SQLite writer transactions.
7. `ProjectAdapterService` requires a Technical Bible-bound registry, verified source readback, and a trusted runner with `authenticate`, `execute`, `verify_native`; optional `verify_cached_receipt` is called before a cached pure test can be reused. Runner outputs include actual log bytes, exact toolchain/adapter/input hashes, and a simulation flag. A Python fixture is used in local tests; it cannot provide live host or QA proof.
8. `v1_effect_intents` in `AUTHORIZED`/`UNKNOWN_EXTERNAL` states are durable work to be discovered by P2's root-owned outbox/recovery scheduler. P2 owns `v1_outbox` and recovery checkpoints. An external timeout remains `UNKNOWN_EXTERNAL` until authoritative provider readback proves applied/not applied; never automatically resend an uncertain effect.
9. `run_feature_smoke`/`verify_assets` need the CAB-E quality gate's required check IDs and an exact feature asset manifest. P2 must supply real target game toolchains (Godot/FMOD) for production, not the fixture Python runner.

## Locally executed verification

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q tests/knowledge tests/source tests/artifacts tests/publication tests/archive tests/production/context`.

The legacy baseline suites are baseline regression checks only; they are not evidence that P2's replacement runtime has been wired, deployed, or accepted. Real Git readback, native engine import/build, protected branch review, release gating, and production host validation belong to P2/P3/P4/P5.

10. P2 must add the parent `src/nomad_studio/production/__init__.py` (protected P2-owned path) before wheel packaging; CAB-I cannot edit that path. Python source imports work through the namespace, but current setuptools package discovery will not reliably package the child packages until the parent is declared.
