# WG-F — Source Proof and Repository Ownership

**Frozen interface:** `NOMAD/CMD/1`, `NOMAD/QUERY/1`, `Component` from `nomad_studio.contracts.v1`. This module owns `src/nomad_studio/source/**` only and implements S16/S17/S18. It never writes to any game repository, creates a separate SQLite connection, authenticates a raw worker ID, or runs a GitHub publisher.

## Composition

```python
from nomad_studio.source.api import build_component
source = build_component({
    'store': shared_state_store,
    'source_reader': independent_git_reader,
    'scheduler': scheduler_component,
    # optional: 'clock_us': trusted_epoch_microseconds_callable,
})
# Source tables already exist in a freshly initialized Studio V1 StateStore.
```

All dependencies are injected. `store` is the *sole* WG-A `StateStore`, `scheduler` is the frozen `Component` contract (not an imported implementation), and `source_reader` is the independently authenticated, credential-limited provider adapter described below. Absence of required dependencies fails closed with `BLOCKED/NOT_CONFIGURED`. The component never loads credentials or permanent source-target mappings from a prompt. The frozen interface and standard-library-only imports are preserved.

## Source-target registry and policy

Target keys are bounded project-neutral identifiers, not a fixed list of game repositories. They are *identifiers*, not verified GitHub coordinates. Every target starts **unconfigured**. An authenticated owner must register its current exact `owner/repo` and `refs/heads/branch` before source operations can resolve it. All successful source claims re-read the live provider HEAD and check the current configured mapping epoch. No game source bytes are transferred into the Studio database/repository; stored data is metadata, SHA digests, reference identities, and sanitized proof bodies.

Policy `owned_paths` and `protected_paths` arrays contain exact paths or boundary-aware `dir/**` subtrees; other globs are rejected. Policy also supports `ci_workflows` (allowed exact CI workflow strings, optional) and `max_tree_entries` (default 200000, hard cap 500000). Effective protected paths always include `.github/**`, `.gitmodules`, and `.git/**`, even when absent from the owner-provided list. The canonical sorted policy JSON is SHA-256 digested; any policy update increments `mapping_epoch`, invalidating stale scheduler fences. File path modifications require the intersection of the registered source-owned paths **and** live assigned scheduler resource scope. The policy's `protected_paths` has priority over both.

**Strict path policy:** Mutated Git paths are UTF-8 strings limited to ASCII-safe, relative POSIX segments. Non-ASCII characters (including homogeneous Unicode lookalikes), drive/absolute paths, backslashes, dot/dot-dot segments, Windows-reserved names, trailing spaces/dots and wildcard path mutations are denied. This conservative rule rejects legitimate Unicode-path edits until a separately reviewed internationalized-path policy exists. Existing Unicode filenames may appear in Git tree metadata; casefold/NFKD/lookalike key collisions against requested changed paths are blocked. Full recursive tree metadata is required; incomplete or over-limit tree output is an untrusted readback. Symlinks/submodule endpoints and changes traversing them are denied. Changes are action-typed and must verify both source and destination for `rename`/`copy`; `delete` verifies its source.

## Exported commands and queries

All command payloads are closed objects. Unknown fields, unknown operations, missing required fields and wrong protocol versions fail closed. All commands require a gateway-minted, unexpired `VerifiedIdentity`, nonempty bounded string `request_id` and `idempotency_key`. `source:read`, `source:verify`, and `source:admin`/`owner:approve` are distinct scopes.

| Operation | Form / required identity scope | Payload shape | Result |
| --- | --- | --- | --- |
| `source.resolve` | command or query / `source:read` | `{"target": "Terrain3D"}` | `SOURCE_RESOLVED`: independently observed repository, exact ref, HEAD SHA, tree SHA, mapping epoch, policy digest, tree/symlink/submodule counts, protected patterns |
| `source.protect` | command / `source:admin` **and** `owner:approve` | Upsert: `{"action":"upsert", "target":"Terrain3D", "repository":"owner/repo", "ref":"refs/heads/dev", "policy":{...}, "expected_mapping_epoch":0}`. Disable: `{"action":"disable", "target":"Terrain3D", "expected_mapping_epoch":N}` | `SOURCE_POLICY_UPDATED`, idempotent and auditable; expected epoch compare-and-set |
| `source.protect` | query / `source:read` | `{"target":"Terrain3D"}` | `SOURCE_POLICY`: current stored mapping, policy, digest, enabled flag |
| `source.check_paths` | command / `source:verify` | `ClaimPayload` with `changes` | `PATHS_VERIFIED`: current live admissibility preview; **no persisted proof or receipt** |
| `source.verify_base` | command / `source:verify` | `ClaimPayload` with optional `changes` | `SOURCE_PROOF_VERIFIED`: durable exact HEAD/tree/path object SHA and scheduler-fence proof, audit event and receipt ID |
| `source.readback_ci` | command / `source:verify` | `ClaimPayload` without `changes`, plus `run_id` | `SOURCE_PROOF_VERIFIED`: exact provider-observed CI run repo/head/workflow/completion/success and current ref, durable audit event and receipt ID |

