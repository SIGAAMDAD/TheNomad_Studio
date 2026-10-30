from hashlib import sha256
import pytest
from studio.agents.shift_runtime import ShiftInbox, ShiftProtocolError, ShiftBatch

H='a'*64
A='1'*40
B='2'*40

def msg(i, **kw):
    d=dict(project_id='p',worker_id='w',shift_id='s',assignment_id='a'+str(i),
           command_id='c'+str(i),kind='ASSIGN',payload_digest=H,source_revision='sha1')
    d.update(kw)
    return d

class Relay:
    def __init__(self):
        self.messages=[];self.receipts=[];self.checkpoints=[];self.head=A;self.after=[]
    def read_messages(self, **kwargs):
        self.after.append(kwargs['after'])
        return ShiftBatch(tuple(self.messages), self.head)
    def publish_receipt(self, **kwargs):
        self.receipts.append(kwargs)
        return 'c'*40
    def checkpoint(self, **kwargs):
        self.checkpoints.append(kwargs)


def test_two_assignments_one_shift():
    relay=Relay();inbox=ShiftInbox('p','w','s','sha1',relay)
    relay.messages=[msg(1)];a=inbox.poll();assert len(a)==1
    inbox.record_result(a[0],result={'artifact':'one'},status='completed')
    assert inbox.cursor==A
    relay.head=B;relay.messages=[msg(2)];b=inbox.poll();assert [x.command_id for x in b]==['c2']
    inbox.record_result(b[0],result={'artifact':'two'},status='completed')
    assert len(relay.receipts)==2 and len(inbox.processed)==2
    relay.messages=[]
    assert not inbox.poll()
    assert relay.after[:3]==[None,A,B]
    assert relay.checkpoints[-1]['processed']==('c1','c2')


def test_two_commands_in_one_snapshot_do_not_advance_cursor_early():
    relay=Relay();relay.messages=[msg(1),msg(2)]
    inbox=ShiftInbox('p','w','s','sha1',relay)
    pending=inbox.poll()
    inbox.record_result(pending[0],result={},status='completed')
    assert inbox.cursor is None
    assert [x.command_id for x in inbox.poll()]==['c2']
    assert relay.after==[None]
    inbox.record_result(pending[1],result={},status='completed')
    assert inbox.cursor==A


def test_wrong_recipient_and_stale_revision_rejected():
    for bad in [msg(1,worker_id='other'),msg(1,shift_id='old'),msg(1,source_revision='old')]:
        relay=Relay();relay.messages=[bad]
        with pytest.raises(ShiftProtocolError):ShiftInbox('p','w','s','sha1',relay).poll()


def test_duplicate_does_not_execute_twice():
    relay=Relay();relay.messages=[msg(1),msg(1)]
    inbox=ShiftInbox('p','w','s','sha1',relay)
    a=inbox.poll();assert len(a)==1
    inbox.record_result(a[0],result={},status='completed')
    relay.messages=[]
    assert inbox.poll()==[]
    with pytest.raises(ShiftProtocolError):inbox.record_result(a[0],result={},status='completed')


def test_stop_cooperative():
    relay=Relay();relay.messages=[msg(3,kind='STOP')]
    inbox=ShiftInbox('p','w','s','sha1',relay)
    inbox.record_result(inbox.poll()[0],result={'stop':True},status='stopped')
    relay.messages.append(msg(4))
    assert inbox.stopping and inbox.poll()==[]


def test_receipt_unverified_keeps_pending():
    relay=Relay();relay.messages=[msg(1)];relay.publish_receipt=lambda **kw:''
    inbox=ShiftInbox('p','w','s','sha1',relay)
    with pytest.raises(ShiftProtocolError):inbox.record_result(inbox.poll()[0],result={},status='completed')
    assert not inbox.processed and inbox.cursor is None


def test_duplicate_command_with_different_content_is_rejected():
    relay=Relay();relay.messages=[msg(1),msg(1,payload_digest='b'*64)]
    with pytest.raises(ShiftProtocolError, match='divergent'):
        ShiftInbox('p','w','s','sha1',relay).poll()


def test_command_mutation_after_checkpoint_is_rejected():
    relay=Relay();relay.messages=[msg(1)]
    inbox=ShiftInbox('p','w','s','sha1',relay)
    inbox.record_result(inbox.poll()[0],result={'ok':True},status='completed')
    relay.head=B;relay.messages=[msg(1,payload_digest='b'*64)]
    with pytest.raises(ShiftProtocolError, match='changed'):
        inbox.poll()


def test_empty_batch_advances_verified_git_head():
    relay=Relay();inbox=ShiftInbox('p','w','s','sha1',relay)
    assert inbox.poll()==[] and inbox.cursor==A
    assert relay.checkpoints[-1]['cursor']==A


def test_command_id_is_not_a_git_cursor():
    relay=Relay();relay.messages=[msg(1)]
    inbox=ShiftInbox('p','w','s','sha1',relay)
    inbox.record_result(inbox.poll()[0],result={},status='completed')
    assert inbox.cursor==A and inbox.cursor!='c1'


def test_distinct_authorized_source_revisions_in_one_shift():
    relay=Relay();relay.messages=[msg(1,source_revision='first-source')]
    authorized={'a1':'first-source','a2':'second-source'}
    inbox=ShiftInbox('p','w','s',None,relay,
        verify_assignment=lambda cmd:authorized.get(cmd.assignment_id)==cmd.source_revision)
    first=inbox.poll()[0]
    inbox.record_result(first,result={},status='completed')
    relay.head=B;relay.messages=[msg(2,source_revision='second-source')]
    second=inbox.poll()[0]
    inbox.record_result(second,result={},status='completed')
    assert inbox.processed=={'c1','c2'}
    relay.head='3'*40;relay.messages=[msg(3,source_revision='third-source')]
    with pytest.raises(ShiftProtocolError,match='not authorized'):
        inbox.poll()
