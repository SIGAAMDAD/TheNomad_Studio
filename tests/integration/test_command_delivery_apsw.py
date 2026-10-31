"""C->G command outbox publishes exact bytes, reconciles UNKNOWN without rewriting."""
import hashlib
import json
from types import SimpleNamespace

import pytest
apsw=pytest.importorskip("apsw")

from studio.app.command_delivery import CommandDeliveryPump
from studio.integrations.common import RemotePreflightUnavailable
from studio.storage.workforce import WorkforceStorage
from studio.workforce import Workforce


class VerifiedRemote:
    def __init__(self):
        self.posts=[]
        self.objects={}
        self.readable=True
        self.drop_first_reply=False
        self.no_write_once=False

    def publish(self, path, body, *, expected_digest, effect_intent_recorded):
        assert effect_intent_recorded
        assert hashlib.sha256(body).hexdigest()==expected_digest
        if self.no_write_once:
            self.no_write_once=False
            raise RemotePreflightUnavailable('prewrite readback failed',status=503)
        self.posts.append(path)
        receipt=SimpleNamespace(path=path,body=body,body_sha256=expected_digest,
                                verified=True,commit_sha="a"*40)
        self.objects[path]=receipt
        if self.drop_first_reply:
            self.drop_first_reply=False
            raise TimeoutError('remote accepted publication but reply disappeared')
        return receipt

    def observe_exact(self, path):
        return self.objects.get(path) if self.readable else None


def test_c_command_delivery_and_unknown_readback_no_second_write(tmp_path):
    db=WorkforceStorage.open(tmp_path/"outbox.sqlite",fresh=True)
    try:
        c=db._connection
        c.execute("INSERT INTO principals VALUES('operator','configured','operator','human')")
        c.execute("INSERT INTO projects VALUES('project','Project','digest',1,'active')")
        c.execute("INSERT INTO project_grants VALUES('project','operator','owner')")
        c.execute("INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) "
                  "VALUES('coder','1','digest','ready')")
        worker=Workforce(db,require_action=lambda actor,project,action:None)
        actor={"principal_id":"operator","issuer":"configured",
               "trusted_proof_ref":"proof","allowed_project_ids":["project"]}
        worker.register(actor,"project",{"request_id":"register",
                "worker_id":"worker","browser_chat_ref":"manual-chat","role_id":"coder"})
        worker.begin_shift(actor,"project",{"request_id":"shift",
                                           "worker_id":"worker","shift_id":"shift"})
        envelope=json.dumps({"kind":"STOP","project_id":"project",
                             "worker_id":"worker","shift_id":"shift"},
                            sort_keys=True,separators=(",",":"))
        expected=hashlib.sha256(envelope.encode()).hexdigest()
        c.execute("INSERT INTO command_outbox(project_id,command_id,worker_id,shift_id,"
                  "kind,envelope_digest,envelope_json,remote_path,created_at,state)"
                  "VALUES('project','stop','worker','shift','STOP',?,?,?,'now','queued')",
                  (expected,envelope,".nomad/relays/project/mail/worker/stop.json"))
        remote=VerifiedRemote()
        remote.drop_first_reply=True
        pump=CommandDeliveryPump(writer=db,workforce=worker,
                                 actor_for_project=lambda project:actor,
                                 github_for_project=lambda project:remote)
        # The remote write succeeds before the reply is lost.
        assert pump.poll_once()=={"published":0,"unknown":1,"rejected":0}
        assert remote.posts==[".nomad/relays/project/mail/worker/stop.json"]
        assert db.read("project",lambda tx:tx.one(
            "SELECT state,remote_commit_sha FROM command_outbox "
            "WHERE project_id=? AND command_id=?",("project","stop")))=={
                "state":"unknown","remote_commit_sha":None}
        # The remote is temporarily unreadable; repeated reconciliation is
        # strictly read-only and cannot issue a duplicate publication.
        remote.readable=False
        for _ in range(3):
            assert pump.poll_once()=={"published":0,"unknown":1,"rejected":0}
        assert len(remote.posts)==1
        remote.readable=True
        assert pump.poll_once()=={"published":1,"unknown":0,"rejected":0}
        assert len(remote.posts)==1
        assert db.read("project",lambda tx:tx.one(
            "SELECT state,remote_commit_sha FROM command_outbox "
            "WHERE project_id=? AND command_id=?",("project","stop")))=={
                "state":"published","remote_commit_sha":"a"*40}
    finally:
        db.close()


def test_command_delivery_refuses_invalid_poll_bound(tmp_path):
    db=WorkforceStorage.open(tmp_path/"bounds.sqlite",fresh=True)
    try:
        worker=Workforce(db,require_action=lambda *_:None)
        with pytest.raises(ValueError,match="polling interval"):
            CommandDeliveryPump(writer=db,workforce=worker,
                                actor_for_project=lambda _:None,
                                github_for_project=lambda _:None,interval_seconds=0)
    finally:
        db.close()


def test_preflight_outage_preserves_queued_command_and_retry(tmp_path):
    db=WorkforceStorage.open(tmp_path/"preflight.sqlite",fresh=True)
    try:
        c=db._connection
        c.execute("INSERT INTO principals VALUES('operator','configured','operator','human')")
        c.execute("INSERT INTO projects VALUES('project','Project','digest',1,'active')")
        c.execute("INSERT INTO project_grants VALUES('project','operator','owner')")
        c.execute("INSERT INTO specialist_roles(role_id,role_revision,role_manifest_digest,state) "
                  "VALUES('coder','1','digest','ready')")
        worker=Workforce(db,require_action=lambda *_:None)
        actor={"principal_id":"operator"}
        worker.register(actor,"project",{"request_id":"register",
                        "worker_id":"worker","browser_chat_ref":"manual-chat","role_id":"coder"})
        worker.begin_shift(actor,"project",{"request_id":"shift",
                                           "worker_id":"worker","shift_id":"shift"})
        envelope=json.dumps({"kind":"STOP","project_id":"project",
                             "worker_id":"worker","shift_id":"shift"},
                            sort_keys=True,separators=(",",":"))
        expected=hashlib.sha256(envelope.encode()).hexdigest()
        c.execute("INSERT INTO command_outbox(project_id,command_id,worker_id,shift_id,"
                  "kind,envelope_digest,envelope_json,remote_path,created_at,state)"
                  "VALUES('project','stop','worker','shift','STOP',?,?,?,'now','queued')",
                  (expected,envelope,".nomad/relays/project/mail/worker/stop.json"))
        remote=VerifiedRemote()
        remote.no_write_once=True
        pump=CommandDeliveryPump(writer=db,workforce=worker,
                                 actor_for_project=lambda _:actor,
                                 github_for_project=lambda _:remote)
        assert pump.poll_once()=={"published":0,"unknown":1,"rejected":0}
        assert remote.posts==[]
        assert db.read("project",lambda tx:tx.one(
            "SELECT state FROM command_outbox WHERE project_id=? AND command_id=?",
            ("project","stop")))=={"state":"queued"}
        assert pump.poll_once()=={"published":1,"unknown":0,"rejected":0}
        assert len(remote.posts)==1
    finally:
        db.close()
