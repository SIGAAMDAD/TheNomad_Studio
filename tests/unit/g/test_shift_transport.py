"""Real GitHub connector with fake HTTP, no external requests or AI workers."""
import pytest
from hashlib import sha256

from test_integrations import FakeGitHubHTTP, SHA0, SHA1, SHA2, TOKEN
from studio.integrations.github import GitHub, GitHubChannel
from studio.integrations.common import IntegrationError, ProjectBinding
from studio.agents.shift_runtime import ShiftInbox, ShiftProtocolError
from studio.agents.github_transport import GitHubShiftTransport, canonical


def command(*, command_id='cmd-one', shift_id='shift-one', worker_id='worker-one'):
    return dict(project_id='p1',worker_id=worker_id,shift_id=shift_id,
                assignment_id='assignment-one',command_id=command_id,kind='ASSIGN',
                payload_digest='a'*64,source_revision='source-main')


def rig():
    fake=FakeGitHubHTTP()
    channel=GitHubChannel('p1','owner','repo','studio/comms')
    github=GitHub(fake,TOKEN,channel,ProjectBinding('p1','account'))
    intents=[];checkpoints=[]
    def record(path, digest):
        intents.append((path,digest));return True
    def checkpoint(**kwargs):checkpoints.append(kwargs)
    transport=GitHubShiftTransport(github,shift_id='shift-one',record_intent=record,
                                   save_checkpoint=checkpoint)
    return fake,channel,transport,intents,checkpoints


def test_verified_github_two_assignments_share_one_shift():
    fake,channel,transport,intents,checkpoints=rig()
    cmd=command()
    path=channel.path('mail','worker-one',cmd['command_id'])
    fake.inject(path,canonical(cmd),SHA1)
    inbox=ShiftInbox('p1','worker-one','shift-one','source-main',transport)
    first=inbox.poll()
    assert len(first)==1 and inbox.cursor is None
    receipt=inbox.record_result(first[0],result={'artifact':'one'},status='completed')
    assert receipt==SHA2 and inbox.cursor==SHA1
    assert fake.mutations==1 and len(intents)==1
    # The new Git head includes a receipt; no second ASSIGN is fabricated.
    assert inbox.poll()==[]
    assert inbox.cursor==SHA2
    assert checkpoints[-1]['cursor']==SHA2
    assert any('/receipts/worker-one/cmd-one/' in path for path in fake.snapshot[SHA2])


def test_receipt_publication_requires_persisted_intent():
    fake,channel,transport,intents,_=rig()
    cmd=command()
    fake.inject(channel.path('mail','worker-one',cmd['command_id']),canonical(cmd),SHA1)
    transport.record_intent=lambda *_:False
    inbox=ShiftInbox('p1','worker-one','shift-one','source-main',transport)
    with pytest.raises(ShiftProtocolError,match='intent'):
        inbox.record_result(inbox.poll()[0],result={},status='completed')
    assert fake.mutations==0 and not inbox.processed


def test_prior_shift_mail_ignored_for_same_persistent_worker():
    fake,channel,transport,_,_=rig()
    cmd=command(shift_id='different-shift')
    fake.inject(channel.path('mail','worker-one',cmd['command_id']),canonical(cmd),SHA1)
    batch=transport.read_messages(project_id='p1',worker_id='worker-one',after=None)
    assert batch.messages==() and batch.head_sha==SHA1


def test_cross_worker_receipt_path_rejected():
    fake,channel,transport,_,_=rig()
    body=canonical(dict(protocol_version='2',receipt_id='r1',project_id='p1',
                        worker_id='worker-one',shift_id='shift-one',command_id='command-one',
                        command_digest='a'*64,result_digest='b'*64,status='completed'))
    with pytest.raises(IntegrationError):
        transport.github._validate_path(channel.path('receipts','worker-two','r1',message_id='command-one'),body)


def test_github_incomplete_scan_preserves_cursor():
    fake,channel,transport,intents,checkpoints=rig()
    fake.inject(channel.path('mail','worker-one','cmd-one'),canonical(command()),SHA1)
    fake.truncated=True
    inbox=ShiftInbox('p1','worker-one','shift-one','source-main',transport)
    with pytest.raises(Exception):inbox.poll()
    assert inbox.cursor is None and not checkpoints
