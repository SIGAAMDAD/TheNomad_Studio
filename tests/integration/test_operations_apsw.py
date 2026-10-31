"""F budgets use the production named SQL plans and actual C APSW writer."""
import pytest
apsw = pytest.importorskip("apsw")

from studio.operations.core import Operations, Scope, DomainError
from studio.storage.operations import OperationsAPSWRunner
from studio.storage.workforce import WorkforceStorage


def test_budget_api_durable_revision_replay_and_project_fencing(tmp_path):
    writer = WorkforceStorage.open(tmp_path / "budgets.sqlite", fresh=True)
    try:
        c = writer._connection
        c.execute("INSERT INTO principals VALUES('actor','configured','actor','human')")
        for project in ("p1","p2"):
            c.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES(?,?,?,'active')",
                      (project,project,"digest"))
        runner = OperationsAPSWRunner(writer)
        svc = Operations(runner)
        scope = Scope("p1","actor","verified",frozenset({"p1"}))
        args = {"budget_id":"ci","limit_amount":100,"expected_revision":0,"request_id":"one"}
        first = svc.configure_budget(scope,**args)
        assert first == {"budget_id":"ci","limit_amount":100}
        assert svc.configure_budget(scope,**args) == first
        assert svc.get_budgets(scope)["budgets"][0]["limit_amount"] == 100
        assert c.execute("SELECT event_type,event_seq FROM audit_events "
                         "WHERE project_id='p1'").fetchone() == ("budget.configured",1)
        with pytest.raises(DomainError,match="different content"):
            svc.configure_budget(scope,**{**args,"limit_amount":200})
        updated = svc.configure_budget(scope,budget_id="ci",limit_amount=120,
                                      expected_revision=1,request_id="two")
        assert updated["limit_amount"] == 120
        assert c.execute("SELECT COUNT(*) FROM audit_events "
                         "WHERE project_id='p1'").fetchone() == (2,)
        with pytest.raises(DomainError,match="revision"):
            svc.configure_budget(scope,budget_id="ci",limit_amount=150,
                                 expected_revision=1,request_id="three")
        with pytest.raises(DomainError,match="scope"):
            svc.get_budgets(Scope("p2","actor","verified",frozenset({"p1"})))
        assert writer.read("p2",lambda tx:tx.one(
            "SELECT budget_id FROM budgets WHERE project_id=?",("p2",))) is None
        with pytest.raises(DomainError,match="unregistered"):
            runner.query(scope,lambda tx:tx.select_named("f.nonexistent",{"project_id":"p1"}))
        with pytest.raises(DomainError,match="read transaction"):
            runner.query(scope,lambda tx:tx.execute_named(
                "f.budget.insert",{"project_id":"p1","budget_id":"forbidden",
                                   "resource_key":"ci","limit_amount":10}))
    finally:
        writer.close()
