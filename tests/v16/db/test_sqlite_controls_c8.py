"""C8 manual-response STOP/DRAIN and leadership safety tests on a real DB."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from studio_v16.communications.service import CoordinationService, Scope
from studio_v16.db.store import Denied, GatewayContext
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3,45), 'SQLite >= 3.45 required')
class SQLiteControlsC8(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = ConnectionOwner(Path(self.tmp.name) / 'controls.sqlite')
        self.addCleanup(self.owner.close)
        self.store = SQLiteStore(self.owner)
        self.worker = GatewayContext(principal_id='worker', verified_gateway=True)
        self.leader = GatewayContext(principal_id='leader', verified_gateway=True)
        self.other = GatewayContext(principal_id='other', verified_gateway=True)
        with self.owner.write() as (db, now):
            for pid in ('worker','leader','other'):
                db.execute('INSERT INTO principals(principal_id,issuer,subject,kind,status) VALUES(?,?,?,?,?)',
                           (pid,'fixture',pid,'WORKER','ACTIVE'))
            db.execute('INSERT INTO leader_state(department,principal_id,leader_epoch,updated_at) VALUES(?,?,1,?)',
                       ('design','leader',now))
        self.coord = CoordinationService(self.store, policy={
            'worker': Scope(frozenset({'design'}),frozenset({'RESPONSE'})),
            'leader': Scope(frozenset({'design'}),frozenset({'CONTROL'})),
            'other': Scope(frozenset({'design'}),frozenset({'CONTROL'})),
        })
        self.assertEqual(self.coord.start_response('response-c8','cw-1','design',self.worker),'RUNNING')

    def test_stop_generation_is_fenced_and_immutable(self):
        nonce='c8-stop-nonce-000001'
        self.assertEqual(self.coord.control('response-c8','STOP',0,nonce,1,self.leader),1)
        self.assertEqual(self.coord.control('response-c8','STOP',0,nonce,1,self.leader),1)
        with self.assertRaises(Denied):
            self.coord.control('response-c8','STOP',0,'c8-stop-nonce-000002',1,self.leader)
        with self.assertRaises(Denied):
            self.coord.control('response-c8','CONTINUE',1,'c8-stop-nonce-000003',1,self.leader)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_controls').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT control_epoch FROM response_attempts').fetchone()[0],1)

    def test_worker_observes_stop_and_cannot_observe_another_response(self):
        self.coord.control('response-c8','STOP',0,'c8-stop-nonce-000004',1,self.leader)
        seen=self.coord.observe_control('response-c8',self.worker)
        self.assertEqual(seen['action'],'STOP')
        self.assertEqual(seen['control_epoch'],1)
        self.assertIsNone(self.coord.observe_control('response-c8',self.worker))
        with self.assertRaises(Denied):
            self.coord.observe_control('response-c8',self.other)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_control_observations').fetchone()[0],1)

    def test_nonleader_cannot_issue_controls(self):
        with self.assertRaises(Denied):
            self.coord.control('response-c8','STOP',0,'c8-stop-nonce-000005',1,self.other)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_controls').fetchone()[0],0)

    def test_stale_leader_epoch_denied(self):
        with self.assertRaises(Denied):
            self.coord.control('response-c8','DRAIN',0,'c8-drain-nonce-00001',0,self.leader)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_controls').fetchone()[0],0)

    def test_pause_prevents_unauthorized_continue(self):
        self.assertEqual(self.coord.control('response-c8','PAUSE',0,'c8-pause-nonce-00001',1,self.leader),1)
        with self.assertRaises(Denied):
            self.coord.control('response-c8','CONTINUE',1,'c8-unpause-nonce-0001',1,self.leader)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_controls').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
