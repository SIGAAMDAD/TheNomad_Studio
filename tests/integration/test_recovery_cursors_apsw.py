"""Durable startup recovery: C cursor CAS and uncertain channel isolation."""
import pytest
apsw=pytest.importorskip("apsw")

from studio.app.errors import StudioError
from studio.app.startup import StartupRecovery, RelayObservation
from studio.storage.workforce import WorkforceStorage
from studio.storage.recovery_cursors import RecoveryCursorAPSWState


def _fixture(db):
    c=db._connection
    c.execute("INSERT INTO principals VALUES('system','configured','system','service')")
    c.execute("INSERT INTO projects VALUES('p','P','digest',1,'active')")
    c.execute("INSERT INTO project_repositories(project_id,repo_id,provider_key,"
              "remote_ref) VALUES('p','repo','github','main')")
    c.execute("INSERT INTO relay_channels VALUES('p','channel','repo','studio/comms',"
              "'.nomad/relays/p',1)")


def test_verified_recovery_cursor_is_cas_and_unknown_channel_is_held(tmp_path):
    db=WorkforceStorage.open(tmp_path/"relay.sqlite",fresh=True)
    try:
        _fixture(db)
        state=RecoveryCursorAPSWState(db,system_principal_id="system")
        assert state.channels()[0].verified_ref_sha is None
        assert state.persist_verified_cursor(project_id="p",channel_id="channel",
                                             expected_ref_sha=None,new_ref_sha="a"*40)
        assert not state.persist_verified_cursor(project_id="p",channel_id="channel",
                                                 expected_ref_sha=None,new_ref_sha="b"*40)
        assert state.channels()[0].verified_ref_sha=="a"*40
        assert state.persist_verified_cursor(project_id="p",channel_id="channel",
                                             expected_ref_sha="a"*40,new_ref_sha="b"*40)
        assert state.channels()[0].verified_ref_sha=="b"*40
        with pytest.raises(StudioError,match="digest"):
            state.persist_verified_cursor(project_id="p",channel_id="channel",
                                          expected_ref_sha="b"*40,new_ref_sha="not-a-sha")
        db._connection.execute(
            "INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,"
            "action,effect_digest,approval_required,state,provider_key,request_id) "
            "VALUES('p','uncertain','project','p','sync',?,0,'unknown','github','req')",
            ("d"*64,))
        assert state.outstanding_effects(project_id="p")==["uncertain"]
        state.hold_channel_unknown(project_id="p",channel_id="channel",
                                   reason="remote GitHub branch rewritten")
        assert state.channels()==[]
        assert db.read("p",lambda tx:tx.one(
            "SELECT enabled FROM relay_channels WHERE project_id=? AND channel_id=?",
            ("p","channel"))) == {"enabled":0}
        assert db.read("p",lambda tx:tx.one(
            "SELECT event_type FROM audit_events WHERE project_id=?",("p",))) == {
                "event_type":"relay.channel_held_unknown"}
        state.hold_channel_unknown(project_id="p",channel_id="channel",
                                   reason="same unknown")
    finally:
        db.close()


def test_startup_scan_advances_only_verified_ancestry(tmp_path):
    db=WorkforceStorage.open(tmp_path/"replay.sqlite",fresh=True)
    try:
        _fixture(db)
        class Transport:
            def __init__(self):
                self.ancestry=True
            def scan_since(self, **kwargs):
                return [],"f"*40,self.ancestry
            def reconcile_external_effect(self, **kwargs):
                return True
        class Projector:
            def project_verified(self, observation):
                raise AssertionError("unexpected fake observation")
        state=RecoveryCursorAPSWState(db,system_principal_id="system")
        remote=Transport()
        manager=StartupRecovery(state,remote,Projector())
        report=manager.recover()
        assert state.channels()[0].verified_ref_sha=="f"*40
        remote.ancestry=False
        manager.recover()
        assert state.channels()==[]
    finally:
        db.close()
