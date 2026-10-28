"""Replay, ordering and reconnect tests using a real SQLite inbox."""
import sqlite3
import unittest

from studio_v16.communications.service import CoordinationService
from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.db.store import Denied
from tests.v16.db import test_sqlite_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteInboxReplayC8(unittest.TestCase):
    setUp = fixture.SQLiteC8Tests.setUp
    _coord = fixture.SQLiteC8Tests._coord

    def test_duplicate_delivery_ordered_ack_and_reconnect(self):
        coord, sender, recipient = self._coord()
        first = coord.route_event('e1', 'design', 'recipient', {'task': 'one'}, sender)
        second = coord.route_event('e2', 'design', 'recipient', {'task': 'two'}, sender)
        self.assertEqual(coord.route_event('e1', 'design', 'recipient', {'task': 'one'}, sender), first)
        self.assertEqual([row['event_id'] for row in coord.inbox('design', recipient)], ['e1', 'e2'])
        with self.assertRaises(Denied):
            coord.ack('design', second, recipient)
        self.assertEqual(coord.ack('design', first, recipient), first)
        self.assertEqual(coord.ack('design', first, recipient), first)
        path, policy = self.owner.path, coord._policy
        self.owner.close()
        owner = ConnectionOwner(path)
        self.addCleanup(owner.close)
        resumed = CoordinationService(SQLiteStore(owner), policy=policy)
        self.assertEqual(resumed.ack('design', first, recipient), first)
        self.assertEqual([row['event_id'] for row in resumed.inbox('design', recipient)], ['e2'])
        self.assertEqual(resumed.ack('design', second, recipient), second)
        self.assertEqual(resumed.ack('design', first, recipient), second)
        self.assertEqual(resumed.inbox('design', recipient), [])
        with owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_events').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_deliveries').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT last_sequence FROM department_inbox_cursors').fetchone()[0], second)


if __name__ == '__main__':
    unittest.main()
