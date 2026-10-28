"""Additional authenticated control negative checks on a real SQLite file."""
import sqlite3
import unittest

from studio_v16.db.store import Denied, GatewayContext
from tests.v16.db import test_sqlite_controls_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteControlsExtraC8(unittest.TestCase):
    setUp = fixture.SQLiteControlsC8.setUp

    def test_forged_stop_and_stale_leader_epoch_do_not_pause(self):
        forged = GatewayContext(principal_id='leader', verified_gateway=False)
        for actor, epoch, nonce in ((forged, 1, 'forged-stop-nonce-01'),
                                    (self.other, 1, 'other-stop-nonce-001'),
                                    (self.leader, 0, 'stale-stop-nonce-001')):
            with self.subTest(nonce=nonce), self.assertRaises(Denied):
                self.coord.control('response-c8', 'STOP', 0, nonce, epoch, actor)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_controls').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status,control_epoch FROM response_attempts WHERE response_id='response-c8'").fetchone(), ('RUNNING', 0))


if __name__ == '__main__':
    unittest.main()
