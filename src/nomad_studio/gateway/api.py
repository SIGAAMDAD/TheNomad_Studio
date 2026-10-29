"""R9 authenticated owner gateway: mTLS transport plus native owner proof.

Certificate possession alone never creates owner authority. The command digest
is bound to independent, fresh provider proof before CAB-J CommandPort sees it.
"""
from __future__ import annotations
import hashlib
import ipaddress
import socket
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from nomad_studio.trust.port_types import Result, ident, sha, digest, refused

class RequestError(ValueError):
    pass

def strict_json(raw: bytes, *, max_body: int = 65536) -> Mapping[str, Any]:
    if not isinstance(raw,bytes) or not 0 < len(raw) <= max_body:
        raise RequestError('invalid payload size')
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: raise RequestError('duplicate key')
            out[k]=v
        return out
    def invalid_constant(_): raise RequestError('nonfinite number')
    try:
        data=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise RequestError('invalid JSON') from exc
    def check(value, depth=0):
        if depth>16: raise RequestError('nested payload too deep')
        if isinstance(value,dict):
            if len(value)>256: raise RequestError('excessive fields')
            for k,v in value.items():
                if not isinstance(k,str) or len(k)>128: raise RequestError('invalid key')
                check(v,depth+1)
        elif isinstance(value,list):
            if len(value)>4096: raise RequestError('excessive array')
            for v in value: check(v,depth+1)
        elif isinstance(value,str) and len(value)>65536:
            raise RequestError('oversized text')
    check(data)
    if not isinstance(data,dict): raise RequestError('root must be object')
    return data

OWNER_FIELDS = frozenset({'command_id','operation','owner_principal_id','request_digest',
    'auth_proof_ref','idempotency_key','issued_at_us','project_id','feature_id'})
OWNER_REQUIRED = frozenset({'command_id','operation','owner_principal_id','request_digest',
    'auth_proof_ref','idempotency_key','issued_at_us'})
OWNER_OPERATIONS = frozenset({'studio.project.register','studio.project.set_active',
    'studio.set_budget','cabal.configure','cabal.inspect','production.submit_brief',
    'production.status','production.inspect','production.request_changes',
    'production.pause','production.resume','production.cancel','bible.propose',
    'bible.approve','effects.approve_protected','studio.status'})

class OwnerGateway:
    def __init__(self, *, identity: Any, commands: Any, certificate_identities: Mapping[str, Mapping[str,str]],
                 audit: Any, max_body: int = 65536):
        if type(max_body) is not int or not 1024 <= max_body <= 1_048_576:
            raise ValueError('invalid maximum')
        self.identity,self.commands,self.audit=identity,commands,audit
        self.certificates={str(k):dict(v) for k,v in certificate_identities.items()}
        self.max_body=max_body

    def submit(self, command: Mapping[str,Any], proof_ref: str, cert_der: bytes | None) -> Result:
        if not isinstance(cert_der,bytes) or not cert_der:
            return refused('UNAUTHENTICATED')
        cert_digest=hashlib.sha256(cert_der).hexdigest()
        identity=self.certificates.get(cert_digest)
        if not identity or not ident(proof_ref) or not isinstance(command,Mapping):
            return refused('UNAUTHENTICATED')
        if (not OWNER_REQUIRED.issubset(command) or not set(command).issubset(OWNER_FIELDS) or
            any(not ident(command.get(k)) for k in
                ('command_id','owner_principal_id','auth_proof_ref','idempotency_key')) or
            any(not ident(command[k]) for k in ('project_id','feature_id') if k in command) or
            command.get('operation') not in OWNER_OPERATIONS or
            type(command.get('issued_at_us')) is not int or command['issued_at_us'] < 1 or
            not digest(command.get('request_digest')) or command['auth_proof_ref'] != proof_ref):
            return refused('INVALID_INPUT')
        try:
            # The request digest excludes the digest itself and native proof ref.
            # The resulting digest is independently signed by the owner provider.
            canonical_digest=sha({k:v for k,v in command.items()
                                  if k not in ('request_digest','auth_proof_ref')})
        except (TypeError,ValueError):
            return refused('INVALID_INPUT')
        if canonical_digest != command['request_digest']:
            return refused('EVIDENCE_INVALID')
        proof=self.identity.authenticate_owner(proof_ref,canonical_digest)
        if (proof.status!='OK' or not proof.record or
            proof.record.get('principal_id') != command['owner_principal_id'] or
            any(proof.record.get(k)!=identity.get(k) for k in ('principal_id','credential_id'))):
            return refused('UNAUTHENTICATED')
        try:
            result=self.commands.submit_owner_command(dict(command),proof)
        except Exception:
            result=refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
        # Never publish command text, sensitive body or provider attestation.
        try:
            self.audit({'command_digest':canonical_digest,'command_id':command['command_id'],
                        'principal_id':proof.record['principal_id'],'status':result.status,'code':result.code})
        except Exception:
            return refused('PROVIDER_UNKNOWN','UNKNOWN_EXTERNAL')
        return result

