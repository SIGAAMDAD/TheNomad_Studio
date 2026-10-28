"""RECOVER-01: all-table SHADOW transfer crash and exact immutable replay."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from infra.sqlite.transfer import TABLES, import_shadow
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.migrate import migrate
from studio_v16.db.sqlite.types import canonical


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteImportCrashC8(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.export = self.root / 'source'
        self.export.mkdir()
        self.target = self.root / 'target.sqlite'
        with ConnectionOwner(self.root / 'source.sqlite') as owner:
            migrate(owner)
            # A real published historical event is represented as a source row.
            with owner.write() as (db, _):
                db.execute("INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES ('historic-worker','fixture','h','WORKER','ACTIVE')")
            with owner.connection(readonly=True) as db:
                entries = {}
                for table in TABLES:
                    names = [r[1] for r in db.execute('PRAGMA table_info(' + table + ')')]
                    if table == 'event_journal':
                        names.remove('sequence')
                    if table == 'schema_migrations':
                        names = ['version', 'checksum', 'applied_at']
                    pk = [r[1] for r in db.execute('PRAGMA table_info(' + table + ')') if r[5]]
                    if table == 'event_journal':
                        pk = ['event_id']
                    # A row in source may be read from SQLite, but this uses exactly
                    # the exported PostgreSQL JSONL manifest and validation path.
                    rows = [dict(zip(names, r)) for r in db.execute('SELECT ' + ','.join(names) + ' FROM ' + table)]
                    raw = b''.join((canonical(item)+'\n').encode() for item in rows)
                    (self.export / (table + '.jsonl')).write_bytes(raw)
                    entries[table] = dict(rows=len(rows), sha256=hashlib.sha256(raw).hexdigest(),
                                          columns=names, primary_keys=pk)
                original = dict(zip((r[1] for r in db.execute('PRAGMA table_info(authority_mode)')),
                                    db.execute('SELECT * FROM authority_mode').fetchone()))
        manifest = dict(schema='PG-CORE-EXPORT/1', source_snapshot='fixture-stable-snapshot',
                        source_authority=original, tables=entries)
        (self.export / 'manifest.json').write_text(canonical(manifest) + '\n')
        with ConnectionOwner(self.target) as target_owner:
            migrate(target_owner)

    def test_process_death_before_transfer_commit_rolls_back_entire_manifest_then_reimports_once(self):
        code = '''
from contextlib import contextmanager
import os, sys
from infra.sqlite.transfer import import_shadow
from studio_v16.db.sqlite.connection import ConnectionOwner
owner = ConnectionOwner(sys.argv[1])
original = owner.write
@contextmanager
def die_before_commit():
    with original() as value:
        yield value
        os._exit(0)
owner.write = die_before_commit
import_shadow(owner, sys.argv[2])
os._exit(7)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.target), str(self.export)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        with ConnectionOwner(self.target) as owner:
            with owner.connection(readonly=True) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM transfer_runs').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM historical_schema_migrations').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM principals').fetchone()[0], 0)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            receipt = import_shadow(owner, self.export)
            self.assertEqual(receipt['status'], 'IMPORTED_SHADOW')
            self.assertEqual(import_shadow(owner, self.export)['status'], 'DUPLICATE')
            with owner.connection(readonly=True) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM transfer_runs').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM principals').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT mode FROM authority_mode WHERE singleton=1').fetchone(), ('SHADOW',))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM grants').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM effect_dispatches').fetchone()[0], 0)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
