"""Actual APSW D scheduler boundary enforces project fencing and atomic replay."""
import pytest
apsw = pytest.importorskip("apsw")

from studio.production.service import ProductionFault
from studio.storage.production import ProductionAPSWStorage
from studio.storage.workforce import WorkforceStorage


def test_d_owned_transaction_is_durable_and_no_cross_project_writes(tmp_path):
    writer=WorkforceStorage.open(tmp_path/"d.sqlite",fresh=True)
    try:
        conn=writer._connection
        conn.execute("INSERT INTO principals VALUES('owner','configured','owner','human')")
        for project in ("p1","p2"):
            conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,'active')",
                         (project,project,"digest"))
            conn.execute("INSERT INTO cabals(project_id,cabal_id,name,charter_digest,state) VALUES(?,?,?,'digest','active')",
                         (project,"cabal",project))
        store=ProductionAPSWStorage(writer)
        def insert(tx):
            assert tx.one("SELECT 1 AS n FROM cabals WHERE project_id=? AND cabal_id=?",
                          ("p1","cabal")) == {"n":1}
            tx.change("INSERT INTO features(project_id,feature_id,brief_digest,snapshot_id,"
                      "source_revision_id,revision,state) VALUES(?,?,?,?,?,1,'proposed')",
                      ("p1","feature","d"*64,"snap","source"))
            return {"feature_id":"feature", "state":"proposed"}
        first=store.command("p1","D.submitFeature","r1","a"*64,insert)
        assert first["state"]=="proposed"
        assert store.command("p1","D.submitFeature","r1","a"*64,
                             lambda _:pytest.fail("replayed"))==first
        assert store.read("p1",lambda tx:tx.all(
            "SELECT feature_id FROM features WHERE project_id=?",("p1",)))==[{"feature_id":"feature"}]
        assert store.read("p2",lambda tx:tx.all(
            "SELECT feature_id FROM features WHERE project_id=?",("p2",)))==[]
        with pytest.raises(ProductionFault,match="bound project"):
            store.read("p1",lambda tx:tx.all(
                "SELECT feature_id FROM features WHERE project_id=?",("p2",)))
        with pytest.raises(ProductionFault,match="unowned"):
            store.command("p1","D.planFeature","r2","a"*64,
                lambda tx:(tx.change("UPDATE projects SET name=? WHERE project_id=?",
                                     ("crossed","p1")), {})[1])
        with pytest.raises(ProductionFault,match="read transaction"):
            store.read("p1",lambda tx:tx.change(
                "UPDATE tasks SET state=? WHERE project_id=?",("bad","p1")))
        with pytest.raises(ProductionFault,match="command owner"):
            store.command("p1","G.runBuild","r3","a"*64,lambda _: {})
    finally:
        writer.close()
