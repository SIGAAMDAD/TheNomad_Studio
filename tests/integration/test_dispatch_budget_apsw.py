"""Production D + F reservations, I shifts and C ledger share one APSW write."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
apsw = pytest.importorskip("apsw")

from studio.production import ProductionService, ProductionFault
from studio.storage.production import ProductionAPSWStorage
from studio.storage.reservations import AtomicBudgetReservations
from studio.storage.workforce import WorkforceStorage


def reference():
    path=Path(__file__).resolve().parents[1]/"unit/d/test_production.py"
    spec=importlib.util.spec_from_file_location("_actual_d_fixture",path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def test_real_production_lease_dispatch_and_f_budget_are_atomic(tmp_path):
    writer=WorkforceStorage.open(tmp_path/"d-f.sqlite", fresh=True)
    try:
        m=reference()
        m.fixture_db(writer._connection)
        blobs=m.VerifiedBriefReference()
        d=ProductionService(
            storage=ProductionAPSWStorage(writer),
            authorization=m.ExplicitAuthorization(),briefs=blobs,
            reservations=AtomicBudgetReservations(),
            acceptance=m.VerifiedIndependentQA(),
            progress=m.VerifiedProgressReference(),stop=m.StopSink(),
            clock=lambda: datetime(2026,10,30,tzinfo=timezone.utc),
            lease_seconds=30)
        actor={"principal_id":"owner","issuer":"trusted",
               "allowed_project_ids":["p1"],"trusted_proof_ref":"verified"}
        brief_digest=blobs.add(m.brief())
        item=d.submitFeature(actor,"p1",{
            "request_id":"sf1","brief_digest":brief_digest,
            "snapshot_id":"s1","acceptance_digest":"accept-feature"})
        feature=item["feature_id"]
        assert item==d.submitFeature(actor,"p1",{
            "request_id":"sf1","brief_digest":brief_digest,
            "snapshot_id":"s1","acceptance_digest":"accept-feature"})
        planned=d.planFeature(actor,"p1",feature,{
            "request_id":"pf1","snapshot_id":"s1","expected_revision":1})
        assert len(planned["task_ids"])==2
        task_id=planned["task_ids"][0]
        result=d.dispatchTask(actor,"p1",task_id,{
            "request_id":"lease1","expected_revision":1,"worker_id":"w1"})
        assert result["delivery_state"]=="queued"
        assert result["lease_generation"]==1
        row=writer.read("p1",lambda tx:tx.one(
            "SELECT reserved_amount,spent_amount FROM budgets WHERE project_id=? AND budget_id=?",
            ("p1","cpu-budget")))
        assert row=={"reserved_amount":3,"spent_amount":0}
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT kind,state FROM command_outbox WHERE project_id=? AND command_id=?",
            ("p1",result["command_id"])))=={"kind":"ASSIGN","state":"queued"}
        assert d.dispatchTask(actor,"p1",task_id,{
            "request_id":"lease1","expected_revision":1,"worker_id":"w1"})==result
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM reservations WHERE project_id=?",("p1",)))["n"]==1
        assert writer.read("p2",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM reservations WHERE project_id=?",("p2",)))["n"]==0
        canceled=d.cancelTask(actor,"p1",task_id,{
            "request_id":"cancel1","expected_revision":2,"reason":"human stop"})
        assert canceled["state"]=="canceled"
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT reserved_amount FROM budgets WHERE project_id=? AND budget_id=?",
            ("p1","cpu-budget")))["reserved_amount"]==0
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT state FROM reservations WHERE project_id=?",
            ("p1",)))["state"]=="released"
    finally:
        writer.close()
