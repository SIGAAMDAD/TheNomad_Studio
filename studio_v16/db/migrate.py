"""Checksum-locked PG migrations; rollback is test-only, never runtime rollback."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path

MIGRATIONS = Path(__file__).with_name('migrations')


def apply(conn) -> list[str]:
    """Acquire an advisory migration lock and atomically apply each unapplied version."""
    applied = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(1616, 2026)")
        for file in sorted(MIGRATIONS.glob('*.up.sql')):
            version = file.name.removesuffix('.up.sql')
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            exists = conn.execute('SELECT to_regclass(%s)', ('nomad_v16.schema_migrations',)).fetchone()[0]
            recorded = (conn.execute('SELECT checksum FROM nomad_v16.schema_migrations WHERE version=%s', (version,)).fetchone()
                        if exists else None)
            if recorded:
                if recorded[0].strip() != digest:
                    raise ValueError(f'migration checksum changed: {version}')
                continue
            conn.execute(file.read_text(encoding='utf-8'))
            conn.execute('INSERT INTO nomad_v16.schema_migrations(version,checksum) VALUES(%s,%s)', (version,digest))
            applied.append(version)
    return applied


def rollback_disposable(conn, *, disposable_test_database: bool = False) -> None:
    if not disposable_test_database:
        raise PermissionError('Destructive rollback requires explicit disposable-test-database marker')
    with conn.transaction():
        mode = conn.execute('SELECT mode FROM nomad_v16.authority_mode WHERE singleton=true').fetchone()
        if not mode or mode[0] != 'SHADOW':
            raise PermissionError('Cannot destroy ACTIVE database')
        for file in sorted(MIGRATIONS.glob('*.down.sql'),reverse=True):
            conn.execute(file.read_text(encoding='utf-8'))


def main() -> None:
    import psycopg
    parser = argparse.ArgumentParser(description='Nomad V1.6 schema migrations (never deploys/cuts over)')
    parser.add_argument('--dsn', default=os.environ.get('NOMAD_V16_PG_DSN'))
    parser.add_argument('--rollback-disposable-test-only', action='store_true')
    args = parser.parse_args()
    if not args.dsn:
        parser.error('Supply --dsn or NOMAD_V16_PG_DSN')
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        if args.rollback_disposable_test_only:
            rollback_disposable(conn,disposable_test_database=True)
            print('Disposable test schema dropped')
        else:
            print('Applied:', ', '.join(apply(conn)) or '(already current)')

if __name__ == '__main__':
    main()
