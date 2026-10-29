# CAB-A SQL conformance — R9 immutable fresh-only schema

**Validated contract:** `CONTRACTS/initial_schema_v1.sql`, frozen 56-table R8 target DDL. `tests/cabals/core/fixtures/frozen_schema.sql` is a byte-for-byte test fixture, NOT an authorized schema change. No `org_*` tables, rank-based authorization, legacy migration, or old wire compatibility is used.

## Owned tables and foreign keys

| Table | CAB-A writes | Relevant frozen constraints | Runtime enforcement |
| --- | --- | --- | --- |
| `v1_cabals` | `INSERT` | `(project_id,cabal_id)` PK and project FK; `charter_epoch` nonnegative | Active project, current project authority epoch, pinned feature contract, current active Design/Technical Bible editions, feature-specific derived scope digest |
| `v1_memberships` | `INSERT` | `(project_id,cabal_id,worker_id)` PK and composite project/cabal FK | Selected only from CAB-G verified worker/skill rows and current CAB-H verified identity evidence; expiry, revocation, project skill scope, feature membership gate |
| `v1_deliberations` | `INSERT` | `group_id` PK; composite FK to cabal and feature run under same project | Subgroup is its *own* ephemeral cabal with membership subset; same project/feature/charter/scope/bible pair; actual text sent to CAB-D Bible conflict gate; trusted initiator context |

**Read-only shared tables:** `v1_projects`, `v1_feature_runs`, `v1_feature_contracts`, `v1_bible_editions`, `v1_tasks`, `v1_verified_identities`, `v1_worker_capabilities`, `v1_skill_capabilities`. CAB-A never writes those tables. P2 must inject a SQL transaction capability allowing writes ONLY to the three CAB-A tables above. `v1_tasks.payload_json` must contain the complete frozen `TaskSpec` record: authorizer rejects field substitutions even if an unchanged stored task digest is presented. A verified BiblePort snapshot is cross-checked against BOTH live active Bible edition identities/manifests/epochs in the SQLite reader/writer snapshot.

## Specification defects in frozen DDL (proposed changes require new owner-approved contract revision)

1. **Missing direct `feature_id` on `v1_cabals`:** no composite FK from cabal `(project_id,feature_id)` to `v1_feature_runs`. CAB-A currently binds project, feature, contract, Bible pair and feature scope in a deterministic opaque digest, rechecking it on every operation. `get_cabal_roster` must scan in-scope feature runs to recover the matching feature. Proposed: `feature_id NOT NULL`, composite FK to feature run, and a project/feature index.
2. **Missing `parent_cabal_id` and typed subgroup membership snapshot lineage:** subgroup membership is durable as a separate cabal but the *parent* relation is not queryable after restart from DDL fields. CAB-A validates parent/subset at creation and hashes parent ID into subgroup ID, but cannot provide independently inspectable lineage. Proposed: parent composite FK or dedicated immutable subgroup lineage table scoped to `(project_id, feature_id)`.
3. **Missing `question_digest`, initiator proof and compliance receipt reference on `v1_deliberations`:** the table stores subgroup scope/state/time, not the actual immutable question and CAB-D evidence. A group can be authorized while CAB-D checks its text, but an independent later audit cannot retrieve the exact checked question from this table. Proposed typed immutable refs/digests for question, trusted initiator, Bible assessment evidence. CAB-D remains the compliance decision owner.
4. **Missing subgroup expiry/close epoch:** memberships expire, but `v1_deliberations.status` and the subgroup cabal state have no frozen close/expiry API, so their rows remain as historical `OPEN`/`ACTIVE` after their workforce access expires. Proposed immutable close/reap lifecycle plus `expires_at_us` and state transitions under CAB-A single-writer authority.
5. **Missing relational skill FK:** `v1_memberships.role_ref` is free-form text without FK to `v1_skill_capabilities`. CAB-A currently rechecks verified qualification and identity at every task authorization. Propose FK if compatible with intended role-ref semantics; do not equate role rank with permission.
6. **Missing feature/cabal FK from `v1_tasks`:** it stores feature and cabal IDs, but they are not linked by a composite project/feature cabal foreign key. CAB-A explicitly checks both plus graph, contract, pair and frozen task payload. A composite FK requires adding direct `feature_id` to cabals first.

These are **specification findings**, not permission to modify central DDL. The current implementation fail-closes on incorrect scope, identity, project, active edition and charter epoch in its four port operations. No owner authority is derived from membership, subgroup deliberation, qualified role, or returned `Result`.
