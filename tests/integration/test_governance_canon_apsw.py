"""Two actual Bible publications build one persisted Canon with owner receipts."""
import hashlib
import json

import pytest
apsw=pytest.importorskip("apsw")

from studio.governance.model import Intent
from studio.governance.service import Governance
from studio.storage.governance import GovernanceAPSWService
from studio.storage.workforce import WorkforceStorage


class VerifiedSource:
    def __init__(self):
        self.rows={}
    def put(self,project,purpose,body):
        value=hashlib.sha256(body).hexdigest()
        self.rows[(project,purpose,value)]=body
        return value
    def read_verified(self,project,digest,purpose):
        return self.rows[(project,purpose,digest)]


class ActualImpactCatalog:
    def referenced_tasks(self,project,keys):
        return set()


def actor():
    return {"principal_id":"owner","issuer":"test","trusted_proof_ref":"proof",
            "owner_verified":True,"allowed_project_ids":["p"]}


def test_actual_bible_owner_publication_and_canon_restart(tmp_path):
    path=tmp_path/"canon-publication.sqlite"
    writer=WorkforceStorage.open(path,fresh=True)
    blobs=VerifiedSource()
    manifest=json.dumps({"format":"nomad.bible.manifest.v1","clauses":[],"facts":[]},
                        sort_keys=True,separators=(",",":")).encode()
    try:
        c=writer._connection
        c.execute("INSERT INTO principals VALUES('owner','test','owner','human')")
        c.execute("INSERT INTO projects VALUES('p','Nomad','digest',1,'active')")
        c.execute("INSERT INTO project_grants VALUES('p','owner','owner')")
        service=GovernanceAPSWService(writer,blobs=blobs,impacts=ActualImpactCatalog())
        for kind in ("design","technical"):
            bible=service.createBible(actor(),"p",{"request_id":"b-"+kind,
                "kind":kind,"title":kind})["bible_id"]
            content=blobs.put("p","bible-content",("original "+kind).encode())
            metadata=blobs.put("p","bible-manifest",manifest)
            for value,purpose in ((content,"bible-content"),(metadata,"bible-manifest")):
                c.execute("INSERT OR IGNORE INTO blobs VALUES (?,?,?,'verified')",
                          (value,10,"blobs/"+value))
                c.execute("INSERT OR IGNORE INTO project_blobs VALUES (?,?,?)",
                          ("p",value,purpose))
            stage=service.stageBible(actor(),"p",bible,{
                "request_id":"stage-"+kind,
                "content_digest":content,
                "manifest_digest":metadata,
            })
            obj=Intent(stage["intent_id"],bible,"stage-"+kind,content,metadata,None,"verified",0)
            effect_digest=Governance.publication_effect("p",bible,obj)
            pending=service.requestDecision(actor(),"p",{
                "request_id":"decision-"+kind,
                "subject_type":"bible",
                "subject_id":bible,
                "action":"bible.publish",
                "effect_digest":effect_digest,
            })
            service.resolveDecision(actor(),"p",pending["decision_id"],{
                "request_id":"resolve-"+kind,
                "decision":"approve",
                "expected_revision":1,
                "expected_effect_digest":effect_digest,
            })
            published=service.publishBible(actor(),"p",bible,{
                "request_id":"publish-"+kind,
                "intent_id":stage["intent_id"],
                "decision_id":pending["decision_id"],
                "effect_digest":effect_digest,
            })
            assert published["edition_id"]
            if kind=="design":
                assert published["snapshot_id"]==""
            else:
                assert published["snapshot_id"]
                assert service.getCanon(actor(),"p")["snapshot_id"]==published["snapshot_id"]
        assert c.execute("SELECT COUNT(*) FROM approval_consumptions WHERE project_id='p'").fetchone()[0]>=2
        assert c.execute("SELECT COUNT(*) FROM bible_editions WHERE project_id='p'").fetchone()[0]==2
        assert c.execute("SELECT COUNT(*) FROM canon_snapshots WHERE project_id='p'").fetchone()[0]==1
    finally:
        writer.close()
    again=WorkforceStorage.open(path,fresh=False)
    try:
        service=GovernanceAPSWService(again,blobs=blobs,impacts=ActualImpactCatalog())
        assert service.getCanon(actor(),"p")["snapshot_id"]==published["snapshot_id"]
        assert len(service.listBibles(actor(),"p")["bibles"])==2
    finally:
        again.close()
