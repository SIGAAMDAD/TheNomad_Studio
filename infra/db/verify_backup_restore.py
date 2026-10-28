"""Verify pg_dump/pg_restore to an EMPTY disposable PostgreSQL database.

Requires `NOMAD_V16_TEST_PG_DSN` and `NOMAD_V16_RESTORE_TEST_PG_DSN`.
It never alters the source database. It never drops the restore schema afterward.
Use separate disposable local DBs; inspect the restored schema yourself.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys


def main():
    try:
        import psycopg
    except ImportError as e:
        raise SystemExit('psycopg3 required: pip install -r infra/db/requirements.txt') from e
    origin=os.environ.get('NOMAD_V16_TEST_PG_DSN')
    target=os.environ.get('NOMAD_V16_RESTORE_TEST_PG_DSN')
    if not origin or not target or origin == target:
        raise SystemExit('Set distinct source and restore-test DSNs. Never use production!')
    if not shutil.which('pg_dump') or not shutil.which('pg_restore'):
        raise SystemExit('PostgreSQL pg_dump and pg_restore binaries are required')
    with psycopg.connect(origin,autocommit=True) as src, psycopg.connect(target,autocommit=True) as dst:
        version=src.execute('SHOW server_version_num').fetchone()[0]
        if int(version)<150000:
            raise SystemExit('PostgreSQL 15+ required')
        if src.execute("SELECT to_regnamespace('nomad_v16')").fetchone()[0] is None:
            raise SystemExit('Source nomad_v16 schema absent')
        if dst.execute("SELECT to_regnamespace('nomad_v16')").fetchone()[0] is not None:
            raise SystemExit('Restore target schema exists. Refusing to overwrite data.')
        tables=[r[0] for r in src.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='nomad_v16' ORDER BY tablename").fetchall()]
        before={t:src.execute(f'SELECT COUNT(*) FROM nomad_v16.{t}').fetchone()[0] for t in tables}
    with tempfile.TemporaryDirectory(prefix='nomad_v16_pg_backup_') as tmp:
        snapshot=str(Path(tmp)/'snapshot.dump')
        subprocess.run(['pg_dump','--dbname',origin,'--format=custom','--schema=nomad_v16','--file',snapshot],check=True)
        subprocess.run(['pg_restore','--dbname',target,'--no-owner','--no-privileges','--exit-on-error',snapshot],check=True)
        with psycopg.connect(target,autocommit=True) as restored:
            after={t:restored.execute(f'SELECT COUNT(*) FROM nomad_v16.{t}').fetchone()[0] for t in tables}
            if before!=after:
                raise RuntimeError(f'Restored table counts differ: {[(t,before[t],after[t]) for t in tables if before[t]!=after[t]]}')
            original_migration=before.get('schema_migrations')
            if original_migration is None or original_migration<1:
                raise RuntimeError('Migration ledger missing')
        print(f'PASS: restored {len(tables)} tables; per-table row counts match; migration ledger present.')

if __name__=='__main__':
    main()
