# CAB-H R9 frozen SQL conformance

Source contract: `CONTRACTS/initial_schema_v1.sql` (SHA-256
`f2a76f51fef851fe972500c906f31cde84bf63e446a4a149ab6ac08c9fe410f5`),
`CONTRACTS/schema_table_owners.json`. The owned test fixture copies are byte-for-byte
copies of these files and are not a replacement production migration.

Fresh-schema SQLite in-memory DDL install: **56 `v1_` tables**; `PRAGMA foreign_keys=ON`.
CAB-H sole-writer table identities, exactly five:

| Table | Primary/unique keys | CAB-H responsibility |
| --- | --- | --- |
| `v1_verified_identities` | `(principal_id, credential_id)` | authority epoch, signed enrollment reference, expiry |
| `v1_host_sessions` | `session_id` | authenticated native session, provider, epoch and capability digest |
| `v1_host_activations` | `(session_id, job_id)`, UNIQUE native receipt | distinct-job native activation readback |
| `v1_provider_capabilities` | `(provider_id, capability_name)` | observed SUPPORTED/UNSUPPORTED/NOT_VERIFIED only |
| `v1_trusted_host_observations` | `observation_id`, UNIQUE provider native ref | immutable native observation digests |

`v1_host_activations.session_id` and `v1_trusted_host_observations.session_id`
reference `v1_host_sessions.session_id`; foreign session inserts fail. Negative
SQL tests confirm both FK integrity and capability/epoch CHECK restrictions.
The DDL has **no** FK from session principal to verified identities; CAB-H
explicitly reads `v1_verified_identities` at native session admission and binds
principal in readback. Identity proof expiry/epoch and CAB-C worker lease
are verified in service methods.

P2 must inject a writer constrained to those **five concrete table identities**,
using serial local transactions for insert/CAS. The CAB-H `store.transaction(fn)`
callback exposes only an allowlisted SQL transaction adapter; `read_one` also
requires table allowlisting. No CAB-H service directly opens SQLite or writes
P2-owned `v1_events`, `v1_idempotency`, `v1_outbox`, or `v1_transaction_receipts`.
The **P2 effect journal** owns those durable effect/idempotency records and must
persist NEW/SAME/CONFLICT and UNKNOWN before provider replay decisions.

Test status: actual owned SQLite conformance tests run against the frozen DDL;
no integrated single-writer process, root capability enforcement or live host
has been certified by this worker.
