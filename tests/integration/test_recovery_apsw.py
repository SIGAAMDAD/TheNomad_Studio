"""Restore request remains durable and fail-closed across provider uncertainty."""
import pytest
apsw=pytest.importorskip("apsw")

from studio.app.errors import StudioError
from studio.app.projects import Actor
from studio.app.releases import RecoveryCoordinator
from studio.storage.recovery import RecoveryAPSWStore
from studio.storage.workforce import WorkforceStorage


def _setup(db):
    c=db._connection
    c.execute("INSERT INTO principals VALUES('human','test','human','human')")
    c.execute("INSERT INTO projects(project_id,name,charter_digest,state) "
              "VALUES('p','project','digest','active')")
    c.execute("INSERT INTO project_grants VALUES('p','human','owner')")
    for key in ("db","manifest"):
        c.execute("INSERT INTO blobs VALUES (?,1,?,'verified')",(key,key))
        c.execute("INSERT INTO project_blobs VALUES ('p',?,'backup')",(key,))
    c.execute("INSERT INTO backup_sets(project_id,backup_digest,database_blob_digest,"
              "manifest_digest,schema_digest,state,created_at) "
              "VALUES('p', 'snapshot', 'db', 'manifest', ?,'verified','now')",("a"*64,))


def test_recovery_request_replay_and_gate(tmp_path):
    db=WorkforceStorage.open(tmp_path/"recovery.sqlite",fresh=True)
    try:
        _setup(db)
        store=RecoveryAPSWStore(db)
        svc=RecoveryCoordinator(store)
        actor=Actor("human","test",frozenset({"p"}),"proof",True)
        assert store.backup_is_verified(project_id="p",backup_digest="snapshot")
        assert store.project_revision(project_id="p")==1
        assert store.outstanding_uncertain_effects(project_id="p")==[]
        payload={"request_id":"restore-1","backup_digest":"b"*64,"expected_revision":1}
        with pytest.raises(StudioError,match="backup"):
            svc.requestRestore(actor,"p",payload)
        payload["backup_digest"]="snapshot"  # test state port directly, coordinator requires SHA
        recorded=store.request_restore(project_id="p",principal_id="human",
                                       request_id="restore-1",request_body_sha256="a"*64,
                                       backup_digest="snapshot",expected_revision=1)
        assert recorded[1]=="requested"
        assert store.request_restore(project_id="p",principal_id="human",
                                     request_id="restore-1",request_body_sha256="a"*64,
                                     backup_digest="snapshot",expected_revision=1)==recorded
        assert store.recovery_state(project_id="p")=="requested"
        assert db.read("p",lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM recovery_jobs WHERE project_id=?",("p",)))["n"]==1
        with pytest.raises(StudioError,match="different content"):
            store.request_restore(project_id="p",principal_id="human",
                  request_id="restore-1",request_body_sha256="b"*64,
                  backup_digest="snapshot",expected_revision=1)
        assert store.recovery_state(project_id="other")=="idle"
    finally:
        db.close()


def test_recovery_refuses_effect_uncertainty_and_stale_revision(tmp_path):
    db=WorkforceStorage.open(tmp_path/"guard.sqlite",fresh=True)
    try:
        _setup(db)
        store=RecoveryAPSWStore(db)
        with pytest.raises(StudioError,match="revision"):
            store.request_restore(project_id="p",principal_id="human",
                request_id="stale",request_body_sha256="a"*64,
                backup_digest="snapshot",expected_revision=999)
        c=db._connection
        c.execute("INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,"
                  "action,effect_digest,approval_required,state,provider_key,request_id) "
                  "VALUES('p','pending','project','p','sync',?,0,'unknown','github','sync-1')",
                  ("c"*64,))
        assert store.outstanding_uncertain_effects(project_id="p")==["pending"]
        with pytest.raises(StudioError,match="reconciliation"):
            store.request_restore(project_id="p",principal_id="human",
                request_id="blocked",request_body_sha256="a"*64,
                backup_digest="snapshot",expected_revision=1)
        assert store.recovery_state(project_id="p")=="idle"
    finally:
        db.close()
