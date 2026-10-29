# Studio V1 — SQLite Authority and Initial Schema

**Status:** locally implemented; production external acceptance unverified.

Studio V1 is a greenfield baseline. There is no historical Studio database
migration or upgrade requirement. The **only** runtime SQL initialization source
is `src/nomad_studio/state/schema_v1.sql`, pinned by `SCHEMA_SHA256` in
`state/schema.py`. `open_store(config)` initializes it once on an empty SQLite
file, stores one `studio_v1_schema` identity record (version 1, SHA-256), and
verifies this identity on every reopen. Missing, changed, or pre-V1 database
state causes an explicit fail-closed `ConfigurationError`. No automatic old-data
conversion or module migration discovery exists.

The initial schema covers the 19 original Studio domains and the six additional
Studio-owned SQL namespaces included in the AG–AV source wave: `persona`,
`ownercontrol`, `convergence`, `capabilities`, `hr_bridge` and
`creative_policy`. Private Oversight's independent database is **not**
consolidated into the Studio database. Ownership and segregation remain required.

## Runtime setup

```python
from nomad_studio.state.api import open_store, build_component
store = open_store({'path': '/private/nomad/studio.sqlite3'})
component = build_component({'state_store': store,
                             'snapshot_dir': '/private/nomad/snapshots'})
# Composition root owns store.close().
```

No `migration_roots`, `migrations`, `apply_migrations`, `migration_history`, or
`state.migrations` API exists in Studio V1. Requests to provide migration
settings are deliberately rejected. The only schema diagnostics are
`store.schema_identity()` and `state.schema`; neither initiates a conversion.

## Safety and isolation

One owner process holds a file lock, serializes writes on one dedicated writer
thread, enforces private local filesystem permissions, WAL, `synchronous=FULL`,
`foreign_keys=ON` and `trusted_schema=OFF`, and provides bounded read snapshots.
Transactions protect the event journal, idempotent receipts, namespaced records,
principal epochs and domain-scoped data. No external network call should run
inside a write transaction. Failed transaction callbacks roll back changes.

The following APIs remain part of the V1 runtime: `transact`, `transact_domain`,
`transact_guarded`, `read`, `diagnostics`, `integrity_check`,
`backup_snapshot`, `inspect_snapshot`, `set_actor_epoch`, `actor_epoch`,
`schema_identity`, and `close`. `state.snapshot` follows durable intent,
local SQLite backup, readback verification and final receipt publication.
Backup/restore is not evidence of an independently verified off-host archive.

## Verification

Run `python -m pytest -q tests/state tests/integration` from the source root.
Tests cover fresh installation, same-version restart, schema integrity/digest,
rejection of old databases and old migration inputs, writer fencing, concurrency,
idempotency, crash recovery and snapshots. No test requiring old Studio upgrades
belongs in the Studio V1 acceptance gate. The private production host, verified
ChatGPT worker execution, provider permissions and independent release QA remain
separate deployment checks.
