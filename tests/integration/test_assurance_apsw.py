"""E operations use the actual C APSW journal with atomic event fencing."""
import pytest
apsw = pytest.importorskip("apsw")

from studio.assurance.service import AssuranceError
from studio.storage.assurance import AssuranceAPSWStore
from studio.storage.workforce import WorkforceStorage


def test_qa_transaction_event_replay_and_isolation(tmp_path):
    writer = WorkforceStorage.open(tmp_path / "studio.sqlite", fresh=True)
    try:
        conn = writer._connection
        conn.execute("INSERT INTO principals VALUES ('person','configured','person','human')")
        for project in ("p1", "p2"):
            conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,'active')",
                         (project, project, "digest"))
            conn.execute("INSERT INTO project_grants VALUES(?,?,'member')", (project, "person"))
        store = AssuranceAPSWStore(writer)
        assert store.read(project_id="p1", query=lambda tx: tx.one(
            "SELECT kind FROM principals WHERE principal_id=?", ("person",))) == {"kind": "human"}
        with pytest.raises(AssuranceError, match="project fence"):
            store.read(project_id="p1", query=lambda tx: tx.one(
                "SELECT auth_issuer FROM principals WHERE principal_id=?", ("person",)))

        def create(tx):
            assert tx.one("SELECT 1 AS allowed FROM project_grants WHERE project_id=? AND principal_id=?",
                          ("p1", "person")) is not None
            tx.execute(
                "INSERT INTO oversight_cases(project_id,case_id,subject_id,state,evidence_digest)"
                " VALUES(?,?,?,'open',?)", ("p1", "case", "subject", "d"*64))
            tx.event(event_type="oversight.opened", subject_id="case", payload_digest="d"*64)
            return {"case_id": "case", "state": "open"}

        def run(op="openOversightCase", digest="a"*64, cmd=create):
            return store.run(project_id="p1", domain="E", operation=op,
                             principal_id="person", request_id="one",
                             request_digest=digest, command=cmd)

        assert run() == {"case_id": "case", "state": "open"}
        assert run(cmd=lambda _: pytest.fail("duplicate executed"))["state"] == "open"
        with pytest.raises(AssuranceError, match="different content"):
            run(digest="b"*64, cmd=lambda _: pytest.fail("changed request executed"))
        with pytest.raises(AssuranceError, match="different content"):
            run(op="resolveOversightCase", cmd=lambda _: pytest.fail("cross-command executed"))
        assert store.read(project_id="p1", query=lambda tx: tx.all(
            "SELECT case_id FROM oversight_cases WHERE project_id=?", ("p1",))) == [{"case_id": "case"}]
        assert store.read(project_id="p2", query=lambda tx: tx.all(
            "SELECT case_id FROM oversight_cases WHERE project_id=?", ("p2",))) == []
        assert writer.read("p1", lambda tx: tx.one(
            "SELECT event_type,actor_principal_id FROM audit_events WHERE project_id=?",
            ("p1",))) == {"event_type": "oversight.opened", "actor_principal_id": "person"}
        with pytest.raises(AssuranceError, match="project"):
            store.read(project_id="p1", query=lambda tx: tx.all(
                "SELECT case_id FROM oversight_cases WHERE project_id=?", ("p2",)))
        with pytest.raises(AssuranceError, match="authority"):
            store.run(project_id="p1", domain="G", operation="openOversightCase",
                      principal_id="person", request_id="two", request_digest="a"*64,
                      command=lambda _: {})
        with pytest.raises(AssuranceError, match="another domain"):
            store.run(project_id="p1", domain="E", operation="new",
                      principal_id="person", request_id="three", request_digest="a"*64,
                      command=lambda tx: (tx.execute("UPDATE projects SET name=? WHERE project_id=?",
                                                     ("bad", "p1")), {} )[1])
    finally:
        writer.close()


def test_failure_rolls_back_qa_and_event(tmp_path):
    writer = WorkforceStorage.open(tmp_path / "rollback.sqlite", fresh=True)
    try:
        conn = writer._connection
        conn.execute("INSERT INTO principals VALUES('person','configured','person','human')")
        conn.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
                     "VALUES('p','Project','digest','active')")
        store = AssuranceAPSWStore(writer)
        def command(tx):
            tx.execute("INSERT INTO oversight_cases(project_id,case_id,subject_id,state,evidence_digest)"
                       " VALUES(?,?,?,'open',?)", ("p","case","item","d"*64))
            tx.event(event_type="oversight.opened", subject_id="case", payload_digest="d"*64)
            raise AssuranceError("conflict","forced rollback")
        with pytest.raises(AssuranceError,match="forced rollback"):
            store.run(project_id="p",domain="E",operation="openOversightCase",
                      principal_id="person",request_id="req",request_digest="a"*64,command=command)
        assert writer.read("p",lambda tx:tx.one(
            "SELECT 1 AS n FROM oversight_cases WHERE project_id=?", ("p",))) is None
        assert writer.read("p",lambda tx:tx.one(
            "SELECT 1 AS n FROM audit_events WHERE project_id=?", ("p",))) is None
    finally:
        writer.close()
