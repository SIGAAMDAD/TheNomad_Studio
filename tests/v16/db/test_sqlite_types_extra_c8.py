"""Lossless wire-type conversions through real SQLite storage."""
from decimal import Decimal
from pathlib import Path
import hashlib
import sqlite3
import tempfile
import unittest

from studio_v16.db.sqlite.connection import ConnectionOwner
from studio_v16.db.sqlite.types import canonical, epoch_us, integer, money_units, parse_json, path_scope, utc_string


@unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), 'SQLite >=3.45 required')
class SQLiteTypesExtraC8(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = ConnectionOwner(Path(self.tmp.name) / 'types.db')
        self.addCleanup(self.owner.close)
        with self.owner.write() as (db, _):
            db.execute('CREATE TABLE values_test (timestamp INTEGER, generation INTEGER, data TEXT, paths TEXT, money INTEGER) STRICT')

    def test_types_roundtrip_without_loss(self):
        stamp = '2026-10-27T23:59:59.123456Z'
        value = {'z': [3, {'title': '한글'}], 'a': 1}
        text = canonical(value)
        scopes = path_scope(['tests/v16/', 'studio_v16/db/', 'tests/v16/'])
        amount = money_units(Decimal('123456.7891'))
        with self.owner.write() as (db, _):
            db.execute('INSERT INTO values_test VALUES (?, ?, ?, ?, ?)',
                       (epoch_us(stamp), integer((1 << 63) - 1), text, scopes, amount))
        with self.owner.connection(readonly=True) as db:
            timestamp, generation, stored, stored_paths, money = db.execute('SELECT * FROM values_test').fetchone()
        self.assertEqual(utc_string(timestamp), stamp)
        self.assertEqual(generation, (1 << 63) - 1)
        self.assertEqual(parse_json(stored), value)
        self.assertEqual(hashlib.sha256(stored.encode()).digest(), hashlib.sha256(text.encode()).digest())
        self.assertEqual(parse_json(stored_paths), ['studio_v16/db/', 'tests/v16/'])
        self.assertEqual(Decimal(money) / Decimal(10000), Decimal('123456.7891'))

    def test_invalid_money_is_not_silently_rounded(self):
        for value in (0.1, True, '-0.1', '0.00001', 'NaN', 'Infinity', '100000000000000'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money_units(value)

    def test_invalid_timestamps_integer_and_json_are_rejected(self):
        for value in ('2026-01-01T00:00:00',):
            with self.subTest(value=value), self.assertRaises(ValueError):
                epoch_us(value)
        for value in (True, 1 << 63, -(1 << 63) - 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                integer(value)
        for value in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_json(value)

    def test_noncanonical_scopes_rejected(self):
        for paths in ([], ['../secret/'], ['studio_v16/db'], ['/etc/']):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                path_scope(paths)


if __name__ == '__main__':
    unittest.main()
