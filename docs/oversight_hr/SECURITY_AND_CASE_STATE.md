# Case-state, evidence, security, and failure protocol

## State transition matrix

Normal: `SEALED_RECEIVED → SCREENED → TRIAGED → INVESTIGATING → EVIDENCE_READY → FINDINGS_DRAFT → SECOND_REVIEW → OWNER_DISPOSITION → CLOSED`.

Side states are explicitly enumerated in `models.TRANSITIONS`: `BLOCKED_ACCESS`, `BLOCKED_INDEPENDENCE`, `WAITING_EVIDENCE`, `WAITING_OWNER`, `APPEALED`, `REOPENED`, and `DISMISSED_NO_EVIDENCE`. `TRIAGED` is created by `triage` after screened intake and includes evidence-based classification. Explicit `reopen_case` requires a new source-verified item and current policy revision. `APPEALED → CLOSED` needs `resolve_appeal` with a verified owner decision; a normal state transition cannot bypass it. Findings are versioned into `finding_versions`; appeals append rather than delete. Independent reviewer candidate cannot be the worker, reporter, original investigator, findings author or implicated party.

`CLAIMED`, `OBSERVED`, `CORROBORATED`, `INFERRED`, `DISPUTED`, and `INVALID_UNREADABLE` remain distinct. Verified event digest/source revision is mandatory for original observations; a corroborated source is required to draft an adverse finding. `record_second_review` requires reviewer identity and verified digest. A rejected/insufficient second review prevents owner disposition. Raw messages and AI interpretation cannot become policy facts.

## Atomic commits, replay and restart

An event ID maps to a canonical JSON SHA256 of its allowed input and a receipt. Same event+same payload returns the original receipt (even with concurrent calls); same event+changed payload yields `CONFLICT`. Every persisted revision stores only metadata and an append-only hash audit event. The `SQLiteJournal` uses `BEGIN IMMEDIATE` and one transaction for record CAS, event receipt and audit append. No external effect is inside this transaction. An effect intent has `PENDING_UNKNOWN` state until trusted gateway readback marks `ENACTED_READBACK`. Reconciliation performs **readback only** and does not retry the effect.

## Confidentiality and static negative controls

The API accepts encrypted receipt references, not case narrative bytes. `intake_sealed` checks an external custody proof; `case_status` reveals only authorized case ID/state/route/counts, `public_status` never discloses existence or case IDs. Hash audit returns no private source refs or case plaintext. An independent case-scoped custody grant is required even where the access port has permitted an HR operation. Anonymous/unscoped reads fail closed. A fake local archive byte sequence is used only by unit tests; production ciphertext handling, KMS, authenticated manifest signing, recoverability, deletion schedule and privacy compliance remain independent external gates.

The source contains no engine, audio, project-specific or cloud-service dependency. An AST-based negative test rejects imports of game runtime, Studio source core, external network/git client packages and privileged connectors. All normal initializations/queries leave the fake provider call counter at zero.

## Release semantics

`OK` means an isolated local method finished, **not** privileged production approval. Unavailable external authority: `UNSUPPORTED`, `NOT_CONNECTED`, `BLOCKED_PRIVATE_ACCESS`, `WAITING_EVIDENCE`, `OWNER_PENDING`, or `PENDING_UNKNOWN`; no fallback signing or plaintext read. P2 must verify the actual private source, then P3/P4 must independently test encryption, source protections, issued keys, host identity, worker sessions, provider readback and owner activation. `PRIVATE_REPO_INTEGRATION_NOT_VERIFIED` remains the release blocker for the private service.
