"""Signed webhook dedupe survives actual APSW process writer restart."""
import hashlib
import hmac
import io
import json
import uuid

import pytest
apsw=pytest.importorskip('apsw')

from studio.app.github_webhooks import GitHubWebhookBinding,GitHubWebhookHints
from studio.storage.workforce import WorkforceStorage


def signed_request(receiver, content, delivery):
    payload=json.dumps(content,sort_keys=True,separators=(',',':')).encode('utf-8')
    sig='sha256='+hmac.new(b'webhook-test-secret-value',payload,hashlib.sha256).hexdigest()
    env={'PATH_INFO':'/webhooks/github','REQUEST_METHOD':'POST',
         'CONTENT_LENGTH':str(len(payload)),'CONTENT_TYPE':'application/json',
         'HTTP_X_GITHUB_EVENT':'push','HTTP_X_GITHUB_DELIVERY':delivery,
         'HTTP_X_HUB_SIGNATURE_256':sig,'wsgi.input':io.BytesIO(payload)}
    status=[]
    receiver(env,lambda s,h:status.append(int(s.split(' ')[0])))
    return status[0]


def test_signed_delivery_survives_db_restart_without_duplicate_mutation(tmp_path):
    path=tmp_path/'studio.sqlite'
    writer=WorkforceStorage.open(path,fresh=True)
    binding=GitHubWebhookBinding('p1','owner','repo','main','comms',b'webhook-test-secret-value')
    delivery=str(uuid.UUID(int=13))
    doc={'repository':{'full_name':'owner/repo'},'ref':'refs/heads/comms'}
    try:
        writer._connection.execute(
            "INSERT INTO projects(project_id,name,charter_digest,state) VALUES('p1','Project','digest','active')")
        wake=[]
        receiver=GitHubWebhookHints(writer,(binding,),lambda:wake.append('wake'))
        assert signed_request(receiver,doc,delivery)==202
        assert signed_request(receiver,doc,delivery)==202
        assert wake==['wake','wake']
        assert writer.read('p1',lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM workforce_command_results "
            "WHERE project_id='p1' AND action_owner='WG-G' AND request_id=?",
            (delivery,)))=={'n':1}
    finally:
        writer.close()
    reopened=WorkforceStorage.open(path,fresh=False)
    try:
        second=GitHubWebhookHints(reopened,(binding,),lambda:None)
        assert signed_request(second,doc,delivery)==202
        assert signed_request(second,{**doc,'extra':1},delivery)==409
        assert reopened.read('p1',lambda tx:tx.one(
            "SELECT COUNT(*) AS n FROM workforce_command_results "
            "WHERE project_id='p1' AND action_owner='WG-G' AND request_id=?",
            (delivery,)))=={'n':1}
    finally:
        reopened.close()
