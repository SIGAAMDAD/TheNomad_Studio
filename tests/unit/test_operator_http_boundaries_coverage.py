"""J HTTP adapter rejects ambiguous requests before invoking any domain mutation."""
import io
import json
from types import SimpleNamespace

import pytest

from studio.api.errors import ApiError
from studio.api.http import (_decode_query,_decode_body,_no_duplicates,_bearer,StudioWSGI)


class Contracts:
    def validate(self,value,schema,location='$'):
        if schema.get('type')=='integer' and type(value) is not int:
            raise ApiError('invalid','incorrect integer')


def operation(*,params=(),mutation=True,schema=None):
    return SimpleNamespace(parameters=params,mutation=mutation,request_schema=schema)


def fail(code,fn):
    with pytest.raises(ApiError) as err:fn()
    assert err.value.code==code


def test_json_duplicate_keys_fail_at_ingress():
    with pytest.raises(ValueError,match='duplicate'):
        _no_duplicates([('request_id','first'),('request_id','second')])
    assert _no_duplicates([('request_id','first'),('other',1)])=={'request_id':'first','other':1}


def test_bearer_requires_exact_single_token():
    for raw in ('', 'Basic abc', 'Bearer ', 'Bearer two tokens', 'Bearer'):
        fail('forbidden',lambda:_bearer({'HTTP_AUTHORIZATION':raw}))
    assert _bearer({'HTTP_AUTHORIZATION':'bearer token'})=='token'


@pytest.mark.parametrize('raw',('a', 'unknown=1','n=1&n=2','n=1&bad=3','n=-1','n=1.5'))
def test_query_is_strict_and_rejects_unknown_or_repeated_parameters(raw):
    op=operation(params=({'name':'n','in':'query','required':True,'schema':{'type':'integer'}},))
    fail('invalid',lambda:_decode_query(raw,op,Contracts()))


def test_required_boolean_and_integer_query_are_cast_exactly():
    op=operation(params=({'name':'active','in':'query','required':True,'schema':{'type':'boolean'}},
                         {'name':'n','in':'query','required':False,'schema':{'type':'integer'}},))
    assert _decode_query('active=true&n=5',op,Contracts())=={'active':True,'n':5}
    assert _decode_query('active=false',op,Contracts())=={'active':False}
    fail('invalid',lambda:_decode_query('active=maybe',op,Contracts()))
    fail('invalid',lambda:_decode_query('n=5',op,Contracts()))


def body_env(raw,**changes):
    env={'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json',
         'wsgi.input':io.BytesIO(raw)}
    env.update(changes)
    return env


@pytest.mark.parametrize('raw,changes', [
    (b'{}',{'CONTENT_TYPE':'text/plain'}),
    (b'{}',{'CONTENT_LENGTH':'-2'}),
    (b'{}',{'CONTENT_LENGTH':'abc'}),
    (b'{}',{'CONTENT_LENGTH':'1048577'}),
    (b'{',{}),
    (b'{"x":NaN}',{}),
    (b'{"x":1,"x":2}',{}),
    (b'{}',{'CONTENT_LENGTH':'30'}),
])
def test_mutation_bodies_require_bounded_exact_canonical_json(raw,changes):
    op=operation(schema={'type':'object'})
    fail('invalid',lambda:_decode_body(body_env(raw,**changes),op,Contracts()))


def test_no_body_for_get_and_missing_content_length_for_mutation():
    assert _decode_body(body_env(b''),operation(mutation=False),Contracts()) is None
    fail('invalid',lambda:_decode_body(body_env(b'{}'),operation(mutation=False),Contracts()))
    fail('invalid',lambda:_decode_body({'wsgi.input':io.BytesIO(b'{}')},
                                      operation(schema={'type':'object'}),Contracts()))
    assert _decode_body(body_env(b'{"request_id":"x"}'),
                        operation(schema={'type':'object'}),Contracts())=={'request_id':'x'}


def test_wsgi_does_not_leak_internal_error_or_call_unverified_service():
    class Routes:
        def resolve(self,method,path):
            if path=='/throw':
                raise RuntimeError('secret token and database password')
            raise ApiError('missing','unknown route')
    class Service:
        contracts=Routes()
    app=StudioWSGI(Service())
    for path,status in (('/throw',500),('/missing',404)):
        env={'PATH_INFO':path,'REQUEST_METHOD':'GET'}
        outputs=[]
        result=b''.join(app(env,lambda s,headers:outputs.append((s,headers))))
        assert int(outputs[0][0].split()[0])==status
        assert b'secret' not in result
        assert any(k=='Cache-Control' and v=='no-store' for k,v in outputs[0][1])