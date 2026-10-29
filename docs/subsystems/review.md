# WG-I — Independent QA, CI, Rework and Merge Authority

**Frozen interface:** `NOMAD/CMD/1`, `NOMAD/QUERY/1`, `nomad_studio.contracts.v1`.
**Composition:** `nomad_studio.review.api.build_component(deps) -> Component`.
**Scope:** Review-control records and guarded merge *intent*; never acts as a GitHub merger or source writer.

## Dependency injection and trusted boundary

| Dependency | Type | Purpose |
| --- | --- | --- |
| `store` | frozen `StateStore` | **Sole** durable SQL writer/reader; WG-A owns connection, V1 schema and journal. |
| `source` | frozen `Component` | WG-F adapter; independent, provider-verified commit/asset branch-head and CI readbacks. |
| `trust` | frozen `Component` | WG-B adapter; live independently authenticated QA, integrator and department-head grants; signed owner decisions. |
| `authority_epoch` | `Callable[[], int]` | Live current gateway authority epoch (deny stale `VerifiedIdentity` even if a scope string exists). |
| `assignment_epoch` | optional `Callable[[str], int]` | Live WG-E epoch for requests carrying `CommandEnvelope.assignment_epoch`. If provided in command but adapter absent, return `NOT_RUN / NOT_CONFIGURED`. |
| `clock_us` | optional `Callable[[], int]` | Trusted service microsecond clock. Default `time.time_ns() // 1000`. |
| `required_ci_checks` | optional iterable of nonempty names | Operator-approved required CI names; defaults to `('unit-tests',)` for isolated setup only. Must be replaced by deployed branch/repository policy. |
| `required_owner_decisions` | optional iterable of decision kinds | Defaults to `('architecture','canon')`; owner-approved policy may add `release`, `qa`, etc. `architecture` and `canon` are always mandatory; additional owner decision kinds can be required by deployment policy. |

Use only the approved single-writer StateStore. The `review_*` tables are created as part of the single pinned Studio V1 initial schema; no migration registry or upgrade sequence is used. **Do not** open a SQLite connection in this production component. Tests use a real local SQLite adapter implementing the frozen `StateStore` interface.

`VerifiedIdentity` is trusted only after the WG-B gateway mints it. The component checks expiry, issuer/credential presence, live authority epoch and per-operation scopes. `worker_id`/`actor_claim` are deliberately ignored for authorization. Actual issuer/transport attestation must remain in the WG-B integration root. Changes to authenticated credential/epoch are never accepted from caller-supplied JSON.

## Operations: closed payload schemas

All commands require **fully-qualified** operation strings such as `review.submit` (bare `submit` is rejected), `domain='review'`, `version='NOMAD/CMD/1'`, nonempty unique `idempotency_key`, a matching optional envelope `job_id`, and the scope in the table. `source_sha`, hashes and readback SHAs are lowercase-normalized 64-digit hexadecimal values. Fields not listed are rejected. `review_idempotency` makes accepted effects transactional and exactly-once per key. Retried accepted requests yield `IDEMPOTENT_REPLAY`, a *historical* effect receipt; they do not certify current provider/QA state. A different payload under the same key yields `CONFLICT/IDEMPOTENCY_CONFLICT`.

| Command operation | Required fields | Optional fields | Required gateway scope | Durable effect |
| --- | --- | --- | --- | --- |
| `review.submit` | `job_id,department,repo,branch,source_sha` | `artifact_hash,artifact_revision` | `review.submit` | Verify source/author and create original job and revision 1. |
| `review.request_qa` | `job_id,revision` | none | `review.request_qa` | Revalidate head, asset, required CI; `PENDING -> QA_PENDING`. Only submitter requests. |
| `review.verdict` | `job_id,revision,result,findings` | none | `review.qa` | Require independent authenticated QA identity; write one `PASS`, `REWORK` or `NOT_RUN` per revision with findings/CI proof. |
| `review.rework` | `job_id,revision,source_sha` | `artifact_hash,artifact_revision` | `review.rework` | From `REWORK/NOT_RUN/INVALIDATED`: verify changed real source/asset, preserve original job and all historic verdicts, create `revision+1` in `PENDING`. |
| `review.integration` | `job_id,revision,repo,branch,integration_sha,target_branch` | none | `review.integrate` | Require subject `PASS`, separately authenticated integrator, real integration-branch containment and independently verified integration CI. No merge. |
| `review.merge_intent` (`action=prepare`) | `action,job_id,revision,expected_head_sha,decision_refs` | none | `review.head_merge` | Require different authenticated department head, signed owner approvals, exact subject/integration CI and target HEAD; reserve `PREPARED` merge intent. **No remote write.** |
| `review.merge_intent` (`action=reconcile`) | `action,job_id,revision,merge_id` | none | `review.head_merge` | Verify the provider actually included exact integration SHA and independently read back the new target HEAD; persist `MERGED`. Timeouts/uncertain evidence remain `UNKNOWN`/`PREPARED`. |

