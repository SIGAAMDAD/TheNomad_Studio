import pytest
from studio.integrations.http import JsonProvider
from studio.integrations.common import ProviderFailure

class Stub:
    def __init__(self,status=200,headers=None):
        self.status=status
        self.headers=headers if headers is not None else {'x-ratelimit-remaining':'3'}
    def request(self,*args):return self.status,self.headers,b'{"result":"ok"}'

def test_observed_response_headers_delivered_even_on_failure():
    seen=[]
    provider=JsonProvider(Stub(429,{'retry-after':'5','x-ratelimit-remaining':'10'}),lambda:'token','https://api.github.com',quota_observer=lambda h,s:seen.append((h,s)))
    with pytest.raises(ProviderFailure):provider.request('GET','/test')
    assert seen==[({'retry-after':'5','x-ratelimit-remaining':'10'},429)]

def test_case_insensitive_rate_headers():
    p=JsonProvider(Stub(403,{'retry-after':'12'}),lambda:'token','https://api.github.com')
    with pytest.raises(ProviderFailure) as ex:p.request('GET','/test')
    assert ex.value.status==403

def test_normal_data_response():
    p=JsonProvider(Stub(),lambda:'token','https://api.github.com')
    assert p.request('GET','/test')=={'result':'ok'}
