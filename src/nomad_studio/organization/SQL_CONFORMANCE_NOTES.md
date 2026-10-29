# CAB-B — R9 SQL Conformance Notes

**Read-only frozen authority:** `CONTRACTS/initial_schema_v1.sql` and
`CONTRACTS/schema_table_owners.json`. No DDL or migrations supplied by CAB-B.

## Single-writer ownership

`CAB-B` exclusively writes these four physical tables through a P2-injected
**service-bound transaction capability**:

| Table | Purpose | Boundary |
|---|---|---|
| `v1_projects` | Owner project identity; `state`/`authority_epoch` CAS | PK `project_id`; `state` IN `INCUBATING,ACTIVE,PAUSED` |
| `v1_project_constitutions` | Epoch-1 owner-authenticated project/constitution registration payload | `(project_id,epoch)` primary key; FK project |
| `v1_briefs` | Immutable original owner natural-language text and UTF-8 SHA-256 digest | Unique `brief_id`, `feature_id`, `owner_command_ref`; project/brief and project/feature composite uniques |
| `v1_feature_contracts` | Immutable Bible-locked revisions, complete deliverable/acceptance payload | `(feature_id,revision)` primary key; composite `(project_id,brief_id)` and `(project_id,feature_id)` FKs to briefs |

`v1_bible_editions` and `v1_bible_clauses` are **CAB-D-owned**; CAB-B may read
active editions to fence stale Bible epochs within a CAB-B transaction. The
active pair must contain exactly one Design and one Technical edition with
matching `edition_id`, `epoch`, `manifest_digest`. Activation is never written
by CAB-B. Context provenance originates from CAB-I, authority/conflict results
from CAB-D, trusted identity/owner proofs from CAB-H and root. No CAB-B SQL
statement writes their tables.

`v1_idempotency`, `v1_events`, `v1_outbox`, `v1_transaction_receipts` remain
root/P2-owned. P2 binds the CAB-B scoped state capability and records its
shared idempotency/event receipts. CAB-B uses stable brief/command and
project/contract composite uniqueness to reject request replay with altered
content; it never inserts directly into root idempotency tables.

## Frozen DDL observations

- `v1_project_constitutions` has `payload_json`, but `v1_projects` has no
  repository column. The registered `ProjectSpec.repository_ref` is retained
  in the immutable epoch-1 `payload_json` for contract source scoping.
- `v1_briefs.original_text` and `original_digest` cannot be rewritten by
  amendments; `amend_brief` delegates creation of an immutable
  `v1_feature_contracts.revision+1` with CAS.
- `v1_feature_contracts` includes `deliverables_digest` and
  `acceptance_digest` in addition to immutable canonical `payload_json`;
  stored digests are recalculated from the exact deliverable/acceptance arrays.
- The SQLite DDL enforces **composite brief and feature project FK membership**;
  CAB-B checks the same lineage before inserts and reads.
- A greenfield SQLite schema only. No `org_*` tables, issue-based hierarchy,
  historical aliases, fallbacks or migration shims are exercised.

## Local checks and integration responsibilities

Owned tests run using the **exact** frozen DDL with `PRAGMA foreign_keys=ON`,
real SQLite transactions and simulated scoped injected capabilities. The
adversarial matrix includes owner spoofing, digest tampering, replay conflicts,
Bible-epoch changes, wrong project/brief/feature, context-source injection,
mandatory-clause conflict reports and contract CAS. These are local simulated
provider results, not deployed owner/provider evidence.

P2 must bind the production single-writer capability with **explicit CAB-B
four-table allowlist**, trusted identity, active CAB-D BiblePort,
CAB-D AuthorityPort, CAB-I ContextPort, and trusted clock. P2 owns the command
routing of `studio.project.register`, all shared journal/outbox/idempotency,
central record type adapter, and integration/cross-CAB/fault gates.