`result` is `PASS`, `REWORK` or `NOT_RUN`. `findings` is an array of bounded, nonempty strings (required nonempty on REWORK). `decision_refs` must contain exactly the kinds configured by `required_owner_decisions` and maps each to an opaque trusted owner decision reference. No decision is accepted based on payload flags.

Query operations on the same domain use `NOMAD/QUERY/1`, payload exactly `{job_id}`, and `review.read` scope:

- `review.submit`: current job status/revision/SHA.
- `review.verdict`: revision and reviewer/verdict history, persisted CI run IDs; cursor is a numeric revision.
- `review.integration`: integration branch history/CI evidence.
- `review.merge_intent`: prepared/invalidated/observed merge intents and exact target HEAD readback.

Query `limit` must be between 1 and 500; use returned `next_cursor` to page over revision histories. Read-only queries cannot move job state. Mutation payloads are bounded at 128 KiB.

## Source adapter (WG-F) proof semantics

Source adapter requests are **query-only** frozen `Component.query` calls; the review component never dispatches GitHub writes or network calls from inside SQL transactions.

| Query | Input payload | Mandatory **trusted provider** result fields |
| --- | --- | --- |
| `source.resolve` mode=`subject` | `{mode,repo,branch,expected_sha}` | `provider_verified=true,scope_verified=true,repo,branch,sha,author_principal_ids:[...]`, `artifact_hash` and `artifact_revision` (null allowed). The `sha` MUST be currently observed branch HEAD; source contributors must map to trusted studio principals. |
| `source.resolve` mode=`integration` | `{mode,repo,branch,expected_sha,subject_sha}` | `provider_verified=true,scope_verified=true,repo,branch,sha,contains_subject_sha` where the containment proof is verified by WG-F, not supplied by the integrator. |
| `source.resolve` mode=`target_head` | `{mode,repo,branch,expected_sha}` | `provider_verified=true,scope_verified=true,repo,branch,sha`, independently read back from target branch. |
| `source.resolve` mode=`merge_readback` | `{mode,repo,branch,merge_id,integration_sha}` | `provider_verified=true,scope_verified=true,repo,branch,sha,merge_id,merged_integration_sha`. `sha` is the **observed post-merge target HEAD**. WG-F must obtain independent provider ancestry/merge evidence linking that head to the exact integration SHA and intent, not echo an untrusted caller claim. |
| `source.readback_ci` | `{repo,branch,sha}` | `provider_verified=true,scope_verified=true,repo,branch,sha,runs:[{name,run_id,sha,provider_verified:true,status:'completed',conclusion:'success'},...]`. All configured required names must be present, unique and successful; unknown/missing/unverified is blocked. |

`scope_verified=true` means WG-F independently confirmed the configured approved repository, branch, path/lease protection and operation scope. Missing scope proof is denied. Every successful adapter response is `Outcome(state='ACCEPTED', payload=...)`. UNKNOWN/NOT_RUN, missing receipt proof, identity mismatch, network exception or spoofed `provider_verified=false` prevents review promotion. **WG-F must use its own verified GitHub provider readbacks and source-bound CI provenance.** A JSON object containing `provider_verified:true` is not by itself proof; the live adapter implementation and gateway principal validation are independent integration gates. No GitHub/Drive external communication was attempted in the WG-I unit tests.

If integration works across repositories, WG-F must explicitly attest exact inclusion/mapping of the reviewed SHA; if it cannot, return `REBASE_REQUIRED`/`PROVIDER_UNKNOWN` rather than guessing ancestry.

## Trust adapter (WG-B) proof semantics

`trust.authorize` is a `Component.query` call with `{action,principal_id,credential_id,authority_epoch,job_id,department,repo,branch,source_sha}` plus `decision_refs` on merge preparation. `action` is one of `qa_verdict`, `integration`, `head_merge`.

