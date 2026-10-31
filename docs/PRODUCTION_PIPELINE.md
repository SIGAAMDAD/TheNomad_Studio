# R5R Studio production pipeline

## State and approval boundaries

- **Human owner:** scope, architecture, Canon, protected source merges and final publication.
- **APSW coordinator:** project-bound feature/task/worker state, leases, outbox, idempotency, checkpoint and recovery transactions.
- **GitHub:** actual remote command/receipt/source bytes and check-run evidence; commits identify credentials and SHA, not a unique ChatGPT worker identity.
- **Browser worker:** an already initialized consumer ChatGPT conversation executing an actual response. No scheduler or GitHub event can awaken an idle response.
- **Independent QA:** distinct reviewer of exact candidate bytes; never self-attested by the producer.

## Workflow

1. Authenticate operator and check grants and owner-approved scope for the project.
2. Verify a content-addressed feature brief, immutable Canon snapshot and canonical source revision. Resolve required role and explicit source workstream.
3. Persist project-scoped task and fenced assignment/lease, permitted resource reservation and exact addressed outbox command atomically.
4. Publish immutable command bytes to the project GitHub communications branch. If the network outcome is ambiguous, read back the exact address and digest before retrying.
5. An actually executing browser response reads its addressed command, performs bounded work and publishes real checkpoint/submission/result receipts.
6. Validate remote receipt bytes, project, recipient, shift, generation, lease and approved task. Reject stale, foreign or replayed results.
7. Obtain independent exact-revision QA; retain PASS/REWORK with reviewer identity and evidence.
8. For source work, check the approved workstream against `src/studio/app/source_policy.py` and the trusted per-project source target. The R5R factory validates remote repo identity, canonical branch and Git commit SHA before APSW source promotion. The owner controls protected merges.
9. Run or observe real CI for the pinned commit. Webhooks are hints for bounded readback, not evidence of success by themselves.
10. Publish owner-approved Canon or release effects only after accepted original source, independent QA, build results and final remote readback.

## Fault handling

Persist effects before transmission. Pre-write failures leave commands queued. Ambiguous after-write failures remain UNKNOWN until remote readback. Enforce separate project scope, bounded provider rate limits, restart-safe checkpoints, and orderly publisher shutdown. Distinguish source-promotion state from the external GitHub branch merge.

## Acceptance

The tested source contract has 72 operator operations. Require separate >=90% Python/APSW, TypeScript/TSX and production C# coverage evidence on the exact intended revision, then execute real configured-provider tests and F-20 browser continuation. Green source CI and generated frontend controls are not evidence of active browser automation or deployed provider readiness.

See [Setup](SETUP.md), [Owner acceptance](OWNER_SETUP.md), [Browser setup](CHATGPT_SETUP.md), and [Source routing](../studio/workforce/SOURCE_REPOSITORIES.md).
