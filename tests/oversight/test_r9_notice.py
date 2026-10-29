import base64
import hmac
from hashlib import sha256
from nomad_studio.oversight.api import OversightNoticeService
from nomad_studio.oversight.codec import canonical,digest
from nomad_studio.trust.port_types import Result
from r9_helpers import SignedNative

class Assignments:
    def current_assignment(self,worker,job):
        return {'worker_id':worker,'job_id':job,'epoch':2,'fenced':False}
class Sink:
    def __init__(self):self.calls=[];self.fail=False
    def accept(self,claims,digest_,proof_ref):
        self.calls.append((claims,digest_,proof_ref))
        if self.fail: raise TimeoutError('unknown provider commit')
        return Result('ACCEPTED','OK',receipt_ref='journal/ack')

def sample():
    return {'notice_id':'notice/1','issuer_id':'fixture/issuer','worker_id':'worker/1',
      'job_id':'job/1','assignment_epoch':2,'action':'HOLD_JOB','scope':'worker_job_epoch',
      'issued_at_us':900_000,'expires_at_us':1_200_000,'case_ref_sha256':'f'*64}

def setup(production=True):
    n=SignedNative(production=production);sink=Sink()
    api=OversightNoticeService(verifier=n.verifier,assignment_reader=Assignments(),
                               sink=sink,clock_us=lambda:1_000_000)
    return n,sink,api

def attest(native,notice):
    sig=base64.urlsafe_b64encode(hmac.new(b'dummy_notice_key',canonical(notice),sha256).digest()).rstrip(b'=').decode()
    signature={'key_id':'oversight/key','signature':sig,'proof_ref':'native/notice'}
    signed={'claims':notice,'key_id':signature['key_id'],'signature':sig}
    native.sign('native/notice','OVERSIGHT_NOTICE',notice_digest=digest(signed),
       notice_id=notice['notice_id'],worker_id=notice['worker_id'],job_id=notice['job_id'],
       assignment_epoch=notice['assignment_epoch'],key_id=signature['key_id'],
       issuer_id=notice['issuer_id'],notice_signature=sig)
    return signature

def test_signed_minimal_notice_and_immutable_scope():
    native,sink,api=setup();notice=sample();signature=attest(native,notice)
    result=api.ingest_signed_notice(notice,signature)
    assert result.status=='ACCEPTED'
    assert result.record=={'notice_id':'notice/1','action':'HOLD_JOB',
                           'worker_id':'worker/1','job_id':'job/1','assignment_epoch':2}
    assert set(sink.calls[0][0]) == set(notice)
    notice['private_case_text']='case details leaked'
    assert api.ingest_signed_notice(notice,signature).code=='INVALID_INPUT'
    assert len(sink.calls)==1

def test_wrong_epoch_forgery_and_simulated_issuer_fail_closed():
    native,sink,api=setup();notice=sample();signature=attest(native,notice)
    notice['assignment_epoch']=3
    assert api.ingest_signed_notice(notice,signature).code=='EVIDENCE_INVALID'
    assert not sink.calls
    native,sink,api=setup(production=False)
    notice=sample();signature=attest(native,notice)
    assert api.ingest_signed_notice(notice,signature).code=='EVIDENCE_INVALID'

def test_sink_ambiguity_never_promotes_notice_to_applied():
    native,sink,api=setup();notice=sample();sig=attest(native,notice)
    sink.fail=True
    assert api.ingest_signed_notice(notice,sig).status=='UNKNOWN_EXTERNAL'
