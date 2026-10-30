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
            connection.execute('CREATE TABLE audit_events(n INTEGER)')
            connection.execute("CREATE TRIGGER sample_audit AFTER INSERT ON sample "
                               "BEGIN INSERT INTO audit_events(n) VALUES(1); END")
            self.assertEqual(tx.change('INSERT INTO sample(key,value) VALUES (?,?)',('one',3)),1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM audit_events').fetchone(),(1,))
            self.assertEqual(tx.one('SELECT key,value FROM sample WHERE key=?',('one',)),
                             {'key':'one','value':3})
            self.assertEqual(tx.all('SELECT key,value FROM sample ORDER BY key'),
                             [{'key':'one','value':3}])
        finally:
            connection.close()


    def test_empty_named_query_and_absent_get_on_actual_apsw(self):
        with tempfile.TemporaryDirectory() as root:
            store = ScopedStore(Path(root) / "empty.sqlite", fresh=True)
            m = store.bind_domain("WG-M")
            a = store.bind_domain("WG-A")
            store.seal_bindings()
            ctx = {"principal_id":"trusted", "issuer":"configured",
                   "allowed_project_ids":["p"], "trusted_proof_ref":"proof"}
            try:
                with store.begin(actor=ctx,project_id="p",request_id="bootstrap",domain_owner=m) as tx:
                    tx.execute_named("projects.insert",values={
                        "name":"project","charter_digest":"digest","state":"active"})
                with store.begin(actor=ctx,project_id="p",request_id="read",domain_owner=a) as tx:
                    self.assertEqual(tx.select_named("bibles.scan"), [])
                    self.assertIsNone(tx.select_named("bibles.get",keys={"bible_id":"missing"}))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
