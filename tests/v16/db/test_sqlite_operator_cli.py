"""R02 SQLite-only local status does not grant a write or infer completion."""
from pathlib import Path
import sqlite3
import unittest
from scripts.studio_current import operator_reader,snapshot
from tests.v16.db import test_sqlite_commands_c8 as fixture

@unittest.skipUnless(sqlite3.sqlite_version_info>=(3,45),'SQLite >=3.45 required')
class CurrentOperatorTests(unittest.TestCase):
    setUp=fixture.SQLiteCommandsC8.setUp
    def test_snapshot_reports_real_state_not_completion(self):
        with operator_reader(self.owner.path) as conn:
            result=snapshot(conn)
            self.assertEqual(result['authority_mode'],'SHADOW')
            self.assertEqual(len(result['jobs']),1)
            self.assertEqual(result['jobs'][0]['state'],'READY')
            self.assertEqual(result['observed_from'],'SQLITE_READONLY')
            self.assertEqual(result['governance_approval'],'NOT_ASSERTED')
    def test_report_only_connection_cannot_mutate(self):
        with operator_reader(self.owner.path) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM jobs')
    def test_bad_path_and_limit_denied(self):
        with self.assertRaises(ValueError):
            with operator_reader(Path('studio/jobs.json')):
                pass
        with operator_reader(self.owner.path) as conn:
            with self.assertRaises(ValueError): snapshot(conn,limit=0)
            with self.assertRaises(ValueError): snapshot(conn,department='')
