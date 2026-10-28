# V1.6 publication authority repair — additive migration 0002

This is a WG-I staging integration candidate. It does not change any of the
19 WG-A frozen files or enable publication, model launches, or automatic cutover.

## Problem and remedy

The original DB command stores `artifact_sha256` as `source_publications.proposed_tree_hash`.
That is incorrect for a Git publication: `artifact_sha256` is the binary **patch** digest,
while the independently calculated proposed Git tree digest is
`target.proposed_tree_sha256`. The old command also has no Drive
`expected_revision` and cannot persist a full, reviewed `PUBLISH-V16/INTENT/1`.

Migration `0002_publication_authority` keeps 0001 checksums unchanged, adds
`source_publications.patch_sha256`, and adds `publication_intents` with immutable
canonical intent JSON, digest, principal/grant/owner/QA identifiers, exact
assignment+lease+revision witnesses, patch SHA, tree SHA, Drive expected revision,
and matching outbox identity. Historical partial publication rows are **not**
backfilled or granted authority. They are excluded from the new recorded-intent
read side. The two legacy partial DB commands now deny publication.

## Trusted execution order

1. The isolated WG-D gateway authenticates its mTLS principal. It uses a
   separately provisioned trusted evidence reader to load externally verified
   `State` (issuer/owner/grant/QA/CI information). No worker-owned JSON is a
   substitute. Construct `PostgresAuthority(store, authoritative_evidence=reader)`;
   the reader must have `load(job_id, grant_id) -> State` and independently check
   signature/revocation/attestation identities. An absent reader fails closed.
2. `PostgresAuthority.load` checks live SQL state, grant, owner decision, issuer
   and reviewer status, assignment, lease, budget and approved scope. State revision
   must be `pg:<jobs.version>:<grant_id>:<assignment_epoch>:<lease_generation>`;
   the evidence reader must use current DB state when issuing that token.
3. `Gateway.prepare` validates original artifact bytes, trusted patch inspection
   or Drive revision and independent QA, then `reserve_gateway_intent` atomically
   writes *all* intent, source/document record, immutable event and outbox identity.
   Only ACTIVE owner-approved DB mode permits it. This method does no provider I/O.
4. WG-G's external providers must be constructed with a trusted callback
   `authorized=PostgresAuthority.authorize_effect`. It verifies that the exact
   immutable intent is still PREPARED and that live authority/mode/fences permit
   a CAS write. **Do not** expose this callback to worker clients. Generic
   outbox claims/ACK/reconciliation cannot claim Git/Drive publication effects.
5. After a server-controlled publish, `Gateway.reconcile` independently reads
   the provider. On exact Git tree+patch+commit or Drive export+new revision, the
   PG transaction records a VERIFIED readback and marks outbox delivered. On a
   mismatched provider observation, the PG conflict hook records evidence,
   sets publication CONFLICT and outbox BLOCKED; the same identity cannot retry.
   Ambiguous results require provider readback before any new effect attempt.

## Transaction / concurrency rules

All publication mutations lock job/grant/decision/attempt/lease, then mode,
then the specific immutable publication row. Duplicate identical reservations
return `DUPLICATE`, changed intents with reused identity return
`IDEMPOTENCY_CONFLICT`. Immutable intent fields are protected by an SQL
trigger. VERIFIED and CONFLICT are terminal. A different source revision,
assignment, lease, policy or revoked grant prevents effect authorization.

## Required integration configuration

- Install migration 0002 on a disposable PostgreSQL 15+ instance first;
  verify checksum-locked 0001/0002 replay and a backup/restore. Run
  `NOMAD_V16_TEST_PG_DSN=... python -m unittest discover -s tests/v16/db -v`.
- Supply a **real independent** issuer/owner/QA evidence reader and separated
  owner, gateway and provider credentials. This repository does not provision
  or cryptographically attest those identities.
- Configure an isolated mTLS gateway, audited authorization service, provider
  readbacks, trusted work branch mapping, and `authorized` callbacks. Never
  route an untrusted worker directly to `PostgresStore`.
- Enable SHADOW-to-ACTIVE only through an independently approved owner release,
  with signed revision, cutover watermark, existing writer retired and restore
  evidence. No activation method is implemented here.
- Require an independent reviewer to rerun WG-H's 18 real adapter scenarios
  at the exact integrated SHA before enabling external effects.

## Rollback

Production rollback switches writer authority back to SHADOW and preserves all
publication-intent, audit and provider evidence. The SQL `.down.sql` scripts are
**destructive and disposable-test-only**; never use them to roll back production
state. No automatic restart or re-publication follows a timeout.
