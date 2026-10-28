"""Ordered, checksum-locked SQLite migrations without executescript auto-commit."""
from hashlib import sha256
from pathlib import Path
import sqlite3

from .connection import ConnectionOwner

MIGRATIONS = Path(__file__).with_name('migrations')
LEDGER = '''CREATE TABLE IF NOT EXISTS schema_migrations (
 version TEXT PRIMARY KEY, checksum TEXT NOT NULL CHECK(length(checksum)=64),
 applied_at INTEGER NOT NULL, dialect TEXT NOT NULL CHECK(dialect='sqlite'),
 runtime_revision TEXT NOT NULL
) STRICT'''


def statements(script):
    pending = ''
    for line in script.splitlines(keepends=True):
        # complete_statement understands trigger BEGIN/END and quoted semicolons.
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ''
    if pending.strip() and any(not x.lstrip().startswith('--') for x in pending.splitlines() if x.strip()):
        raise ValueError('incomplete migration SQL')


def migrate(owner: ConnectionOwner, *, directory=MIGRATIONS, runtime_revision='DB-STORAGE/2-SQLITE'):
    paths = sorted(Path(directory).glob('[0-9][0-9][0-9][0-9]_*.sql'))
    if not paths:
        raise ValueError('migration files missing')
    scripts = [(p.stem, p.read_bytes()) for p in paths]
    if len({name[:4] for name, _ in scripts}) != len(scripts):
        raise ValueError('duplicate migration sequence')
    expected = {name: sha256(raw).hexdigest() for name, raw in scripts}
    applied = []
    with owner.write() as (conn, now):
        conn.execute(LEDGER)
        saved = dict(conn.execute('SELECT version,checksum FROM schema_migrations'))
        # Validate the entire ledger before touching any schema, including future IDs.
        if any(expected.get(name) != checksum for name, checksum in saved.items()):
            raise ValueError('MIGRATION_CHECKSUM_MISMATCH')
        names = [name for name, _ in scripts]
        if set(names[:len(saved)]) != set(saved):
            raise ValueError('MIGRATION_ORDER_MISMATCH')
        for name, raw in scripts:
            if name in saved:
                continue
            for statement in statements(raw.decode('utf-8')):
                conn.execute(statement)
            conn.execute('INSERT INTO schema_migrations VALUES(?,?,?,?,?)',
                         (name, expected[name], now, 'sqlite', runtime_revision))
            applied.append(name)
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('MIGRATION_FOREIGN_KEY_FAILURE')
    return applied


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    with ConnectionOwner(args.database) as owner:
        print(migrate(owner))


if __name__ == '__main__':
    main()
