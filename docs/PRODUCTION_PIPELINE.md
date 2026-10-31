# R5R Studio production pipeline

## Authority

The owner controls project scope, canon and protected source merges. The frozen contracts and verified source SHAs control interface shape; APSW is authoritative for project-scoped machine state; GitHub is authoritative for verified remote bytes. Google Drive provides owner-reviewed creative source documents.

## Pipeline

1. Authenticate the operator and apply project policy to a verified intake/feature brief.
2. Resolve current specialist role/capability, source pin and accepted work scope.
3. Reserve permitted resources; persist an assignment, active fenced lease and exact addressed outbox command in the APSW transaction.
4. Publish immutable bytes to the project's authenticated GitHub communications branch; a missing response after PUT remains unknown until exact readback.
5. An actually executing manually initialized browser shift receives its addressed command, produces the artifact and publishes a real receipt.
6. Independently verify remote receipt, current lease generation and submission evidence. Reject stale leases and cross-worker replay.
7. Independent QA evaluates exact revisions and records PASS or REWORK with distinct reviewer identity.
8. Source integration verifies ownership, approved baseline, QA and remote bytes before promotion.
9. CI runs on the configured source ref. Missed or duplicate webhooks trigger bounded readback; they cannot certify success or cause blind redispatch.
10. Owner-authorized canon or release publication is committed only after original source, QA, build and final effect readback.

## Failure semantics

Persist intent before external mutation. Pre-write failure leaves a command queued; ambiguous post-write outcome requires readback-only recovery. Checkpoints are durable, content verified and monotonic. A project failure does not starve unrelated projects; credential rate limits share a governor without repeated API polling. Shutdown preserves APSW ownership until dependent publishers have stopped.

## Acceptance

Require passing exact-SHA Python/APSW, TypeScript, .NET, provenance and control checks with >=90% coverage independently in each language. Live browser continuation F-20 is an additional acceptance gate, not implied by the source suite.

See SETUP.md, OWNER_SETUP.md and CHATGPT_SETUP.md.
