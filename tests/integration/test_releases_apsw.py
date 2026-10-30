"""Production release store: actual APSW, frozen FK/trigger contracts, durable replay."""
import importlib.util
import sys
from pathlib import Path

import pytest

apsw = pytest.importorskip("apsw")
from studio.app.errors import StudioError
from studio.storage.workforce import WorkforceStorage
from studio.storage.releases import ReleaseAPSWStore


def _fixture(conn):
    source = Path(__file__).resolve().parents[1] / "unit/d/test_production.py"
    spec = importlib.util.spec_from_file_location("_release_fixture",source)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    m.fixture_db(conn)


def test_release_requires_exact_artifacts_and_durable_replay(tmp_path):
    db=WorkforceStorage.open(tmp_path/"studio.sqlite", fresh=True)
    try:
        c=db._connection
        _fixture(c)
        for blob in ("artifact-content","artifact-manifest"):
            c.execute("INSERT INTO blobs VALUES (?,?,?,?)",(blob,3,"store/"+blob,"verified"))
        c.execute("INSERT INTO artifacts(project_id,artifact_id,kind,content_digest,manifest_digest,"
                  "source_revision_id,snapshot_id) VALUES(?,?,?,?,?,?,?)",
                  ("p1","artifact","source","artifact-content","artifact-manifest","rev1","s1"))
        store=ReleaseAPSWStore(db)
        kw=dict(project_id="p1",principal_id="owner",request_id="prepare-1",
                request_body_sha256="a"*64,source_revision_id="rev1",snapshot_id="s1",
                artifact_ids=("artifact",),release_digest="b"*64)
        r=store.prepare_release(**kw)
        assert r.state=="prepared"
        assert store.prepare_release(**kw)==r
        assert store.get_release(project_id="p1",release_id=r.release_id)==r
        assert store.list_releases(project_id="p1")==[r]
        assert db.read("p1",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM release_artifacts WHERE project_id=?",
            ("p1",)))["n"]==1
        with pytest.raises(StudioError,match="mismatch"):
            store.prepare_release(**{**kw,"request_id":"invalid","artifact_ids":("missing",)})
        with pytest.raises(StudioError,match="grant"):
            store.prepare_release(**{**kw,"principal_id":"outsider","request_id":"other"})
        with pytest.raises(StudioError,match="different content"):
            store.prepare_release(**{**kw,"release_digest":"c"*64})
        assert store.list_releases(project_id="p2")==[]
    finally:
        db.close()


def test_release_observation_requires_actual_owner_decision(tmp_path):
    db=WorkforceStorage.open(tmp_path/"release-observe.sqlite",fresh=True)
    try:
        c=db._connection
        _fixture(c)
        for blob in ("artifact-content","artifact-manifest"):
            c.execute("INSERT INTO blobs VALUES (?,?,?,?)",(blob,3,"store/"+blob,"verified"))
        c.execute("INSERT INTO artifacts(project_id,artifact_id,kind,content_digest,manifest_digest,"
                  "source_revision_id,snapshot_id) VALUES(?,?,?,?,?,?,?)",
                  ("p1","artifact","source","artifact-content","artifact-manifest","rev1","s1"))
        store=ReleaseAPSWStore(db)
        r=store.prepare_release(project_id="p1",principal_id="owner",request_id="r1",
             request_body_sha256="a"*64,source_revision_id="rev1",snapshot_id="s1",
             artifact_ids=("artifact",),release_digest="b"*64)
        with pytest.raises(StudioError,match="owner"):
            store.prepare_publication(project_id="p1",release_id=r.release_id,
                decision_id="missing",effect_digest="b"*64,request_id="publish",
                request_body_sha256="c"*64)
        c.execute("INSERT INTO decision_proposals(project_id,proposal_id,subject_type,"
                  "subject_id,action,effect_digest,requester_principal_id,state,created_at) "
                  "VALUES(?,?, 'release',?,'release.publish',?,'owner','pending','now')",
                  ("p1","owner-approved",r.release_id,"b"*64))
        c.execute("INSERT INTO owner_decisions(project_id,decision_id,subject_type,subject_id,"
                  "action,effect_digest,snapshot_id,verified_owner_principal_id,proof_digest,state) "
                  "VALUES(?,?,'release',?,'release.publish',?,?,'owner','proof','approved')",
                  ("p1","owner-approved",r.release_id,"b"*64,"s1"))
        kw=dict(project_id="p1",release_id=r.release_id,decision_id="owner-approved",
                effect_digest="b"*64,request_id="publish",request_body_sha256="c"*64)
        effect=store.prepare_publication(**kw)
        assert effect.state=="prepared"
        assert store.prepare_publication(**kw)==effect
        assert db.read("p1",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM approval_consumptions WHERE project_id=?",
            ("p1",)))["n"]>=1
        store.mark_publication_unknown(project_id="p1",effect_id=effect.effect_id)
        assert store.get_release(project_id="p1",release_id=r.release_id).state=="unknown"
        with pytest.raises(StudioError,match="differs"):
            store.observe_publication(project_id="p1",effect_id=effect.effect_id,
                                      verified_remote_digest="d"*64)
        store.observe_publication(project_id="p1",effect_id=effect.effect_id,
                                  verified_remote_digest="b"*64)
        assert store.get_release(project_id="p1",release_id=r.release_id).state=="published"
        store.observe_publication(project_id="p1",effect_id=effect.effect_id,
                                  verified_remote_digest="b"*64)
        store.mark_publication_unknown(project_id="p1",effect_id=effect.effect_id)
        assert store.get_release(project_id="p1",release_id=r.release_id).state=="published"
    finally:
        db.close()
