from nomad_studio.hr_bridge.api import PrivateOversightBridge
from nomad_studio.trust.port_types import Result
from nomad_studio.oversight.codec import CLAIM_FIELDS

class NoticeSink:
    def __init__(self):self.received=[]
    def ingest_signed_notice(self,c,s):
        self.received.append((c,s))
        return Result('ACCEPTED','OK',receipt_ref='accepted/notice')

def test_private_case_cannot_cross_bridge():
    sink=NoticeSink();bridge=PrivateOversightBridge(sink)
    claims={k:'x' for k in CLAIM_FIELDS}
    signature={'key_id':'a','signature':'b','proof_ref':'c'}
    assert bridge.submit({**claims,'private_case_text':'sensitive'},signature).code=='INVALID_INPUT'
    assert sink.received==[]
    assert bridge.submit(claims,signature).status=='ACCEPTED'
    assert set(sink.received[0][0])==CLAIM_FIELDS
