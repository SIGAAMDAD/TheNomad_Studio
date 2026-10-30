"""C-backed source stage, independent QA, verified GitHub CAS and restart."""
import importlib.util
import sys
from pathlib import Path

import pytest
apsw=pytest.importorskip("apsw")

from studio.app.errors import StudioError
from studio.storage.integration_state import IntegrationAPSWStore, VerifiedRevision
from studio.storage.workforce import WorkforceStorage


def fixture(conn):
    location=Path(__file__).resolve().parents[1]/"unit/d/test_production.py"
    spec=importlib.util.spec_from_file_location("_integration_fixture",location)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    module.fixture_db(conn)


def test_stage_and_promote_approved_source_across_restart(tmp_path):
    path=tmp_path/"integration.sqlite"
    writer=WorkforceStorage.open(path,fresh=True)
    remote_calls=[]
    def verify(project,revision):
        remote_calls.append((project,revision))
        return VerifiedRevision("repo","c"*40,revision)
    try:
        c=writer._connection
        fixture(c)
        c.execute("INSERT INTO features(project_id,feature_id,brief_digest,snapshot_id,"
                  "source_revision_id,state) VALUES('p1','feature','brief','s1','rev1','planned')")
        c.execute("INSERT INTO tasks(project_id,task_id,feature_id,cabal_id,snapshot_id,"
                  "acceptance_digest,state) VALUES('p1','task','feature','p1-cabal','s1',"
                  "'accept','submitted')")
        c.execute("INSERT INTO leases VALUES('p1','task',1,'w1','later','active')")
        c.execute("INSERT INTO sessions(project_id,session_id,author_principal_id,task_id,"
                  "lease_generation,package_digest,snapshot_id,state)"
                  "VALUES('p1','session','owner','task',1,'pack-p1','s1','prepared')")
        c.execute("INSERT INTO submissions(project_id,submission_id,session_id,task_id,"
                  "lease_generation,source_revision_id,manifest_digest,state) "
                  "VALUES('p1','sub','session','task',1,'rev1','manifest','submitted')")
        store=IntegrationAPSWStore(writer,verify)
        stage=dict(project_id="p1",principal_id="owner",request_id="stage1",
                   request_body_sha256="a"*64,intent_id="intent",submission_id="sub",
                   expected_revision_id="rev1",staged_revision_id="b"*64)
        saved=store.record_stage(**stage)
        assert saved.state=="staged"
        assert store.record_stage(**stage)==saved
        assert store.canonical_revision(project_id="p1")=="rev1"
        with pytest.raises(StudioError,match="different content"):
            store.record_stage(**{**stage,"staged_revision_id":"d"*64})
        with pytest.raises(StudioError,match="unique accepted QA"):
            store.cas_canonical_promotion(project_id="p1",principal_id="owner",
                request_id="promote",request_body_sha256="a"*64,intent_id="intent",
                expected_revision_id="rev1",new_revision_id="b"*64,qa_review_ids=())
        with pytest.raises(StudioError,match="QA"):
            store.cas_canonical_promotion(project_id="p1",principal_id="owner",
                request_id="promote",request_body_sha256="a"*64,intent_id="intent",
                expected_revision_id="rev1",new_revision_id="b"*64,qa_review_ids=("review",))
        c.execute("INSERT INTO principals VALUES('reviewer','test','reviewer','human')")
        c.execute("INSERT INTO project_grants VALUES('p1','reviewer','member')")
        blob="p1-design-blob"
        c.execute("INSERT OR IGNORE INTO project_blobs(project_id,content_digest,purpose) "
                  "VALUES('p1',?,'source-artifact')",(blob,))
        c.execute("INSERT INTO artifacts(project_id,artifact_id,kind,content_digest,"
                  "manifest_digest,producer_session_id,source_revision_id,snapshot_id) "
                  "VALUES('p1','artifact','source',? ,?,'session','rev1','s1')",(blob,blob))
        c.execute("INSERT INTO submission_artifacts VALUES('p1','sub','artifact')")
        c.execute("INSERT INTO qa_reviews(project_id,review_id,submission_id,"
                  "reviewer_principal_id,author_session_id,source_revision_id,"
                  "acceptance_digest,verdict,evidence_digest) "
                  "VALUES('p1','review','sub','reviewer','session','rev1',"
                  "'accept','pending','evidence')")
        c.execute("INSERT INTO qa_independence_proofs(project_id,review_id,"
                  "author_session_id,reviewer_principal_id,independence_evidence_digest)"
                  "VALUES('p1','review','session','reviewer',?)",("e"*64,))
        c.execute("INSERT INTO qa_receipts VALUES('p1','review',?,'artifact',?)",
                  ("v"*64,"e"*64))
        c.execute("UPDATE qa_reviews SET verdict='pass',evidence_digest=? "
                  "WHERE project_id='p1' AND review_id='review'",("e"*64,))
        c.execute("UPDATE submissions SET state='qa_passed' "
                  "WHERE project_id='p1' AND submission_id='sub'")
        assert store.cas_canonical_promotion(
            project_id="p1",principal_id="owner",request_id="promote",
            request_body_sha256="a"*64,intent_id="intent",
            expected_revision_id="rev1",new_revision_id="b"*64,
            qa_review_ids=("review",))
        assert store.canonical_revision(project_id="p1")=="b"*64
        assert store.load_intent(project_id="p1",intent_id="intent").state=="promoted"
        assert writer.read("p1",lambda tx:tx.one(
            "SELECT state FROM submissions WHERE project_id=? AND submission_id=?",
            ("p1","sub")))["state"]=="integrated"
        assert remote_calls
    finally:
        writer.close()
    reopened=WorkforceStorage.open(path,fresh=False)
    try:
        state=IntegrationAPSWStore(reopened,verify)
        assert state.canonical_revision(project_id="p1")=="b"*64
        assert state.load_intent(project_id="p1",intent_id="intent").state=="promoted"
    finally:
        reopened.close()
