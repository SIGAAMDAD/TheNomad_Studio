from hashlib import sha256
import pytest
from studio.agents.shift_runtime import ShiftInbox,ShiftProtocolError

H='a'*64

def msg(i,**kw):
    d=dict(project_id='p',worker_id='w',shift_id='s',assignment_id='a'+str(i),command_id='c'+str(i),kind='ASSIGN',payload_digest=H,source_revision='sha1')
    d.update(kw);return d

class Relay:
    def __init__(self):self.messages=[];self.receipts=[];self.checkpoints=[]
    def read_messages(self,**kwargs):return self.messages[:]
    def publish_receipt(self,**kwargs):self.receipts.append(kwargs);return 'github:verified:'+kwargs['command_id']
    def checkpoint(self,**kwargs):self.checkpoints.append(kwargs)

def test_two_assignments_one_shift():
    relay=Relay();inbox=ShiftInbox('p','w','s','sha1',relay)
    relay.messages=[msg(1)];a=inbox.poll();assert len(a)==1
    inbox.record_result(a[0],result={'artifact':'one'},status='completed')
    relay.messages.append(msg(2));b=inbox.poll();assert [x.command_id for x in b]==['c2']
    inbox.record_result(b[0],result={'artifact':'two'},status='completed')
    assert len(relay.receipts)==2
    assert len(inbox.processed)==2
    assert not inbox.poll()
    assert relay.checkpoints[-1]['processed']==('c1','c2')

def test_wrong_recipient_and_stale_revision_rejected():
    for bad in [msg(1,worker_id='other'),msg(1,shift_id='old'),msg(1,source_revision='old')]:
        relay=Relay();relay.messages=[bad]
        with pytest.raises(ShiftProtocolError):ShiftInbox('p','w','s','sha1',relay).poll()

def test_duplicate_does_not_execute_twice():
    relay=Relay();relay.messages=[msg(1),msg(1)]
    inbox=ShiftInbox('p','w','s','sha1',relay)
    assert len(inbox.poll())==1
    inbox.record_result(inbox.poll()[0],result={},status='completed')
    assert inbox.poll()==[]
    with pytest.raises(ShiftProtocolError):inbox.record_result(inbox_cmd:=type('X',(),{'project_id':'p','worker_id':'w','shift_id':'s','command_id':'c1','kind':'ASSIGN'})(),result={},status='completed')

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
    assert not inbox.processed
