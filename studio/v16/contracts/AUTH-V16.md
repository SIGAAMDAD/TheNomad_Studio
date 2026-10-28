# AUTH-V16/1 - external principal, job-scoped authority and denial semantics

## Trust and authorization boundary

`auth-evaluate` is a **client-controlled request**. `claimed_principal_id`, `worker_id`, `claimed_role_id` in RUN, `external_credential_ref`, and every role string are **untrusted assertions**. A connector's platform-level consent authorizes app access, **not Studio job/source authority**. Even a successfully parsed `auth-grant` is untrusted until fetched/verified independently from an owner-authorized, immutable issuer and checked for live revocation. The gateway derives `verified_principal_id` from an authenticated service account/token with a verified issuer and subject, not a chat-provided value. Shared-owner account/connector sessions cannot act as worker/head/QA principals for privileged actions. In that case return `UNTRUSTED_CONNECTOR` or `IDENTITY_UNVERIFIED` and retain local patch-only mode.

An authenticated owner decision binds **job, principal, worker, repo, operation, exact approved scope SHA256, source base SHA, assignment_epoch, lease_generation, policy_revision, authority_revision, issue/approval reference and expiry**. An approved role name grants nothing by itself. Issue comments, labels and PR metadata are observation evidence, not owner grants. Revocation must be resolved from durable trusted state, not only `revoked_at:null` on the presented object. A client cannot select a higher `policy_revision` or a newer epoch to bypass a stale credential.

## Evaluation order (first applicable denial returned)

1. Parse schema and supported version; `MALFORMED_REQUEST` / `UNKNOWN_SCHEMA_VERSION`.
2. Verify isolated gateway and externally authenticated **principal** via trusted credential channel; `GATEWAY_UNAVAILABLE`, `UNTRUSTED_CONNECTOR`, `IDENTITY_UNVERIFIED`.
3. Load owner decision, live grant, matching job/repo/worker, active approved scope, policy and authority revisions, authenticated grant issuer; `ACCESS_REQUIRED`, `OWNER_APPROVAL_REQUIRED`, `INVALID_SCOPE`, `POLICY_REVISION_MISMATCH`, `AUTHORITY_REVISION_MISMATCH`.
4. Check live revocation and time from server clock: `REVOKED_GRANT`, `EXPIRED_GRANT`. Expiry uses half-open interval `[issued_at, expires_at)` and UTC server time. No client clock authority.
5. Compare live assignment epoch **and lease_generation** to request/grant and active exclusive lease holder; `STALE_ASSIGNMENT`, `STALE_LEASE`. For read/ack contexts use the corresponding assignment/version fences; no privileged write by merely holding a lease.
6. Match exact repo, baseline SHA and branch target to current provider readback; `INVALID_SCOPE`, `REBASE_REQUIRED` (or `SOURCE_SHA_MISMATCH` for evidence mismatch).
7. Normalize every target path as a repo-relative POSIX path without decoding ambiguous encodings. Reject blank segments, `.`/`..`, backslashes, percent-encodings, control/Unicode confusables, unexpected dotfiles, symlinks, submodules, case-insensitive collisions and path prefixes outside `owned_path_prefixes`; `INVALID_PATH`, `PROTECTED_PATH`, `INVALID_SCOPE`. No bare string `startswith`: match **directory boundary**. Verify path and file type against actual Git tree (not client JSON) before any mutation. Treat rename/delete old and new paths as separate targets.
8. Check exact operation, branch policy, owner privileged gate, independent exact-SHA QA for promotion, budget and canon gates as applicable; `INVALID_SCOPE`, `OWNER_APPROVAL_REQUIRED`, `QA_PENDING`, `SELF_REVIEW`, `BUDGET_DENIED`, `CUTOVER_NOT_APPROVED`.
9. Return `ALLOW` only from the authenticated gateway after all server-side checks. Persist audited reason and immutable decision receipt. Never sign or publish an ALLOW based solely on fixture content.

`SOURCE_BRANCH_UPDATE` means **only assigned noncanonical work branch**. `SOURCE_MERGE`, `DEPLOY`, `APPROVE_CANON`, `APPROVE_SPEND` have no local worker path. WG-D may build owner-gated support later, but no sample fixture admits such operations. A QA principal cannot be the producer/reviewer of the same exact subject. The isolated trust boundary, not file-level prompts, must enforce this.

## Stable deny code set

The authoritative set is `$defs/denial_code` in `common.v1.schema.json`: `ACCESS_REQUIRED`, `IDENTITY_UNVERIFIED`, `UNTRUSTED_CONNECTOR`, `INVALID_SCOPE`, `INVALID_PATH`, `PROTECTED_PATH`, `POLICY_REVISION_MISMATCH`, `AUTHORITY_REVISION_MISMATCH`, `OWNER_APPROVAL_REQUIRED`, `EXPIRED_GRANT`, `REVOKED_GRANT`, `STALE_ASSIGNMENT`, `STALE_LEASE`, `REBASE_REQUIRED`, `SOURCE_SHA_MISMATCH`, `QA_PENDING`, `SELF_REVIEW`, `BUDGET_DENIED`, `CUTOVER_NOT_APPROVED`, `GATEWAY_UNAVAILABLE`, `DOCUMENT_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `UNKNOWN_SCHEMA_VERSION`, `RETRYABLE_BLOCKED`, `MALFORMED_REQUEST`. Return one stable primary code with internally safe event references; redact secrets and Oversight case contents.
