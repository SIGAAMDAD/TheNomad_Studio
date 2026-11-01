"""F-20 remote receipt verification never accepts synthetic self-claims."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from qa.f20_remote import verify_remote_pairs


def sample():
    start = datetime(2026, 10, 31, tzinfo=timezone.utc)
    evidence = {'project_id':'p','channel_id':'c','shift_id':'s',
                'contract_digest':'f'*64,'pairs':[]}
    remote = {}
    for index in (1, 2):
        command = {'protocol_version':'1','kind':'ASSIGN','message_id':f'm{index}',
                   'project_id':'p','channel_id':'c','to_shift_id':'s',
                   'task_id':f't{index}','lease_generation':index,
                   'contract_digest':'f'*64,
                   'issued_at':(start+timedelta(minutes=index*2)).isoformat()}
        raw = json.dumps(command).encode()
        receipt = {'protocol_version':'1','kind':'SEEN','receipt_id':f'r{index}',
                   'project_id':'p','channel_id':'c','recipient_shift_id':'s',
                   'message_id':f'm{index}','contract_digest':'f'*64,
                   'observed_message_sha256':hashlib.sha256(raw).hexdigest(),
                   'issued_at':(start+timedelta(minutes=index*2+1)).isoformat()}
        evidence['pairs'].append({'command_path':f'commands/{index}.json',
                                  'command_commit_sha':str(index)*40,
                                  'receipt_path':f'receipts/{index}.json',
                                  'receipt_commit_sha':str(index+2)*40})
        remote[(f'commands/{index}.json',str(index)*40)] = raw
        remote[(f'receipts/{index}.json',str(index+2)*40)] = json.dumps(receipt).encode()
    return evidence,remote


def test_exact_two_remote_receipts_produce_limited_verdict():
    evidence,remote = sample()
    output = verify_remote_pairs(evidence, lambda path, sha: remote[(path, sha)])
    assert output['distinct_receipts'] == 2
    assert output['browser_continuity'] == 'REQUIRES_INDEPENDENT_OBSERVATION'


def test_rejects_wrong_shift_and_replayed_receipt():
    evidence,remote = sample()
    message = json.loads(remote[('receipts/2.json','4'*40)])
    message['recipient_shift_id']='another'
    remote[('receipts/2.json','4'*40)]=json.dumps(message).encode()
    with pytest.raises(ValueError, match='receipt does not authenticate'):
        verify_remote_pairs(evidence,lambda path, sha: remote[(path, sha)])
    evidence,remote=sample()
    evidence['pairs'][1]['receipt_commit_sha']='3'*40
    with pytest.raises(ValueError):
        verify_remote_pairs(evidence,lambda path, sha: remote[(path, sha)])


def test_rejects_forged_command_bytes_and_malformed_locator():
    evidence,remote=sample()
    remote[('commands/1.json','1'*40)]=b'{}'
    with pytest.raises(ValueError, match='remote assignment'):
        verify_remote_pairs(evidence,lambda path, sha: remote[(path, sha)])
    evidence,remote=sample()
    evidence['pairs'][0]['command_path']='../outside'
    with pytest.raises(ValueError, match='unsafe'):
        verify_remote_pairs(evidence,lambda path, sha: remote[(path, sha)])


def test_rejects_fewer_than_two_receipts():
    evidence,remote=sample()
    evidence['pairs'].pop()
    with pytest.raises(ValueError, match='exactly two'):
        verify_remote_pairs(evidence,lambda path, sha: remote[(path, sha)])