The claim payload contains **all** fields below (except `changes` for `readback_ci`):

```json
{
  "target": "Terrain3D",
  "base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "lease_generation": 12,
  "policy_digest": "<sha256 of registered source policy>",
  "resource_scope": ["src/**"],
  "changes": [{"action":"modify", "path":"src/app.py"}]
}
```

The command envelope additionally supplies `job_id`, `worker_id`, `assignment_epoch`, and `source_sha`; source SHA must equal the verified live HEAD and the scheduler's assigned base SHA. The `worker_id` is for routing/fence comparison only, never authentication. Path change forms are exactly `{"action":"add"|"modify", "path":"..."}`, `{"action":"delete", "source":"..."}`, or `{"action":"rename"|"copy", "source":"...", "destination":"..."}`. Maximum 1024 changes per request. Both source/destination must be owned, assigned, and unprotected. Adding to an existing/confusable destination is refused; modifying/deleting/renaming a missing file is refused.

## Scheduler fence integration seam

WG-F queries the **frozen** sibling `Component.query(...)` API with `QueryEnvelope(version='NOMAD/QUERY/1', domain='scheduler', operation='claim', job_id=..., worker_id=..., payload={"target": key, "repository": repo, "ref": ref})`. This must be interpreted as a **non-mutating, authoritative current-claim read** by the integrator; it may not create/reserve/extend a lease. WG-E's brief does not freeze a response schema, so the adapter is an explicit **INTEGRATION_CHANGE_REQUEST** pending WG-E integration.

The query must return `Outcome(state='ACCEPTED', payload={...})` with these trusted values, all sourced from the active authoritative lease/assignment in the sole state owner:

```json
{
  "principal_id":"worker-verified",
  "worker_id":"worker-1",
  "job_id":"job-1",
  "assignment_epoch":8,
  "lease_generation":12,
  "authority_epoch":9,
  "mapping_epoch":1,
  "policy_digest":"<canonical registered digest>",
  "resource_scope":["src/**"],
  "expected_head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expires_at_us":3000000000000000
}
```

Every field is checked against the authenticated principal, envelope, current SQLite policy digest and mapping epoch, current trusted time and independently observed provider HEAD. A worker-supplied SHA/digest/lease number can never replace this authoritative snapshot. No successful claim without scheduler integration. WG-E may expose an equivalent read-only snapshot through a composition-root adapter implementing `Component.query`; WG-F does **not** import or modify WG-E's source. If WG-E reserves `scheduler.claim` for mutations, the integrator must expose a sanctioned read operation and update callers/contract as an explicit coordinated integration change, not silently mutate a lease during verification.

The provider ref is re-read after scheduler validation (and after CI validation) to catch movement during verification. **Remote HEAD may still move after the proof transaction**. WG-I and WG-J must recheck exact target SHA at QA/merge, and source publication must use provider-side conditional expected-ref update/compare-and-swap. Persisted source proofs are historical evidence, not grants or indefinite current-state assertions.

## Independently authenticated Git/CI readback port

The integration-root `source_reader` must implement the `SourceReader` Protocol in `source/models.py`:

```python
credential_scopes: frozenset[str] = frozenset({'repo:read', 'ci:read'})
independent_readback: bool = True
get_ref(repository, ref) -> RefObservation
get_commit(repository, commit_sha) -> CommitObservation
get_tree(repository, tree_sha, *, max_entries) -> TreeObservation
get_ci(repository, run_id) -> CIObservation
```

`get_ref` returns the exact repository/ref/commit from a real authenticated source provider. `get_commit` resolves exact commit SHA and Git tree object SHA. `get_tree` returns a **complete** recursive listing of Git object metadata, with `kind=blob|tree|symlink|submodule`, Git object SHA, and canonical Git mode (`100644`, `100755`, `040000`, `120000`, `160000`). It must set `complete=False` on truncation or failed pagination. `get_ci` returns exact run ID, repo, subject HEAD SHA, workflow, provider status and conclusion. No method returns Git source blob bytes, mutates a ref, or publishes code. Git SHA-1 (40 hex) and Git SHA-256 (64 hex) are both supported as repository object identifiers; integrity of downloaded provider responses and certificate/authentication policy belongs to the adapter.

