"""Real A governance actions over the C APSW writer, owner-bound and durable."""
import pytest

apsw=pytest.importorskip("apsw")

from studio.governance.model import GovernanceError
from studio.governance.service import digest
from studio.storage.governance import GovernanceAPSWService
from studio.storage.workforce import WorkforceStorage


class BlobSource:
    def read_verified(self, project, sha, purpose):
        raise AssertionError("Bible bytes were not staged in this test")


class ImpactCatalog:
    def referenced_tasks(self, project, keys):
        return set()


def actor(person="owner", owner=True):
    return {"principal_id":person,"issuer":"test","trusted_proof_ref":"verified",
            "owner_verified":owner,"allowed_project_ids":["p"]}


def test_bibles_owner_decisions_policy_grants_survive_reopen(tmp_path):
    path=tmp_path/"canon.sqlite"
    writer=WorkforceStorage.open(path,fresh=True)
    try:
        c=writer._connection
        c.execute("INSERT INTO principals VALUES('owner','test','owner','human')")
        c.execute("INSERT INTO principals VALUES('reviewer','test','reviewer','human')")
        c.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
                  "VALUES('p','Nomad','digest','active')")
        c.execute("INSERT INTO project_grants VALUES('p','owner','owner')")
        svc=GovernanceAPSWService(writer,blobs=BlobSource(),impacts=ImpactCatalog())
        created=svc.createBible(actor(),"p",{"request_id":"b1","kind":"design","title":"Design"})
        assert svc.createBible(actor(),"p",{"request_id":"b1","kind":"design","title":"Design"})==created
        assert svc.listBibles(actor(),"p")["bibles"]==[
            {"bible_id":created["bible_id"],"kind":"design","active_edition_id":""}]
        request={"request_id":"d1","subject_type":"bible",
                 "subject_id":created["bible_id"],"action":"bible.publish",
                 "effect_digest":"e"*64}
        proposal=svc.requestDecision(actor(),"p",request)
        assert proposal["state"]=="pending"
        with pytest.raises(GovernanceError,match="owner"):
            svc.resolveDecision(actor("reviewer",False),"p",proposal["decision_id"],{
                "request_id":"attempt","decision":"approve","expected_revision":1,
                "expected_effect_digest":"e"*64})
        decision=svc.resolveDecision(actor(),"p",proposal["decision_id"],{
            "request_id":"approve","decision":"approve","expected_revision":1,
            "expected_effect_digest":"e"*64})
        assert decision["state"]=="approved"
        assert svc.listDecisions(actor(),"p")["decisions"][0]["state"]=="approved"
        rules=[{"rule_id":"r1","subject_type":"bible","action":"read",
                "scope_digest":"f"*64,"disposition":"delegated"}]
        policy={"request_id":"policy1","policy_digest":digest(rules),
                "expected_revision":0,"rules":rules}
        p=svc.setProjectPolicy(actor(),"p",policy)
        assert p["revision"]==1
        assert svc.getProjectPolicy(actor(),"p")==p
        assert svc.setProjectPolicy(actor(),"p",policy)==p
        grant=svc.grantProjectAccess(actor(),"p",{"request_id":"g1","principal_id":"reviewer",
                                                 "grant_code":"member"})
        assert grant["principal_id"]=="reviewer"
        assert svc.listProjectGrants(actor("reviewer",False),"p")["grants"]==[
            {"principal_id":"reviewer","grant_code":"member"}]
        assert svc.revokeProjectAccess(actor(),"p",{"request_id":"r1",
            "principal_id":"reviewer","grant_code":"member"})==grant
        with pytest.raises(GovernanceError,match="grant"):
            svc.listBibles(actor("reviewer",False),"p")
        assert writer.read("p",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM owner_decisions WHERE project_id=?",("p",)))["n"]==1
    finally:
        writer.close()
    reopened=WorkforceStorage.open(path,fresh=False)
    try:
        svc=GovernanceAPSWService(reopened,blobs=BlobSource(),impacts=ImpactCatalog())
        assert svc.getProjectPolicy(actor(),"p")==p
        assert svc.listDecisions(actor(),"p")["decisions"][0]["state"]=="approved"
        assert svc.createBible(actor(),"p",{"request_id":"b1","kind":"design","title":"Design"})==created
    finally:
        reopened.close()


def test_project_grants_are_checked_against_current_apsw_state(tmp_path):
    writer=WorkforceStorage.open(tmp_path/"grant.sqlite",fresh=True)
    try:
        c=writer._connection
        c.execute("INSERT INTO principals VALUES('owner','test','owner','human')")
        c.execute("INSERT INTO projects VALUES('p','Project','digest',1,'active')")
        c.execute("INSERT INTO project_grants VALUES('p','owner','member')")
        svc=GovernanceAPSWService(writer,blobs=BlobSource(),impacts=ImpactCatalog())
        with pytest.raises(GovernanceError,match="grant"):
            svc.setProjectPolicy(actor(),"p",{"request_id":"x","rules":[],
                "policy_digest":digest([]),"expected_revision":0})
        c.execute("INSERT INTO project_grants VALUES('p','owner','owner')")
        assert svc.setProjectPolicy(actor(),"p",{"request_id":"x","rules":[],
            "policy_digest":digest([]),"expected_revision":0})["revision"]==1
    finally:
        writer.close()