class _BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    def __init__(self, *args, tls_context: ssl.SSLContext, **kwargs):
        self._tls_context=tls_context
        super().__init__(*args, **kwargs)
        self._inflight=threading.BoundedSemaphore(value=32)
    def get_request(self):
        # Bound the TLS handshake itself; wrapping the listening socket can
        # block the single accept thread before request concurrency limits.
        plain, address=super().get_request()
        plain.settimeout(5)
        try:
            return self._tls_context.wrap_socket(plain,server_side=True),address
        except Exception:
            plain.close()
            raise
    def process_request(self, request, client_address):
        if not self._inflight.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except Exception:
            self._inflight.release()
            raise
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self._inflight.release()

class _BoundedIPv6Server(_BoundedServer):
    address_family = socket.AF_INET6

class GatewayServer:
    def __init__(self, gateway: OwnerGateway, *, tls_context: ssl.SSLContext,
                 host: str = '127.0.0.1', port: int = 8765):
        if (not isinstance(tls_context,ssl.SSLContext) or tls_context.verify_mode != ssl.CERT_REQUIRED or
            tls_context.minimum_version < ssl.TLSVersion.TLSv1_2):
            raise ValueError('required verified client certificates')
        try:
            if not ipaddress.ip_address(host).is_loopback: raise ValueError('nonloopback bind')
        except ValueError as exc:
            raise ValueError('gateway requires loopback bind') from exc
        if type(port) is not int or not 0 <= port < 65536:
            raise ValueError('invalid gateway port')
        self.gateway,self.context,self.host,self.port=gateway,tls_context,host,port
        self._server=None; self._thread=None

    def serve(self) -> tuple[str,int]:
        if self._server is not None: raise RuntimeError('already running')
        gateway=self.gateway
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'; server_version='Nomad-R9'; sys_version=''
            def setup(self):
                self.request.settimeout(5)
                super().setup()
            def log_message(self,*_): pass
            def answer(self,status,result):
                body=json.dumps({'status':result.status,'code':result.code,
                                 'receipt_ref':result.receipt_ref},separators=(',',':')).encode()
                self.send_response(status)
                for k,v in {'Content-Type':'application/json','Cache-Control':'no-store',
                            'X-Content-Type-Options':'nosniff','Content-Length':str(len(body)),
                            'Connection':'close'}.items(): self.send_header(k,v)
                self.end_headers(); self.wfile.write(body); self.close_connection=True
            def do_POST(self):
                if self.path != '/v1/owner/command':
                    return self.answer(404,refused('NOT_FOUND','NOT_FOUND'))
                try:
                    if self.headers.get('Content-Type')!='application/json' or self.headers.get('Transfer-Encoding'):
                        raise RequestError('invalid encoding')
                    sizes=self.headers.get_all('Content-Length') or []
                    if len(sizes)!=1 or not sizes[0].isdigit() or not 0<int(sizes[0])<=gateway.max_body:
                        raise RequestError('bad content length')
                    body=strict_json(self.rfile.read(int(sizes[0])),max_body=gateway.max_body)
                    if set(body) != {'command','provider_proof_ref'}:
                        raise RequestError('bad fields')
                    cert=self.connection.getpeercert(binary_form=True)
                    result=gateway.submit(body['command'],body['provider_proof_ref'],cert)
                except RequestError:
                    result=refused('INVALID_INPUT')
                except Exception:
                    result=refused('LOCAL_RECOVERY_REQUIRED','BLOCKED_LOCAL')
                status=(200 if result.status in ('OK','ACCEPTED') else
                        401 if result.code=='UNAUTHENTICATED' else
                        409 if result.status=='CONFLICT' else
                        503 if result.status in ('UNKNOWN_EXTERNAL','BLOCKED_LOCAL','NOT_VERIFIED') else 400)
                self.answer(status,result)
        cls=_BoundedIPv6Server if ipaddress.ip_address(self.host).version == 6 else _BoundedServer
        server=cls((self.host,self.port),Handler,tls_context=self.context)
        try:
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
        except Exception:
            server.server_close(); raise
        self._server,self._thread=server,thread
        return server.server_address[:2]

    def close(self):
        if self._server is None:return
        server,thread=self._server,self._thread
        self._server=self._thread=None
        server.shutdown();server.server_close();thread.join(timeout=5)
        if thread.is_alive(): raise RuntimeError('gateway thread did not stop')
