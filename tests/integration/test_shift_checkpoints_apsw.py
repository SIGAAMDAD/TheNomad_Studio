"""Checkpoint crash/restart tests run on the production APSW C writer."""
import pytest
apsw = pytest.importorskip('apsw')

from studio.storage.workforce import WorkforceStorage
from studio.storage.shift_checkpoints import DurableShiftCheckpoints, CheckpointFault


def fresh(tmp_path):
    path = tmp_path / 'c.sqlite'
    writer = WorkforceStorage.open(path, fresh=True)
    c = writer._connection
    c.execute("INSERT INTO principals VALUES('operator','configured','operator','human')")
    c.execute("INSERT INTO projects VALUES('project','Project','digest',1,'active')")
    c.execute("INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) "
              "VALUES('coder','1','digest','ready')")
    c.execute("INSERT INTO worker_conversations(project_id,worker_id,principal_id,role_id,"
              "browser_chat_ref,state) VALUES('project','worker','operator','coder','browser','registered')")
    c.execute("INSERT INTO worker_shifts(project_id,shift_id,worker_id,state,created_at) "
              "VALUES('project','shift','worker','awaiting_hello','now')")
    c.execute("UPDATE worker_shifts SET state='active',hello_commit_sha=?,"
              "hello_digest=?,observed_at='now' WHERE project_id='project' AND shift_id='shift'",
              ('a' * 40, 'b' * 64))
    return writer, path


def test_verified_checkpoint_survives_writer_restart_and_is_monotone(tmp_path):
    writer, db_path = fresh(tmp_path)
    artifacts = tmp_path / 'checkpoints'
    store = DurableShiftCheckpoints(writer, artifacts)
    first = store.save_checkpoint(project_id='project', worker_id='worker',
                                  shift_id='shift', cursor=None,
                                  processed=(), fingerprints={})
    again = store.save_checkpoint(project_id='project', worker_id='worker',
                                  shift_id='shift', cursor=None,
                                  processed=(), fingerprints={})
    assert first == again
    second = store.save_checkpoint(project_id='project', worker_id='worker',
                                   shift_id='shift', cursor='c' * 40,
                                   processed=('cmd',), fingerprints={'cmd': 'd' * 64})
    assert second != first
    with pytest.raises(CheckpointFault, match='roll back'):
        store.save_checkpoint(project_id='project', worker_id='worker',
                              shift_id='shift', cursor='c' * 40,
                              processed=(), fingerprints={})
    writer.close()
    reopened = WorkforceStorage.open(db_path, fresh=False)
    try:
        doc = DurableShiftCheckpoints(reopened, artifacts).load_latest(
            project_id='project', worker_id='worker', shift_id='shift')
        assert doc['cursor'] == 'c' * 40
        assert doc['processed'] == ['cmd']
        assert doc['fingerprints'] == {'cmd': 'd' * 64}
    finally:
        reopened.close()


def test_tampering_and_cross_worker_replay_fails_closed(tmp_path):
    writer, _ = fresh(tmp_path)
    try:
        store = DurableShiftCheckpoints(writer, tmp_path / 'blobs')
        checkpoint = store.save_checkpoint(project_id='project', worker_id='worker',
                                           shift_id='shift', cursor=None,
                                           processed=(), fingerprints={})
        with pytest.raises(CheckpointFault, match='owner mismatch'):
            store.load_latest(project_id='project', worker_id='intruder', shift_id='shift')
        target = store._path('project', 'shift', checkpoint)
        target.write_bytes(b'{}')
        with pytest.raises(CheckpointFault, match='digest'):
            store.load_latest(project_id='project', worker_id='worker', shift_id='shift')
    finally:
        writer.close()
