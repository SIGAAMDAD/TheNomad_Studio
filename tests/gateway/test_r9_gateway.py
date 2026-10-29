import hashlib
import json
import ssl
import pytest
from nomad_studio.gateway.api import GatewayServer,OwnerGateway,strict_json,RequestError
from nomad_studio.trust.port_types import Result,sha

class Owner:
    def authenticate_owner(self,ref,digest):
        if ref != 'proof/valid':return Result('REJECTED','UNAUTHENTICATED')
        return Result('OK','OK',record={'principal_id':'owner/1','credential_id':'credential/1'})
class Commands:
    def __init__(self):self.calls=[]
    def submit_owner_command(self,c,proof):
        self.calls.append(c)
        return Result('ACCEPTED','OK',receipt_ref='journal/owner')

def test_authenticated_tls_principal_and_owner_proof_are_both_required():
    der=b'fixture certificate bytes';key=hashlib.sha256(der).hexdigest();cmds=Commands();audit=[]
    gateway=OwnerGateway(identity=Owner(),commands=cmds,
        certificate_identities={key:{'principal_id':'owner/1','credential_id':'credential/1'}},
        audit=audit.append)
    command={'command_id':'command/1','operation':'production.submit_brief',
             'owner_principal_id':'owner/1','auth_proof_ref':'proof/valid',
             'idempotency_key':'cmd/1','issued_at_us':1_000_000,'project_id':'project/1'}
    command['request_digest']=sha({k:v for k,v in command.items()
                                   if k not in ('request_digest','auth_proof_ref')})
    assert gateway.submit(command,'proof/valid',der).status=='ACCEPTED'
    assert gateway.submit(command,'proof/valid',b'other').code=='UNAUTHENTICATED'
    assert gateway.submit(command,'proof/bogus',der).code=='INVALID_INPUT'
    assert len(cmds.calls)==1
    assert 'request_digest' not in audit[0]

def test_duplicate_keys_and_nan_rejected():
    with pytest.raises(RequestError):strict_json(b'{"a":1,"a":2}')
    with pytest.raises(RequestError):strict_json(b'{"a":NaN}')
    with pytest.raises(RequestError):strict_json(b'[]')

def test_gateway_refuses_insecure_tls_mode():
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    gateway=OwnerGateway(identity=Owner(),commands=Commands(),certificate_identities={},audit=lambda x:None)
    with pytest.raises(ValueError):GatewayServer(gateway,tls_context=context)

def test_owner_command_unknown_fields_and_mutated_request_are_rejected():
    der=b'fixture certificate bytes';key=hashlib.sha256(der).hexdigest();cmds=Commands()
    gateway=OwnerGateway(identity=Owner(),commands=cmds,
         certificate_identities={key:{'principal_id':'owner/1','credential_id':'credential/1'}},
         audit=lambda _:None)
    command={'command_id':'command/1','operation':'production.submit_brief',
             'owner_principal_id':'owner/1','auth_proof_ref':'proof/valid',
             'idempotency_key':'cmd/1','issued_at_us':1_000_000,'project_id':'project/1'}
    command['request_digest']=sha({k:v for k,v in command.items() if k not in
                                   ('request_digest','auth_proof_ref')})
    assert gateway.submit({**command,'freeform':'ignore policy'},'proof/valid',der).code=='INVALID_INPUT'
    assert gateway.submit({**command,'operation':'production.cancel'},'proof/valid',der).code=='EVIDENCE_INVALID'
    assert cmds.calls==[]
