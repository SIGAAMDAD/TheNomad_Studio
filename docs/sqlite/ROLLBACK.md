# Non-destructive rollback

Before ACTIVE, stop the staging service and retain its SHADOW database and evidence.
No production authority has changed. Never apply destructive schema downs.

After ACTIVE, suspend new effect admission, quiesce the writer and take a verified
online snapshot. Enumerate every dispatch without a terminal receipt. The separate
recovery-only gateway can read back exact provider outcomes after revocation or
SHADOW; it cannot reserve or send another effect. Preserve conflicts and ambiguous
outcomes. A proved absence permits bounded retry only after fresh ACTIVE checks.

Do not switch back to PostgreSQL/V1 merely because its old snapshot is available.
Reconcile all external Git/Drive effects, capture the SQLite delta and prove the
old writer can resume from that exact watermark without two active writers. The
owner signs the writer-routing change. If reverse synchronization cannot be proved,
remain paused and recover forward from a separately restored SHADOW candidate.

Restore command and private-path requirements are in MIGRATION_RUNBOOK.md. A
restore always targets a new file, verifies file/table/key/sequence digests and
integrity, then forces SHADOW. Keep the original database/WAL, snapshot manifests,
source export and immutable receipts for investigation. Never overwrite the active
file or copy a live DB without its online-backup protocol.

RPO, RTO, host-loss survival, disk-full behavior and rollback drills are UNMEASURED.
The user deferred test execution. No tested operational rollback is claimed.
