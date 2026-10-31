"""Durable D/A/J/G production evidence ports: authorization and original bytes."""
import hashlib
import pytest

apsw=pytest.importorskip("apsw")
from studio.production.service import ProductionFault
from studio.app.production_evidence import (
    CurrentProductionAuthorizationBuilder, VerifiedProjectBriefsBuilder,
    IndependentQAAcceptance, VerifiedShiftTaskProgress,
)
from studio.storage.artifact_catalog import ArtifactAPSWCatalog
from studio.storage.workforce import WorkforceStorage


def _actor(principal, owner=False):
    return {"principal_id":principal,"issuer":"configured",
            "trusted_proof_ref":"proof","owner_verified":owner,
            "allowed_project_ids":["p1"]}


def test_current_project_grants_and_original_feature_brief(tmp_path):
    db=WorkforceStorage.open(tmp_path/"production.sqlite",fresh=True)
    try:
        c=db._connection
        c.execute("INSERT INTO principals VALUES('owner','configured','owner','human')")
        c.execute("INSERT INTO principals VALUES('member','configured','member','human')")
        for project in ("p1","p2"):
            c.execute("INSERT INTO projects VALUES(?,?,?,1,'active')",
                      (project,project,"digest"))
        c.execute("INSERT INTO project_grants VALUES('p1','owner','owner')")
        c.execute("INSERT INTO project_grants VALUES('p1','member','member')")
        policy=CurrentProductionAuthorizationBuilder(frozenset({"dispatchTask"})).for_writer(db,None)
        policy.require(_actor("owner",True),"p1","dispatchTask")
        policy.require(_actor("member"),"p1","submitFeature")
        with pytest.raises(ProductionFault,match="current production authorization"):
            policy.require(_actor("member"),"p1","dispatchTask")
        with pytest.raises(ProductionFault,match="authenticated"):
            policy.require(_actor("owner",True),"p2","dispatchTask")
        artifact=ArtifactAPSWCatalog(db,tmp_path/"blobs")
        payload=b'{"feature":"real brief"}'
        sha=artifact.put_verified(project_id="p1",content=payload,purpose="feature-brief")
        briefs=VerifiedProjectBriefsBuilder().for_writer(db,tmp_path/"blobs")
        assert briefs.get_verified(project_id="p1",content_sha256=sha)==payload
        with pytest.raises(ProductionFault,match="not verified"):
            briefs.get_verified(project_id="p2",content_sha256=sha)
        c.execute("DELETE FROM project_grants WHERE project_id='p1' AND principal_id='member'")
        with pytest.raises(ProductionFault,match="current production authorization"):
            policy.require(_actor("member"),"p1","submitFeature")
    finally:
        db.close()


def test_unproven_qa_and_worker_progress_cannot_advance_task(tmp_path):
    db=WorkforceStorage.open(tmp_path/"gated.sqlite",fresh=True)
    try:
        c=db._connection
        c.execute("INSERT INTO projects VALUES('p','P','digest',1,'active')")
        with pytest.raises(ProductionFault,match="independent exact"):
            db.read("p",lambda tx:IndependentQAAcceptance().require_accepted(
                tx,project_id="p",task_id="missing",lease_generation=1,
                evidence_digest="e"*64))
        with pytest.raises(ProductionFault,match="verified shift"):
            db.read("p",lambda tx:VerifiedShiftTaskProgress().require_stage(
                tx,project_id="p",task_id="missing",lease_generation=1,
                next_state="running",evidence_digest="e"*64))
    finally:
        db.close()
