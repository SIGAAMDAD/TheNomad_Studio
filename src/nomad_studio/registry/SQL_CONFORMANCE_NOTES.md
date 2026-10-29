# CAB-G R9 SQL conformance (new-only)

Authority: `CONTRACTS/initial_schema_v1.sql` and `CONTRACTS/schema_table_owners.json`, which P2 owns. CAB-G MUST NOT edit either file. CAB-G's only writable tables are `v1_skill_capabilities`, `v1_worker_capabilities`, `v1_cabal_capacity`, `v1_budget_reservations`, `v1_budget_settlements`, and `v1_persona_refs`. `v1_cabal_capacity` is configured through P2's authenticated, CAB-G-scoped writer; capability placement only **reads** capacity and does not grant leases or write CAB-C scheduling state.

| Frozen table | Implementation use | Invariants |
| --- | --- | --- |
| `v1_skill_capabilities` | Registry installer | `skill_id` PK, trusted installed bytes SHA256, per-project scope, requirements/evidence in `payload_json` |
| `v1_worker_capabilities` | Qualified observer sync | `(worker_id,skill_id)` PK, FK to existing skill, nonempty trusted evidence, `available IN (0,1)` |
| `v1_cabal_capacity` | Allocation read | `(project_id,cabal_id,resource_class)` PK; project/cabal FK; nonnegative reserved and capacity, no oversubscription |
| `v1_budget_reservations` | Reserve/reconcile | PK reservation, feature FK, nonnegative unit_limit/reserved/measured; protected idempotent intent |
| `v1_budget_settlements` | Measured charge | PK settlement, reservation FK, unique verified provider receipt ref, positive clock observation |
| `v1_persona_refs` | Style-only pointer | worker PK, verified source digest, `style_only=1` required by CHECK constraint |

**Budget semantics:** canonical normalized billable units represent provider usage/cost ceilings; P2 billing adapter must convert observed provider charges into these immutable units. A serial write transaction checks `SUM(reserved_units + measured_units)` over the feature and refuses overage. Settlements replace reserved units with measured units, or release unused reserved units on final measurement; a replayed provider receipt must be byte/intent-identical. No provider call occurs inside the state transaction. The frozen DDL has no additional currency or exchange-rate columns. Unit normalization and binding of the active feature budget must be done by P2's trusted feature-budget/billing provider.

**Project scope:** `v1_skill_capabilities.skill_id` is a global PK. CAB-G forbids reusing an id for different projects. CAB-G namespaces physical global skill keys as deterministic `skill:SHA256(project_id NUL specialization_id)`; source specialization IDs survive unchanged in the pinned roster payload. Project base roster is an explicit pinned project dataset, never a core-required 174-session roster; historical `heads` and `department` attributes have zero authorization effect.

**Composition:** injected `reader.read(fn)` and `writer.write(fn)` must run over SQLite connections with Row mapping, serialized writes, explicit table identity access control and atomic transaction commit/rollback. P2 additionally supplies trusted provider-installed skill readback, active bible, worker observation, charge readback, project budget and persona source/owner approval. P2 owns full schema conformance/22-port integration/foreign-key tests. CAB-G tests use a real SQLite instance and explicit test doubles, never claim native provider proof.
