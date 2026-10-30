"""Independent explicit APSW runtime gate, skipped if wheel unavailable."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import apsw
except ImportError:
    apsw = None

from studio.storage.engine import ScopedStore
from studio.storage.errors import Conflict, Forbidden


@unittest.skipUnless(apsw is not None, "NOT_RUN: APSW package is unavailable in this environment")
class TestActualAPSW(unittest.TestCase):
    def test_create_fresh_and_reopen_foreign_keys_wal(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "studio.sqlite"
            store = ScopedStore(path, fresh=True)
            m = store.bind_domain("WG-M")
            a = store.bind_domain("WG-A")
            store.seal_bindings()
            ctx = {"principal_id":"human", "issuer":"trusted", "allowed_project_ids":["p1"],"trusted_proof_ref":"verified"}
            with store.begin(actor=ctx,project_id="p1",request_id="create",domain_owner=m) as t:
                t.execute_named("projects.insert", values={"name":"one", "charter_digest":"charter", "state":"active"})
            with store.begin(actor=ctx,project_id="p1",request_id="bible",domain_owner=a) as t:
                t.execute_named("bibles.insert",values={"bible_id":"b","kind":"design","title":"b"})
            self.assertEqual(store._conn.execute("PRAGMA foreign_keys").fetchone()[0],1)
            self.assertEqual(store._conn.execute("PRAGMA journal_mode").fetchone()[0],"wal")
            with self.assertRaises(Conflict):
                # Cannot install fresh schema into an existing initialized database.
                ScopedStore(path,fresh=True)
            store.close()
            reopened = ScopedStore(path)
            reopened.close()


    def test_empty_apsw_selects_do_not_fail_description_lookup(self):
        from studio.storage.workforce import WorkforceTransaction
        connection = apsw.Connection(':memory:')
        try:
            connection.execute('CREATE TABLE sample(key TEXT PRIMARY KEY,value INTEGER)')
            tx = WorkforceTransaction(connection)
            self.assertIsNone(tx.one('SELECT key,value FROM sample WHERE key=?',('missing',)))
            self.assertEqual(tx.all('SELECT key,value FROM sample WHERE key=?',('missing',)),[])
            tx.change('INSERT INTO sample(key,value) VALUES (?,?)',('one',3))
            self.assertEqual(tx.one('SELECT key,value FROM sample WHERE key=?',('one',)),
                             {'key':'one','value':3})
            self.assertEqual(tx.all('SELECT key,value FROM sample ORDER BY key'),
                             [{'key':'one','value':3}])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