The authenticated gateway/trust service must independently prove and return: `provider_verified=true`, `allowed=true`, exact matching `principal_id`, `credential_id`, `authority_epoch`, `job_id`, `department`, `source_sha`, `action`, a nonempty `grant_receipt`, and role `qa` / `integrator` / `department_head` as appropriate. For `head_merge`, the accountable **department** is exact; a QA credential, worker prompt ID or integrator role cannot impersonate a head. The trust adapter is responsible for signed grant validation, expiry/revocation checking and independent head issuer binding.

On merge prepare, return `owner_decisions[kind] = {ref,verified:true,signature_verified:true,owner_principal_id,source_sha}` for **every** required decision kind, independently authenticated and hash-bound. Fail closed on missing signature/owner/canon/architecture. Do not accept reviewer-reported PASS as an owner signature. Approval refs and grant receipts are persisted only as opaque identifiers/hashes, not as secrets or signing material.

## Durable schema and event chain

The Studio V1 schema owns only these `review_*` names:

| Table | Authority |
| --- | --- |
| `review_jobs` | Original job ID, department, repository, head SHA, asset hash/revision, submitter, known source contributors, current review revision and status. |
| `review_revisions` | Monotonic revision history, immutable prior source/asset fingerprints and disposition. |
| `review_verdicts` | Single reviewer verdict per `(job_id,revision)`; findings, bound CI proof digest, **exact CI run IDs and subjects** in JSON. |
| `review_integrations` | Exact integration SHA and target branch, distinct integrator, CI proof digest and run evidence; one integration per revision. |
| `review_merge_intents` | Exact expected target HEAD, integration SHA, department head principal, independently minted grant receipt, decision-ref digest and reconciled HEAD. |
| `review_idempotency` | Mutation fingerprint and durable receipt, in the same transaction as state and audit event. |

Accepted mutations emit `DomainEvent(domain='review', ...)` via WG-A's `StateTx.append_event`, including `SUBMITTED`, `QA_REQUESTED`, `VERDICT_PASS`, `VERDICT_REWORK`, `VERDICT_NOT_RUN`, `REWORK_SUBMITTED`, `INVALIDATED`, `INTEGRATION_PREPARED`, `MERGE_INTENT_PREPARED`, `MERGE_INTENT_INVALIDATED`, `MERGE_READBACK_VERIFIED`. All contain a stable job ID, monotonic revision and redacted proof digest/IDs rather than raw credentials. An attested source/asset change invalidates old QA and any prepared merge intent. An attested integration branch change invalidates its prepared merge intent while leaving subject PASS history intact.

## Failure states, fault handling, and restrictions

- `DENIED`: malformed schema, invalid scope, unverified/revoked identity, self-review or self-merge.
- `BLOCKED`: `QA_PENDING`, `OWNER_APPROVAL_REQUIRED`, `SOURCE_SHA_MISMATCH`, `STALE_ASSIGNMENT`, `REBASE_REQUIRED`, etc.
- `CONFLICT`: conflicting idempotency key, existing job/review/integration/merge intent.
- `UNKNOWN`: absent/unverifiable provider proof, provider timeout, SQL failure/uncertain commit. Never report successful external mutation.
- `NOT_RUN/NOT_CONFIGURED`: assignment fencing requested but its trusted live reader is absent; real host/ChatGPT/connector tests are separately marked NOT_RUN.

The component deliberately cannot merge a branch. The manual integrator **must** connect an external conditional publishing saga (WG-J + the secure source provider) that consumes only a live, authenticated and unexpired `PREPARED` intent, rereads exact source and target heads, proves no drift, invokes the actual authenticated head-only provider operation, and then returns to WG-I for `merge_intent(reconcile)`. A prepared intent alone MUST NEVER authorize an unscoped generic GitHub merge. Repository branch protection may provide additional defense but must not replace the service guard.

Rechecked against the frozen seed contract under isolated tests. This file is a proposed integration adapter schema and **INTEGRATION_CHANGE_REQUEST** wherever WG-B/WG-F/WG-J cannot expose the described proof fields without coordinated changes; no sister module's implementation was imported or edited. Real GitHub review/merge, signed owner decision issuers, ordinary ChatGPT connector, deployment, and external CI were not available here and are `NOT_RUN`.
