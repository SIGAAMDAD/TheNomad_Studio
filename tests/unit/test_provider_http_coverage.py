"""Provider transport and parsing failure boundaries with no network traffic."""
import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from studio.integrations.common import ProviderFailure, RemoteUnknown
from studio.integrations.http import JsonProvider, UrllibHttp


class StubHTTP:
    def __init__(self, response):
        self.response = response
        self.calls=[]
    def request(self, method,url,headers,body=None):
        self.calls.append((method,url,headers,body))
        if isinstance(self.response,BaseException):
            raise self.response
        return self.response


def test_provider_send_payload_and_empty_or_json_response():
    http=StubHTTP((201,{},b''))
    transport=JsonProvider(http,lambda:'private','https://api.github.com/')
    assert transport.request('POST','/dispatch',data={'a':1},expect=(201,)) is None
    method,url,headers,body=http.calls[0]
    assert (method,url)==('POST','https://api.github.com/dispatch')
    assert body==b'{"a":1}'
    assert headers['Authorization']=='Bearer private'
    assert headers['Content-Type']=='application/json'
    http.response=(200,{},b'[1,2]')
    assert transport.request('GET','/checks')==[1,2]


def test_provider_wrong_host_or_missing_credentials_fails_before_transport():
    with pytest.raises(ValueError,match='HTTPS'):
        JsonProvider(StubHTTP(None),lambda:'secret','http://example.com')
    http=StubHTTP((200,{},b'{}'))
    api=JsonProvider(http,lambda:'','https://api.github.com')
    with pytest.raises(ProviderFailure,match='credential'):
        api.request('GET','/user')
    assert http.calls==[]


@pytest.mark.parametrize('status,headers,body,expected',[
    (401,{},b'{}',401),
    (403,{},b'{}',403),
    (429,{'Retry-After':'not-an-interval'},b'{}',429),
    (403,{'X-RateLimit-Remaining':'0'},b'{}',403),
    (503,{},b'{}',503),
])
def test_provider_non_success_does_not_decode_as_success(status,headers,body,expected):
    api=JsonProvider(StubHTTP((status,headers,body)),lambda:'token','https://api.github.com')
    with pytest.raises(ProviderFailure) as caught:
        api.request('GET','/user')
    assert caught.value.status==expected


def test_provider_quota_observer_receives_actual_status_and_response_headers():
    recorded=[]
    api=JsonProvider(StubHTTP((200,{'X-RateLimit-Remaining':'39'},b'{"ok":true}')),
                     lambda:'token','https://api.github.com',
                     quota_observer=lambda headers,status:recorded.append((status,headers)))
    assert api.request('GET','/rate')=={'ok':True}
    assert recorded==[(200,{'X-RateLimit-Remaining':'39'})]


@pytest.mark.parametrize('body',[b'no-json',b'\xff',b'{"broken":'])
def test_provider_rejects_non_json_success(body):
    api=JsonProvider(StubHTTP((200,{},body)),lambda:'token','https://api.github.com')
    with pytest.raises(ProviderFailure,match='invalid JSON'):
        api.request('GET','/broken')


def test_urllib_http_error_returns_bounded_body(monkeypatch):
    def raise_http(*args,**kwargs):
        raise urllib.error.HTTPError('https://api.github.com',403,'denied',{'Retry-After':'7'},io.BytesIO(b'error'*5000))
    monkeypatch.setattr('studio.integrations.http.urllib.request.urlopen',raise_http)
    result=UrllibHttp(timeout=3).request('GET','https://api.github.com',{},None)
    assert result[0]==403
    assert result[1]['Retry-After']=='7'
    assert len(result[2])==4096


def test_urllib_http_network_failure_maps_to_remote_unknown(monkeypatch):
    def lost(*args,**kwargs):
        raise TimeoutError('socket timed out')
    monkeypatch.setattr('studio.integrations.http.urllib.request.urlopen',lost)
    with pytest.raises(RemoteUnknown,match='TimeoutError'):
        UrllibHttp().request('GET','https://api.github.com',{})


def test_urllib_http_success_uses_response_status_headers_and_bytes(monkeypatch):
    class Response:
        status=200
        headers={'Content-Type':'application/json'}
        def read(self):return b'{}'
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr('studio.integrations.http.urllib.request.urlopen',lambda *args,**kw:Response())
    assert UrllibHttp().request('GET','https://api.github.com',{})==(200,{'Content-Type':'application/json'},b'{}')