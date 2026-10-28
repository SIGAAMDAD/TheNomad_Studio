"""RECOVER-01: process death inside a leadership transfer cannot split authority."""
import sqlite3
import subprocess
import sys
import unittest

from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.store import Denied, GatewayContext
from tests.v16.db import test_sqlite_leadership_race_c8 as leadership_fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteTransferCrashC8(unittest.TestCase):
    setUp = leadership_fixture.SQLiteLeadershipRaceC8.setUp
    _setup_handoffs = leadership_fixture.SQLiteLeadershipRaceC8._setup_handoffs

    def test_transfer_process_death_rolls_back_then_retry_commits_exactly_once(self):
        context = self._setup_handoffs()
        path = self.owner.path
        self.owner.close()
        code = '''
from contextlib import contextmanager
import os, sys
from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.store import GatewayContext
owner = ConnectionOwner(sys.argv[1])
store = SQLiteStore(owner)
original_write = owner.write
@contextmanager
def crash_before_commit():
    with original_write() as value:
        yield value
        os._exit(0)
owner.write = crash_before_commit
service = CoordinationService(store, policy={
    'owner': Scope(frozenset({'design'}), frozenset({'HANDOFF'}), owner=True),
    'candidate-a': Scope(frozenset({'design'}), frozenset({'CONTROL'}))})
service.handoff('design','candidate-a',1,'handoff-a', GatewayContext(
    principal_id='owner', verified_gateway=True))
os._exit(7)
'''
        process = subprocess.run([sys.executable, '-c', code, str(path)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        recovered = ConnectionOwner(path)
        self.addCleanup(recovered.close)
        policy = {'owner': Scope(frozenset({'design'}), frozenset({'HANDOFF'}), owner=True),
                  'candidate-a': Scope(frozenset({'design'}), frozenset({'CONTROL'})),
                  'candidate-b': Scope(frozenset({'design'}), frozenset({'CONTROL'}))}
        coord = CoordinationService(SQLiteStore(recovered), policy=policy)
        with recovered.connection(readonly=True) as db:
            self.assertEqual(db.execute("SELECT principal_id,leader_epoch FROM leader_state WHERE department='design'").fetchone(), ('leader', 1))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM leader_handoffs').fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(coord.handoff('design', 'candidate-a', 1, 'handoff-a', context), 2)
        with self.assertRaises(Denied):
            coord.handoff('design', 'candidate-b', 1, 'handoff-b', context)
        with recovered.connection(readonly=True) as db:
            self.assertEqual(db.execute("SELECT principal_id,leader_epoch FROM leader_state WHERE department='design'").fetchone(), ('candidate-a', 2))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM leader_handoffs').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))


    def test_committed_transfer_survives_process_exit_without_second_epoch(self):
        context = self._setup_handoffs()
        path = self.owner.path
        self.owner.close()
        code = """
import os, sys
from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.store import GatewayContext
owner = ConnectionOwner(sys.argv[1])
service = CoordinationService(SQLiteStore(owner), policy={
    'owner': Scope(frozenset({'design'}), frozenset({'HANDOFF'}), owner=True),
    'candidate-a': Scope(frozenset({'design'}), frozenset({'CONTROL'}))})
value = service.handoff('design','candidate-a',1,'handoff-a', GatewayContext(
    principal_id='owner', verified_gateway=True))
os._exit(0 if value == 2 else 7)
"""
        process = subprocess.run([sys.executable, '-c', code, str(path)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        with ConnectionOwner(path) as recovered:
            coordinator = CoordinationService(SQLiteStore(recovered), policy={
                'owner': Scope(frozenset({'design'}), frozenset({'HANDOFF'}), owner=True),
                'candidate-a': Scope(frozenset({'design'}), frozenset({'CONTROL'}))})
            with self.assertRaises(Denied):
                coordinator.handoff('design','candidate-a',1,'handoff-a',context)
            with recovered.connection(readonly=True) as db:
                self.assertEqual(db.execute("SELECT principal_id,leader_epoch FROM leader_state WHERE department='design'").fetchone(), ('candidate-a', 2))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM leader_handoffs').fetchone()[0], 1)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))


if __name__=='__main__':
    unittest.main()