A source reader must have `repo:read` and `ci:read` for CI operations, contain **no** repo-write/admin/publish scope and be independently sourced from any worker-supplied commit, PR status, or WG-J publisher result. The facade enforces these port-declared capabilities, but whether credentials are *actually* independent/permission-limited must be established by production configuration and a real authenticated provider integration test. These test-only mocks do not attest real GitHub credentials.

## Persistence / events

The `source_*` tables are part of the single pinned Studio V1 initial schema. No separate module migration or historical schema replay is performed. Schema:

| Table | Durable purpose |
| --- | --- |
| `source_targets` | Five-name target registry, current owner-configured exact repository/ref, policy JSON/digest, generation, enabled flag, last confirmed head/tree metadata |
| `source_operations` | Idempotency key, canonical request SHA-256 and serialized outcome for accepted writes/proofs; changed payload with reused key returns `IDEMPOTENCY_CONFLICT` |
| `source_proofs` | Verified source/CI receipt ID, exact subject SHA, proof SHA-256/body, authenticated principal, issued time, FK to source target |

Accepted `source.protect` emits `SOURCE_POLICY_UPDATED`; accepted `source.verify_base` emits `SOURCE_BASE_VERIFIED`; accepted `source.readback_ci` emits `CI_READBACK_VERIFIED`. Each is appended to the **shared** StateTx event journal with a unique event ID and redacted target/SHA metadata. Source-side `check_paths` and `resolve` are read-only, return no durable audit proof, and do not append mutation events. No raw passwords, tokens, source blobs or log dumps are stored. All remote reads execute **outside** SQL write transactions.

## Fail-closed outcomes and limits

- `CONFLICT/REBASE_REQUIRED`: source HEAD/policy digest/mapping epoch moved; a new approved base/assignment is required.
- `CONFLICT/STALE_LEASE`, `CONFLICT/STALE_ASSIGNMENT`: expired/changed lease, worker/principal, assignment or authority generation.
- `DENIED/INVALID_PATH`, `DENIED/PROTECTED_PATH`, `DENIED/INVALID_SCOPE`: unsafe ownership/path or credential scope.
- `DENIED/SOURCE_SHA_MISMATCH`: provider CI run does not attest current expected HEAD.
- `BLOCKED/QA_PENDING`: CI is incomplete or concluded other than success.
- `BLOCKED/NOT_CONFIGURED`: missing target mapping, store, or scheduler snapshot adapter.
- `DENIED/UNTRUSTED_CONNECTOR`: provider port has missing read scope, publish scope, or lacks independent-readback assurance.
- `UNKNOWN/PROVIDER_UNKNOWN`: authenticated source/CI API unavailable, inconsistent readback, malformed metadata or incomplete tree; never PASS.
- `UNKNOWN/PERSISTENCE_UNKNOWN`: state-store or unexpected trusted-port error; never PASS.

Maximum 500000 recursive Git tree entries (configurable lower), 1024 changes, 256 ownership/protected patterns per list, 256 live resource patterns, 64 allowed CI workflow names. Memory cost is proportional to complete tree metadata; giant repositories should use bounded provider paging and refuse oversized requests. No internal retry loop; 429/timeouts propagate as UNKNOWN. No permanent global in-memory target cache or second mutable authority.

## Local acceptance / external gates

The in-memory SQLite test-only `FakeStateStore` applies real WG-F migration SQL and exercises actual inserts, idempotent receipts and event writes. `Reader` and `Scheduler` are isolated read-only fakes with injected outage, forged SHA, lease movement, revision movement, Unicode/case, protected-path and credential-scope scenarios. None of them impersonates a production connector.

**Required integration gates still NOT_RUN:** real authenticated GitHub read and CI API adapter; independent token permission/identity enforcement; exact five-repository live owner/ref mapping; real gateway `VerifiedIdentity`; real WG-E scheduler current-claim adapter; shared WG-A migration boot/order; WG-I QA/J conditional publication/merge verification; actual ChatGPT web connector and host; remote CI/CD. These require merging the corresponding worker modules and real credentials/hosting. Do not mark production acceptance PASS from these isolated tests.

**Integration change requests:** (1) confirm a non-mutating scheduler-current-claim query shape (`scheduler.claim` query or integrator-owned frozen-facade adapter) and transactionally grounded fields above; (2) ensure the installed wheel contains the pinned `state/schema_v1.sql`; (3) require WG-I/J to fetch source proofs from the central StateStore by receipt ID with exact SHA and fresh HEAD readback before accepting merge, rather than trusting caller-provided receipt data.
