# SQLite SHADOW initialization and transfer

Run operator tools only on the chosen POSIX service host, from the repository root
using Python with SQLite >=3.45.0 and the existing jsonschema dependency. Provision
a private local durable directory owned by the service account (mode 0700).
Database/WAL files must never live in Drive, a synced worktree, NFS or SMB.
Workers receive only authenticated gateway clients. No credential is in source.

Initialize: `python -m studio_v16.db.sqlite.migrate /srv/nomad/state/studio.db`.
The owner lock permits one logical service writer; each mutation uses the bounded
BEGIN IMMEDIATE lane. The initial authority is SHADOW. A repeated migration is
checksum checked; drift fails before schema changes. No DOWN tool is provided.

Production composition uses server-held `StorageConfig` and `create_store`,
`DurableAuthority` with independently verified authority evidence, existing Git
and Drive providers with `authorized=authority.authorize_effect`, and a separate
`RecoveryGateway`. Pass that recovery gateway to the optional internal transport
route. Recovery certificate identities must be explicitly configured both in mTLS
and `StorageConfig.recovery_principals`; their principal kind is RECOVERY_READBACK.
They cannot submit commands or authorize effects. Trusted evidence uses
`sqlite:<job_version>:<grant_id>:<epoch>:<lease_generation>` for state revision.

Configure `CoordinationService` from server-owned `Scope` entries, never worker
payloads. Bind departments to jobs through exact owner decisions before enabling
controller selection. Owner decision scope hashes bind the exact arguments used
by handoff/control/bind/quarantine APIs; these APIs do not mint approval evidence.
Document metadata registration requires independently read-back pinned revisions.
Drive publication evidence must carry document_writer_generation,
document_writer_token and current leader_epoch. The gateway combines those with
the existing job/grant/QA/source fences before dispatch. Historical document reads
require the provider client's optional read_revision capability; absent that
capability an exact current-revision read is the only supported read.

PG_EXISTS is UNKNOWN. Obtain actual writer-routing and deployment evidence.
If no PostgreSQL authority ever existed, initialize SQLite and ingest historical
V1/Git/Drive snapshots through shadow_import; those remain UNVERIFIED until exact
independent readback. Do not declare PG transfer NOT_APPLICABLE without evidence.

If PostgreSQL really contains authority, install the existing optional psycopg3
dependency on the operator host. Supply NOMAD_TRANSFER_PG_DSN through a protected
service environment, never a chat or command argument. Export with
`python -m infra.sqlite.transfer export /srv/nomad/exports/snapshot-001`.
The exporter uses a real read-only REPEATABLE READ transaction for all 25 tables,
lossless type conversion, per-table canonical row hashes and source watermark.
Import into a new initialized SHADOW file with
`python -m infra.sqlite.transfer import /srv/nomad/exports/snapshot-001 /srv/nomad/candidate/studio.db`.
The importer validates every row and digest, keeps SQLite's checksum ledger,
archives the original PG ledger, preserves original mode as provenance and forces
SHADOW. Source event order is received_at then event_id; new monotonic SQLite
positions are not inferred from random IDs. Repeating an unchanged import is inert.

Online backup: `python -m infra.sqlite.backup snapshot /srv/nomad/state/studio.db /srv/nomad/backups/snapshot-001.db`.
Verify: `python -m infra.sqlite.backup verify /srv/nomad/backups/snapshot-001.db`.
Restore in a separate process to a new private path:
`python -m infra.sqlite.backup restore /srv/nomad/backups/snapshot-001.db /srv/nomad/restore-001/studio.db`.
The online backup API works with WAL writing; a normal file copy is forbidden.
Restore checks the complete table/key/sequence manifest and integrity, then forces
SHADOW. Backups and exports contain privileged state: keep them operator-only,
encrypt off-host with operator-managed keys, verify transport hash and independently
restore the off-host copy. A local snapshot alone does not survive host loss.

Deploy an auditor hourly and the watchdog under a different credential/process.
Their injected delivery adapter must use a case ID idempotency key and independently
verify alert delivery. Failed delivery remains ALERT_DELIVERY_PENDING after bounded
retries and is visible in the owner dashboard. Monitor heartbeat outside the host
to detect service/host loss; this source does not claim such infrastructure exists.

ACTIVE remains blocked pending exact-SHA independent QA, accepted storage contract,
tested off-host restore, measured RPO/RTO, verified one-writer routing, live provider
revocation/recovery tests and the owner's signed cutover decision. Drain prior
authority, reconcile ambiguous dispatches and capture the final watermark before
switching routing. No automated ACTIVE promotion or chat launch is installed.
