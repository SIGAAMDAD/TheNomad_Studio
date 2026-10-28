"""Service-host diagnostics with explicit unknown measurements."""
from pathlib import Path
import shutil


def observe(owner):
    with owner.connection(readonly=True) as conn:
        mode = conn.execute('SELECT mode,signed_revision FROM authority_mode WHERE singleton=1').fetchone()
        high_water = conn.execute('SELECT COALESCE(max(sequence),0) FROM event_journal').fetchone()[0]
        command_high_water = conn.execute('SELECT COALESCE(max(commit_sequence),0) FROM command_results').fetchone()[0]
        outbox = conn.execute('''SELECT status,count(*),min(created_at) FROM outbox
            WHERE status NOT IN ('DELIVERED','DEAD_LETTER','BLOCKED') GROUP BY status''').fetchall()
        migrations = conn.execute('SELECT version,checksum FROM schema_migrations ORDER BY version').fetchall()
        pragmas = {name: conn.execute('PRAGMA ' + name).fetchone()[0]
                   for name in ('journal_mode', 'synchronous', 'foreign_keys', 'trusted_schema', 'busy_timeout')}
    wal = Path(str(owner.path) + '-wal')
    return dict(backend='sqlite_v2', mode=mode[0], signed_revision=mode[1], pragmas=pragmas,
                migrations=migrations, event_high_water=high_water, command_high_water=command_high_water,
                pending_outbox=outbox, writer=dict(owner.metrics), wal_bytes=wal.stat().st_size if wal.exists() else 0,
                disk_free_bytes=shutil.disk_usage(owner.path.parent).free,
                oldest_reader_age='NOT_INSTRUMENTED', backup_age='REQUIRES_BACKUP_MANIFEST',
                off_host_restore_age='NOT_ATTESTED', provider_requests='PROVIDER_ADAPTER_COUNTERS')
