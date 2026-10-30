"""Real SQLite reference-oracle tests of C's SQL guard; NOT APSW runtime proof."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.storage.blobs import ContentAddressedStore
from studio.storage.backup import BackupDescriptor, BackupManager
from studio.storage.cursors import RelayCursorJournal
from studio.storage.effects import Effect, EffectJournal
from studio.storage.engine import ScopedStore
from studio.storage.errors import Conflict, Forbidden, Invalid, Missing, UnknownEffect
from studio.storage.idempotency import BootstrapJournal, IdempotencyJournal, canonical_json_bytes, request_digest
from studio.storage.queries import ProductionQueries
from studio.storage.registry import NamedRegistry
from studio.storage.schema import SCHEMA_DIR, schema_bytes, table_owners
from studio.storage.states import StateMachines


class OracleCursor:
    def __init__(self, cursor):
        self.cur = cursor

    def __iter__(self):
        return iter(self.cur)

    def fetchone(self):
        return self.cur.fetchone()

    def getdescription(self):
        return self.cur.description


class OracleConnection:
    """Offline sqlite3 protocol adapter; never used in production."""
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(schema_bytes().decode())

    def getautocommit(self):
        return not self.db.in_transaction

    def execute(self, statement, args=()):
        return OracleCursor(self.db.execute(statement, args))

    def close(self):
        self.db.close()


def oracle_store():
    store = ScopedStore.__new__(ScopedStore)
    store._mutex = threading.RLock()
    store._domain_key = object()
    store._bindings_open = True
    store._bindings = {}
    store._conn = OracleConnection()
    store._registry = NamedRegistry(store._conn)
    store._machines = StateMachines()
    for owner in sorted(set(table_owners().values())):
        store.bind_domain(owner)
    store.seal_bindings()
    return store


def actor(projects=("p1",)):
    return {"principal_id": "human", "issuer": "trusted", "allowed_project_ids": list(projects), "trusted_proof_ref": "verified-auth-proof"}


class TestStorageOracle(unittest.TestCase):
    def setUp(self):
        self.store = oracle_store()
        self.m = self.store._bindings["WG-M"]
        self.c = self.store._bindings["WG-C"]
        self.a = self.store._bindings["WG-A"]
        self.b = self.store._bindings["WG-B"]
        self.d = self.store._bindings["WG-D"]
        self.g = self.store._bindings["WG-G"]
        self.i = self.store._bindings["WG-I"]
        self.j = self.store._bindings["WG-J"]
        for project in ("p1", "p2"):
            with self.tx(self.m, project, "init-"+project) as t:
                t.execute_named("projects.insert", values={"name": project, "charter_digest": "charter-"+project, "state": "active"})
        with self.tx(self.j, "p1", "identity") as t:
            t.execute_named("principals.insert", values={"principal_id":"human","auth_issuer":"trusted","auth_subject":"human","kind":"human"})

    def tearDown(self):
        self.store.close()

    def tx(self, binding, project="p1", req="test", who=None):
        return self.store.begin(actor=actor((project,)), project_id=project, request_id=req, domain_owner=binding)

    def test_schema_digest_and_table_ownership(self):
        self.assertEqual(hashlib.sha256(schema_bytes()).hexdigest(), (SCHEMA_DIR/"schema.sha256").read_text().split()[0])
        self.assertEqual(len(table_owners()), len({r[0] for r in self.store._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}))
        self.assertEqual(self.store._conn.execute("PRAGMA foreign_keys").fetchone(), (1,))
        self.assertEqual(self.store._conn.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assertGreater(self.store._conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0], 50)

    def test_project_access_and_guessed_id_denied(self):
        with self.assertRaises(Forbidden):
            self.store.begin(actor=actor(("p1",)), project_id="p2", request_id="x", domain_owner=self.a)
        with self.tx(self.m) as t:
            with self.assertRaises(Forbidden):
                t.select_named("projects.scan", filters={"project_id":"p2"})
            self.assertEqual(t.select_named("projects.get",keys={})["project_id"],"p1")
        with self.tx(self.a) as t:
            t.execute_named("bibles.insert", values={"bible_id":"shared","kind":"design","title":"p1 bible"})
            with self.assertRaises(Forbidden):
                t.execute_named("bibles.insert", values={"project_id":"p2","bible_id":"other","kind":"technical","title":"wrong"})
        with self.tx(self.a,"p2") as t:
            self.assertIsNone(t.select_named("bibles.get", keys={"bible_id":"shared"}))
        with self.tx(self.b) as t:
            with self.assertRaises(Forbidden):
                t.select_named("bibles.get", keys={"bible_id":"shared"})
        with self.assertRaises(Forbidden):
            self.store.begin(actor=actor(), project_id="p1", request_id="x", domain_owner="WG-A")

    def test_scope_binds_all_reads_and_aggregations(self):
        for project in ("p1", "p2"):
            with self.tx(self.a,project) as t:
                t.execute_named("bibles.insert", values={"bible_id":"same","kind":"design","title":project})
        with self.tx(self.a) as t:
            self.assertEqual(t.select_named("bibles.count"),1)
            self.assertEqual(len(t.select_named("bibles.scan",limit=1)),1)
            self.assertEqual(t.select_named("bibles.get", keys={"bible_id":"same"})["title"],"p1")
            with self.assertRaises(Invalid):
                t.select_named("bibles.scan",limit=100000)
            with self.assertRaises(Invalid):
                t.select_named("bibles.sum",column="title")
            with self.assertRaises(Invalid):
                t.select_named("bibles.scan",after=["too","many","values"])

    def test_immutable_evidence_is_protected_and_raw_sql_rejected(self):
        with self.tx(self.a) as t:
            with self.assertRaises(Forbidden):
                t.execute_named("owner_decisions.delete",keys={"decision_id":"x"})
            with self.assertRaises(Forbidden):
                t.execute_named("bibles.raw_sql",values={})
            with self.assertRaises(Invalid):
                t.execute_named("bibles.insert",values={"bible_id":"b","kind":"design","title":"x", "sql":"DROP TABLE projects"})
            with self.assertRaises(Forbidden):
                t.execute_named("projects.insert",values={"name":"bad"})
        with self.tx(self.i) as t:
            with self.assertRaises(Forbidden):
                t.execute_named("relay_sync_cursors.update",keys={"channel_id":"x"},values={"observed_ref_sha":"evil"})

    def test_transaction_rollback_and_closed_state(self):
        try:
            with self.tx(self.a) as t:
                t.execute_named("bibles.insert",values={"bible_id":"roll","kind":"design","title":"roll"})
                raise RuntimeError("intentional rollback")
        except RuntimeError:
            pass
        with self.tx(self.a) as t:
            self.assertIsNone(t.select_named("bibles.get",keys={"bible_id":"roll"}))
            t.execute_named("bibles.insert",values={"bible_id":"once","kind":"design","title":"once"})
        with self.assertRaises(Conflict):
            t.commit()

    def test_idempotency_persists_and_digest_conflicts(self):
        with tempfile.TemporaryDirectory() as root:
            bs=ContentAddressedStore(root)
            result=b'{"task":"recorded"}'
            saved=bs.put(result)
            with self.tx(self.c,req="same") as t:
                first=IdempotencyJournal.claim(t,"task.create",{"a":1,"b":2})
                self.assertTrue(first.first_claim)
                IdempotencyJournal.finish(t,"task.create",saved.digest)
            with self.tx(self.c,req="same") as t:
                self.assertEqual(IdempotencyJournal.replay_result(t,"task.create",{"b":2,"a":1},bs),result)
                with self.assertRaises(Conflict):
                    IdempotencyJournal.claim(t,"task.create",{"a":999})
            with self.tx(self.c,req="different") as t:
                IdempotencyJournal.claim(t,"task.create",{"a":3})
                IdempotencyJournal.mark_unknown(t,"task.create")
            with self.tx(self.c,req="different") as t:
                with self.assertRaises(Conflict):
                    IdempotencyJournal.replay_result(t,"task.create",{"a":3},bs)
            self.assertEqual(request_digest({"b":2,"a":1}),request_digest({"a":1,"b":2}))

    def test_bootstrap_request_identity_and_exact_project_result(self):
        with self.tx(self.m,req="new-proj") as t:
            self.assertTrue(BootstrapJournal.claim(t,{"project":"p1"}).first_claim)
            with self.assertRaises(Forbidden):
                BootstrapJournal.finish(t,"p2")
            BootstrapJournal.finish(t,"p1")
        with self.tx(self.m,req="new-proj") as t:
            replay=BootstrapJournal.claim(t,{"project":"p1"})
            self.assertFalse(replay.first_claim)
            self.assertEqual(replay.result_project_id,"p1")
            with self.assertRaises(Conflict):
                BootstrapJournal.claim(t,{"project":"p2"})
        with self.tx(self.a,req="illegal-bootstrap") as t:
            with self.assertRaises(Forbidden):
                BootstrapJournal.claim(t,{"x":1})

    def test_backup_manifest_inventory_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            blobs=ContentAddressedStore(tmp)
            database=blobs.put(b"offline db image test payload")
            obj=blobs.put(b"example game artifact")
            schema_sha=hashlib.sha256(schema_bytes()).hexdigest()
            raw={"format":"nomad-studio-r5-whole-studio-backup","schema_sha256":schema_sha,
                 "database_sha256":database.digest,"database_size":database.byte_size,
                 "blobs":[{"digest":obj.digest,"size":obj.byte_size}]}
            manifest=blobs.put(canonical_json_bytes(raw))
            label=hashlib.sha256((database.digest+":"+manifest.digest).encode()).hexdigest()
            descriptor=BackupDescriptor(label,database.digest,manifest.digest,schema_sha,1)
            manager=BackupManager(blobs)
            self.assertEqual(manager.verify(descriptor)["blobs"],raw["blobs"])
            with self.assertRaises(Invalid):
                manager.verify(BackupDescriptor("0"*64,database.digest,manifest.digest,schema_sha,1))
            blobs._file(obj.digest).write_bytes(b"corrupt")
            with self.assertRaises(Invalid):
                manager.verify(descriptor)

    def test_effect_outbox_exactness(self):
        e=Effect("fx1","project","p1","relay.publish","a"*64,None,"github",False,None)
        with self.tx(self.i,req="fx-request") as t:
            EffectJournal.stage(t,e)
        with self.tx(self.c) as t:
            self.assertEqual(len(EffectJournal.pending(t)),1)
            EffectJournal.dispatch(t,"fx1")
        with self.tx(self.c) as t:
            with self.assertRaises(UnknownEffect):
                EffectJournal.record_readback(t,effect_id="fx1",expected_digest=e.effect_digest,observed_digest=e.effect_digest,remote_operation_id="commit",trusted_verified=False)
            with self.assertRaises(Conflict):
                EffectJournal.record_readback(t,effect_id="fx1",expected_digest="bad",observed_digest="bad",remote_operation_id="commit",trusted_verified=True)
            EffectJournal.record_readback(t,effect_id="fx1",expected_digest=e.effect_digest,observed_digest=e.effect_digest,remote_operation_id="commit",trusted_verified=True)
        with self.tx(self.c) as t:
            self.assertEqual(EffectJournal.pending(t),[])
        with self.tx(self.i,req="fx2-request") as t:
            with self.assertRaises(Forbidden):
                EffectJournal.stage(t,Effect("fx2","project","p1","release","digest",None,"git",True,None))

    def test_content_addresses_and_project_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bs=ContentAddressedStore(tmp)
            with self.tx(self.g) as t:
                with self.assertRaises(Forbidden):
                    t.execute_named("blobs.insert",values={"content_digest":"fake","byte_size":1,"store_key":"fake","durability":"verified"})
                item=bs.put_project(t,b"TEST BYTES",purpose="artifact-content")
            with self.tx(self.g) as t:
                self.assertEqual(bs.read_project(t,item.digest,purpose="artifact-content"),b"TEST BYTES")
            with self.tx(self.g,"p2") as t:
                with self.assertRaises(Forbidden):
                    bs.read_project(t,item.digest)
                self.assertIsNone(t.select_named("blobs.get",keys={"content_digest":item.digest}))
            path=bs._file(item.digest)
            path.write_bytes(b"tampered")
            with self.assertRaises(Invalid):
                bs.read_digest(item.digest)

    def _seed_task_graph(self):
        """Reference-oracle fixture using frozen SQL and actual constraints."""
        d = self.store._conn.db
        d.execute("INSERT INTO project_grants VALUES ('p1','human','owner')")
        for b in ("bd", "bt"):
            d.execute("INSERT INTO blobs VALUES (?,?,?,?)", (b, 1, "ref/"+b, "verified"))
            for purpose in ("bible-content", "bible-manifest"):
                d.execute("INSERT INTO project_blobs VALUES (?,?,?)", ("p1", b, purpose))
        for bible, kind, edition, digest in (("design", "design", "ed", "bd"), ("technical", "technical", "et", "bt")):
            d.execute("INSERT INTO bibles VALUES (?,?,?,?)", ("p1", bible, kind, bible))
            decision, fx = "decision-"+edition, "fx-"+edition
            d.execute("INSERT INTO decision_proposals(project_id,proposal_id,subject_type,subject_id,action,effect_digest,requester_principal_id,created_at) VALUES (?,?,?,?,?,?,?,?)", ("p1",decision,"bible",bible,"bible.publish",fx,"human","now"))
            d.execute("INSERT INTO owner_decisions VALUES (?,?,?,?,?,?,?,?,?,?)", ("p1",decision,"bible",bible,"bible.publish",fx,None,"human","proof","approved"))
            d.execute("INSERT INTO effect_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("p1", "eff-"+edition,"bible",bible,"bible.publish",fx,None,decision,1,"prepared","provider",None,"req-"+edition))
            d.execute("INSERT INTO approval_consumptions VALUES (?,?,?,?)", ("p1",decision,"eff-"+edition,"now"))
            d.execute("INSERT INTO bible_editions VALUES (?,?,?,?,?,?,?,?,?)", ("p1",bible,edition,kind,digest,digest,decision,fx,"now"))
        d.execute("INSERT INTO canon_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", ("p1","snap","design","ed","design","technical","et","technical","policy","manifest"))
        d.execute("INSERT INTO cabals VALUES (?,?,?,?,?)", ("p1","cabal","test","charter","active"))
        d.execute("INSERT INTO features(project_id,feature_id,brief_digest,snapshot_id,state) VALUES (?,?,?,?,?)", ("p1","feat","brief","snap","planned"))
        for tid,state,priority in (("A","ready",5),("B","accepted",3),("C","ready",2),("D","ready",10)):
            d.execute("INSERT INTO tasks(project_id,task_id,feature_id,cabal_id,snapshot_id,acceptance_digest,state,priority) VALUES (?,?,?,?,?,?,?,?)", ("p1",tid,"feat","cabal","snap","accept",state,priority))
        d.execute("INSERT INTO task_dependencies VALUES (?,?,?)", ("p1","A","B"))
        d.execute("INSERT INTO task_dependencies VALUES (?,?,?)", ("p1","B","C"))
        d.execute("INSERT INTO task_dependencies VALUES (?,?,?)", ("p1","D","C"))

    def test_ready_queue_and_cycle_detection_are_bounded(self):
        self._seed_task_graph()
        with self.tx(self.d) as t:
            self.assertTrue(ProductionQueries.would_create_dependency_cycle(t,task_id="C",depends_on_task_id="A"))
            self.assertFalse(ProductionQueries.would_create_dependency_cycle(t,task_id="A",depends_on_task_id="C"))
            with self.assertRaises(Invalid):
                ProductionQueries.would_create_dependency_cycle(t,task_id="C",depends_on_task_id="A",maximum_reachable=1)
            self.assertEqual([x["task_id"] for x in ProductionQueries.ready_tasks(t,limit=20)], ["A", "C"])
            self.assertEqual(ProductionQueries.task_counts(t).ready,3)
            self.assertEqual(t.select_named("tasks.sum",column="priority"),20)
            with self.assertRaises(Invalid):
                ProductionQueries.ready_tasks(t,limit=251)
        with self.tx(self.b) as t:
            with self.assertRaises(Forbidden):
                ProductionQueries.ready_tasks(t)

    def test_bounded_page_and_index_usage_fixture(self):
        # Representative high-cardinality fixture: page size is bounded even
        # when the scoped table has over a thousand rows.
        with self.tx(self.a) as t:
            for number in range(1000):
                t.execute_named("bibles.insert",values={"bible_id":f"b{number:05d}","kind":"design","title":"v"})
            self.assertEqual(t.select_named("bibles.count"),1000)
            first=t.select_named("bibles.scan",limit=25)
            self.assertEqual(len(first),25)
            after=(first[-1]["project_id"],first[-1]["bible_id"])
            second=t.select_named("bibles.scan",limit=25,after=after)
            self.assertEqual(len(second),25)
            self.assertTrue(set(x["bible_id"] for x in first).isdisjoint(x["bible_id"] for x in second))
        self._seed_task_graph()
        d=self.store._conn.db
        task_plan=list(d.execute("EXPLAIN QUERY PLAN SELECT * FROM tasks t WHERE project_id=? AND state='ready' ORDER BY priority DESC,task_id LIMIT ?", ("p1",25)))
        self.assertTrue(any("idx_tasks_ready" in row[-1] for row in task_plan),task_plan)
        graph_plan=list(d.execute("EXPLAIN QUERY PLAN SELECT depends_on_task_id FROM task_dependencies WHERE project_id=? AND task_id=?",("p1","A")))
        self.assertTrue(any("sqlite_autoindex_task_dependencies" in row[-1] for row in graph_plan),graph_plan)

    def test_frozen_machine_denies_illegal_state_updates(self):
        self._seed_task_graph()
        with self.tx(self.d) as t:
            with self.assertRaises(Conflict):
                t.execute_named("tasks.update",keys={"task_id":"A"},values={"state":"accepted"},expected_revision=1)
            with self.assertRaises(Conflict):
                t.execute_named("tasks.update",keys={"task_id":"A"},values={"state":"ready"},expected_revision=1)
            t.execute_named("tasks.update",keys={"task_id":"A"},values={"state":"leased"},expected_revision=1)
            with self.assertRaises(Conflict):
                t.execute_named("tasks.update",keys={"task_id":"A"},values={"state":"accepted"},expected_revision=2)
            t.execute_named("tasks.update",keys={"task_id":"A"},values={"state":"running"},expected_revision=2)
            with self.assertRaises(Invalid):
                t.execute_named("tasks.update",keys={"task_id":"A"},values={"state":"unlisted"},expected_revision=3)

    def test_fenced_lease_and_optimistic_task_revision(self):
        self._seed_task_graph()
        with self.tx(self.d) as t:
            t.execute_named("leases.insert",values={"task_id":"A","generation":2,"worker_id":"worker","expires_at":"tomorrow","status":"active"})
            t.cas_lease(task_id="A",generation=2,old_status="active",new_status="released")
            with self.assertRaises(Conflict):
                t.cas_lease(task_id="A",generation=1,old_status="active",new_status="expired")
            with self.assertRaises(Conflict):
                t.cas_lease(task_id="A",generation=2,old_status="active",new_status="expired")
            t.execute_named("tasks.update",keys={"task_id":"A"},values={"priority":7},expected_revision=1)
            self.assertEqual(t.select_named("tasks.get",keys={"task_id":"A"})["revision"],2)
            with self.assertRaises(Conflict):
                t.execute_named("tasks.update",keys={"task_id":"A"},values={"priority":8},expected_revision=1)
            with self.assertRaises(Invalid):
                t.execute_named("tasks.update",keys={"task_id":"A"},values={"priority":9})

    def test_relay_cursor_no_unverified_advance(self):
        with self.tx(self.g) as t:
            t.execute_named("project_repositories.insert",values={"repo_id":"r","provider_key":"github","remote_ref":"main"})
        with self.tx(self.i) as t:
            t.execute_named("relay_channels.insert",values={"channel_id":"c","repo_id":"r","git_ref":"main","root_path":"relays/","enabled":1})
        with self.tx(self.i) as t:
            self.assertIsNone(RelayCursorJournal.resume_sha(t,channel_id="c"))
            with self.assertRaises(UnknownEffect):
                RelayCursorJournal.checkpoint(t,channel_id="c",prior_ref_sha=None,verified_ref_sha="head1",observed_at="now",traversal_complete=False,ancestry_verified=True,trusted_readback=True)
            RelayCursorJournal.checkpoint(t,channel_id="c",prior_ref_sha=None,verified_ref_sha="head1",observed_at="now",traversal_complete=True,ancestry_verified=True,trusted_readback=True)
        with self.tx(self.i) as t:
            self.assertEqual(RelayCursorJournal.resume_sha(t,channel_id="c"),"head1")
            with self.assertRaises(Conflict):
                RelayCursorJournal.checkpoint(t,channel_id="c",prior_ref_sha="other",verified_ref_sha="head2",observed_at="now",traversal_complete=True,ancestry_verified=True,trusted_readback=True)
            with self.assertRaises(UnknownEffect):
                RelayCursorJournal.checkpoint(t,channel_id="c",prior_ref_sha="head1",verified_ref_sha="head2",observed_at="now",traversal_complete=True,ancestry_verified=False,trusted_readback=True)


if __name__ == "__main__":
    unittest.main()
