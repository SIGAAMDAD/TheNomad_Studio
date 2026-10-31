"""Durable C recovery cursor CAS and unknown-channel holds across process restarts."""
import pytest
apsw=pytest.importorskip('apsw')

from studio.app.errors import StudioError
from studio.storage.workforce import WorkforceStorage
from studio.storage.recovery_cursors import RecoveryCursorAPSWState


@pytest.fixture
def recovery(tmp_path):
    writer=WorkforceStorage.open(tmp_path/'studio.sqlite',fresh=True)
    c=writer._connection
    c.execute("INSERT INTO principals VALUES('system','configured','system','service')")
    c.execute("INSERT INTO principals VALUES('nonservice','configured','operator','human')")
    c.execute("INSERT INTO projects(project_id,name,charter_digest,state) VALUES('p','Project','digest','active')")
    c.execute("INSERT INTO project_repositories VALUES('p','repo','github','main',NULL)")
    c.execute("INSERT INTO relay_channels(project_id,channel_id,repo_id,git_ref,root_path,enabled) "
              "VALUES('p','mail','repo','main','.nomad',1)")
    try:
        yield writer,RecoveryCursorAPSWState(writer,system_principal_id='system')
    finally:
        writer.close()


def test_cursor_first_observation_replay_and_compare_and_swap(recovery):
    writer,state=recovery
    assert len(state.channels())==1
    assert state.channels()[0].verified_ref_sha is None
    assert state.persist_verified_cursor(project_id='p',channel_id='missing',expected_ref_sha=None,new_ref_sha='a'*40) is False
    assert state.persist_verified_cursor(project_id='p',channel_id='mail',expected_ref_sha='a'*40,new_ref_sha='b'*40) is False
    assert state.persist_verified_cursor(project_id='p',channel_id='mail',expected_ref_sha=None,new_ref_sha='a'*40) is True
    assert state.persist_verified_cursor(project_id='p',channel_id='mail',expected_ref_sha=None,new_ref_sha='b'*40) is False
    assert state.persist_verified_cursor(project_id='p',channel_id='mail',expected_ref_sha='c'*40,new_ref_sha='b'*40) is False
    assert state.persist_verified_cursor(project_id='p',channel_id='mail',expected_ref_sha='a'*40,new_ref_sha='b'*40) is True
    assert state.channels()[0].verified_ref_sha=='b'*40


def test_invalid_cursor_digest_does_not_mutate_authoritative_state(recovery):
    writer,state=recovery
    for new,old in [('invalid',None),('A'*40,None),('a'*40,'short')]:
        with pytest.raises(StudioError,match='digest'):
            state.persist_verified_cursor(project_id='p',channel_id='mail',expected_ref_sha=old,new_ref_sha=new)
    assert state.channels()[0].verified_ref_sha is None


def test_unknown_remote_ancestry_holds_channel_with_service_principal(recovery):
    writer,state=recovery
    with pytest.raises(StudioError,match='reason'):
        state.hold_channel_unknown(project_id='p',channel_id='mail',reason='')
    with pytest.raises(StudioError,match='does not exist'):
        state.hold_channel_unknown(project_id='p',channel_id='missing',reason='mismatch')
    untrusted=RecoveryCursorAPSWState(writer,system_principal_id='nonservice')
    with pytest.raises(StudioError,match='not trusted'):
        untrusted.hold_channel_unknown(project_id='p',channel_id='mail',reason='untrusted')
    state.hold_channel_unknown(project_id='p',channel_id='mail',reason='verified remote ancestry changed')
    assert state.channels()==[]
    assert state.persist_verified_cursor(project_id='p',channel_id='mail',expected_ref_sha=None,new_ref_sha='b'*40) is False
    assert writer.read('p',lambda tx:tx.one(
        "SELECT event_type,subject_id FROM audit_events WHERE project_id='p'"))=={
            'event_type':'relay.channel_held_unknown','subject_id':'mail'}
    state.hold_channel_unknown(project_id='p',channel_id='mail',reason='duplicate')
    assert writer.read('p',lambda tx:tx.one(
        "SELECT COUNT(*) AS n FROM audit_events WHERE project_id='p'"))=={'n':1}


def test_unknown_external_effects_remain_in_readback_queue(recovery):
    writer,state=recovery
    writer._connection.execute("INSERT INTO effect_intents(project_id,effect_id,subject_type,subject_id,"
        "action,effect_digest,approval_required,state,provider_key,request_id) "
        "VALUES('p','effect','task','task','dispatch',?,0,'unknown','github','req')",('a'*64,))
    assert state.outstanding_effects(project_id='p')==['effect']
    writer._connection.execute("UPDATE effect_intents SET state='observed' WHERE project_id='p'")
    assert state.outstanding_effects(project_id='p')==[]