"""TX-02: bounded SQLite writer admission with durable inbox ordering.

All operations use the real SQLite owner and addressed inbox schema.
"""
from threading import BoundedSemaphore
import sqlite3
import unittest

from studio_v16.db.sqlite.connection import ConnectionOwner, Unavailable
from studio_v16.db.store import Denied
from studio_v16.db.sqlite.store import SQLiteStore
from studio_v16.communications.service import CoordinationService
from tests.v16.db import test_sqlite_c8 as fixture


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >= 3.45 required')
class SQLiteBackpressureC8(unittest.TestCase):
    setUp = fixture.SQLiteC8Tests.setUp
    _coord = fixture.SQLiteC8Tests._coord

    def test_full_writer_queue_denies_without_partial_event_or_journal_loss(self):
        coord, sender, recipient = self._coord()
        # Replace only the admission semaphore in this private fixture; emulate
        # a fully occupied production queue with a real open write transaction.
        self.owner._queue = BoundedSemaphore(1)
        with self.owner.write() as (db, _):
            with self.assertRaisesRegex(Unavailable, 'SQLITE_QUEUE_FULL'):
                coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender)
            with self.assertRaisesRegex(Unavailable, 'SQLITE_QUEUE_FULL'):
                coord.inbox('design', recipient)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_events').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_deliveries').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM event_journal').fetchone()[0], 2)
        one = coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender)
        self.assertEqual(coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender), one)
        with self.owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_events').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM event_journal').fetchone()[0], 2)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_order_and_cursors_survive_backpressure_retry_and_reconnect(self):
        coord, sender, recipient = self._coord()
        self.owner._queue = BoundedSemaphore(1)
        with self.owner.write() as (_db, _):
            with self.assertRaisesRegex(Unavailable, 'SQLITE_QUEUE_FULL'):
                coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender)
        first = coord.route_event('e1', 'design', 'recipient', {'task':'one'}, sender)
        second = coord.route_event('e2', 'design', 'recipient', {'task':'two'}, sender)
        delivered = coord.inbox('design', recipient)
        self.assertEqual([r['event_id'] for r in delivered], ['e1','e2'])
        self.assertEqual([r['sequence'] for r in delivered], [first, second])
        with self.assertRaises(Denied):
            coord.ack('design', second, recipient)
        self.assertEqual(coord.ack('design', first, recipient), first)
        self.assertEqual(coord.ack('design', first, recipient), first)
        path, policy = self.owner.path, coord._policy
        self.owner.close()
        resumed_owner = ConnectionOwner(path, queue_limit=1)
        self.addCleanup(resumed_owner.close)
        resumed = CoordinationService(SQLiteStore(resumed_owner), policy=policy)
        self.assertEqual([r['event_id'] for r in resumed.inbox('design', recipient)], ['e2'])
        self.assertEqual(resumed.ack('design', second, recipient), second)
        self.assertEqual(resumed.inbox('design', recipient), [])
        with resumed_owner.connection(readonly=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_events').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_deliveries').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM event_journal').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT last_sequence FROM department_inbox_cursors').fetchone()[0], second)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))


if __name__ == '__main__':
    unittest.main()
