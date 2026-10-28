"""Optional read-consistent PostgreSQL core export and fresh SHADOW import.

No psycopg dependency is imported unless an operator requests a real PG export.
Exports are private service artifacts, not worker deliverables or owner grants.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import os

from studio_v16.db.sqlite.types import canonical, parse_json, epoch_us, money_units, path_scope, utc_us
from .backup import file_hash, identifier

# FK order; publication/review intent relation intentionally follows all parents.
TABLES = ('schema_migrations', 'principals', 'jobs', 'job_dependencies', 'decisions', 'grants',
          'worker_assignments', 'attempts', 'resource_leases', 'event_journal', 'delivery_effects',
          'inbox_cursors', 'outbox', 'publication_keys', 'source_publications', 'document_publications',
          'reviews', 'audit_anchors', 'budget_reservations', 'recovery_checkpoints', 'command_results',
          'resume_capsules', 'shadow_imports', 'authority_mode', 'publication_intents')


def convert(table, column, value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return epoch_us(value)
    if table == 'budget_reservations' and column == 'amount':
        return money_units(value)
    if table == 'grants' and column == 'path_scope':
        return path_scope(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return canonical(value)
    if isinstance(value, Decimal):
        raise ValueError('unexpected decimal column')
    if type(value) not in (str, int):
        raise ValueError('unsupported PostgreSQL storage type')
    return value


def export_postgres(connect, destination, *, max_rows=1000000):
    destination = Path(destination)
    if not destination.is_absolute() or destination.exists() or any(p.is_symlink() for p in destination.parents):
        raise ValueError('new absolute private export directory required')
    destination.mkdir(mode=0o700, parents=True)
    manifest = dict(schema='PG-CORE-EXPORT/1', exported_at=utc_us(), tables={}, source_authority=None)
    total = 0
    # Real psycopg transaction, not a simulated importer or a live-write session.
    with connect() as conn, conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        manifest['source_snapshot'] = conn.execute('SELECT txid_current_snapshot()::text').fetchone()[0]
        for table in TABLES:
            columns = conn.execute('''SELECT column_name FROM information_schema.columns
                WHERE table_schema='nomad_v16' AND table_name=%s ORDER BY ordinal_position''', (table,)).fetchall()
            if not columns:
                raise ValueError('source core table missing: ' + table)
            keys = conn.execute('''SELECT a.attname FROM pg_index i JOIN pg_attribute a
                ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
                WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY a.attnum''', ('nomad_v16.' + table,)).fetchall()
            order = ['received_at', 'event_id'] if table == 'event_journal' else [r[0] for r in keys]
            if not order:
                raise ValueError('source primary key missing')
            cursor = conn.execute('SELECT * FROM nomad_v16.' + identifier(table) + ' ORDER BY ' + ','.join(map(identifier, order)))
            names = [c.name for c in cursor.description]
            count, checksum = 0, hashlib.sha256()
            path = destination / (table + '.jsonl')
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as target:
                while values := cursor.fetchmany(500):
                    for record in values:
                        data = {name: convert(table, name, v) for name, v in zip(names, record)}
                        raw = canonical(data).encode() + b'\n'
                        target.write(raw)
                        checksum.update(raw)
                        count += 1
                        total += 1
                        if total > max_rows:
                            raise ValueError('bounded export row limit exceeded')
                        if table == 'authority_mode':
                            manifest['source_authority'] = data
                target.flush()
                os.fsync(target.fileno())
            manifest['tables'][table] = dict(rows=count, sha256=checksum.hexdigest(), columns=names, primary_keys=[k[0] for k in keys])
    fd = os.open(destination / 'manifest.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(canonical(manifest) + '\n')
        target.flush()
        os.fsync(target.fileno())
    return manifest


def import_shadow(owner, source, *, max_rows=1000000):
    source = Path(source)
    manifest_raw = (source / 'manifest.json').read_bytes()
    manifest = parse_json(manifest_raw)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    if manifest.get('schema') != 'PG-CORE-EXPORT/1' or set(manifest['tables']) != set(TABLES):
        raise ValueError('exact 25-table source manifest required')
    total = 0
    for table in TABLES:
        entry = manifest['tables'][table]
        if (type(entry['rows']) is not int or entry['rows'] < 0 or
                file_hash(source / (table + '.jsonl')) != entry['sha256']):
            raise ValueError('export digest/count mismatch')
        total += entry['rows']
    if total > max_rows:
        raise ValueError('bounded import row limit exceeded')
    run_id = 'pg_' + manifest_sha[:40]
    with owner.write() as (conn, now):
        if conn.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone() != ('SHADOW',):
            raise ValueError('SHADOW target required')
        prior = conn.execute('SELECT source_manifest_sha256 FROM transfer_runs WHERE run_id=?', (run_id,)).fetchone()
        if prior:
            if prior[0] != manifest_sha:
                raise ValueError('transfer identity conflict')
            return dict(run_id=run_id, status='DUPLICATE', mode='SHADOW')
        if any(conn.execute('SELECT 1 FROM ' + identifier(t) + ' LIMIT 1').fetchone()
               for t in TABLES if t not in ('schema_migrations', 'authority_mode')):
            raise ValueError('fresh initialized target required')
        conn.execute('INSERT INTO transfer_runs VALUES(?,?,?,?)',
                     (run_id, manifest_sha, canonical(manifest['source_authority']), now))
        imported = {}
        for table in TABLES:
            entry = manifest['tables'][table]
            columns = [r[1] for r in conn.execute('PRAGMA table_info(' + identifier(table) + ')')]
            source_columns = entry['columns']
            expected_columns = (set(columns) - {'sequence'} if table == 'event_journal' else set(columns))
            if table == 'schema_migrations':
                expected_columns = {'version', 'checksum', 'applied_at'}
            if set(source_columns) != expected_columns or not entry['primary_keys'] or not set(entry['primary_keys']) <= set(source_columns):
                raise ValueError('unmapped source columns: ' + table)
            count, checksum = 0, hashlib.sha256()
            with (source / (table + '.jsonl')).open('rb') as data:
                for raw in data:
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError('import record too large')
                    checksum.update(raw)
                    value = parse_json(raw)
                    if set(value) != set(source_columns):
                        raise ValueError('row column mismatch')
                    if table == 'schema_migrations':
                        conn.execute('INSERT INTO historical_schema_migrations VALUES(?,?,?)', (run_id, value['version'], canonical(value)))
                    elif table == 'authority_mode':
                        if value['singleton'] != 1:
                            raise ValueError('invalid source singleton')
                        # Original ACTIVE mode survives solely as provenance.
                        conn.execute('UPDATE authority_mode SET cutover_watermark=?,owner_proof_ref=?,signed_revision=? WHERE singleton=1',
                                     (value['cutover_watermark'], value['owner_proof_ref'], value['signed_revision']))
                    else:
                        conn.execute('INSERT INTO ' + identifier(table) + '(' + ','.join(map(identifier, source_columns)) +
                                     ') VALUES(' + ','.join('?' for _ in source_columns) + ')', tuple(value[c] for c in source_columns))
                        # Exact SQLite row readback after conversion; new sequence
                        # fields are separately generated, never guessed from IDs.
                        keys = entry['primary_keys']
                        found = conn.execute('SELECT ' + ','.join(map(identifier, source_columns)) + ' FROM ' + identifier(table) +
                                             ' WHERE ' + ' AND '.join(identifier(k) + '=?' for k in keys), tuple(value[k] for k in keys)).fetchone()
                        if found != tuple(value[c] for c in source_columns):
                            raise ValueError('SQLite row readback mismatch: ' + table)
                    count += 1
            if count != entry['rows'] or checksum.hexdigest() != entry['sha256']:
                raise ValueError('source changed during import')
            imported[table] = dict(rows=count, sha256=checksum.hexdigest())
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('import foreign key mismatch')
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('import integrity mismatch')
    return dict(run_id=run_id, status='IMPORTED_SHADOW', source_manifest_sha256=manifest_sha,
                tables=imported, transformations=['PG migration ledger archived', 'authority forced SHADOW',
                'event sequence assigned in source received_at/event_id order'], independent_qa='NOT_RUN')


def connect_from_environment():
    # Used only by a separately authorized service operator with PG_EXISTS=YES.
    import psycopg
    dsn = os.environ['NOMAD_TRANSFER_PG_DSN']
    return psycopg.connect(dsn, autocommit=True)


def main():
    import argparse
    from studio_v16.db.sqlite.connection import ConnectionOwner
    from studio_v16.db.sqlite.migrate import migrate
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('export', 'import'))
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', nargs='?', type=Path)
    args = parser.parse_args()
    if args.operation == 'export':
        receipt = export_postgres(connect_from_environment, args.source)
    else:
        if args.destination is None:
            parser.error('fresh SQLite destination required')
        with ConnectionOwner(args.destination) as owner:
            migrate(owner)
            receipt = import_shadow(owner, args.source)
    print(canonical(receipt))


if __name__ == '__main__':
    main()
