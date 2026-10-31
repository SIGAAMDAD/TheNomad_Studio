"""J route and schema interpreter rejects unsupported shapes and unsafe identifiers."""
import re
import pytest

from studio.api.contracts import FrozenContracts, Operation
from studio.api.errors import ApiError


def contracts():
    obj=FrozenContracts.__new__(FrozenContracts)
    obj.schemas={'Token':{'type':'string','minLength':3},'Status':{'type':'string','enum':['ok']}}
    obj.routes=[]
    return obj


@pytest.mark.parametrize('bad',[42,{'unknownSchema':1},{'type':'imaginary'},{'type':['string','invalid']}])
def test_schema_checker_fails_closed_on_unsupported_schema(bad):
    c=contracts()
    with pytest.raises((ValueError,TypeError)):
        c._check_schema(bad)


def test_schema_checker_and_reference_resolution():
    c=contracts()
    assert c._resolve({'$ref':'#/components/schemas/Token'}) == {'type':'string','minLength':3}
    assert c._check_schema({'type':'array','items':{'$ref':'#/components/schemas/Token'}}) is None
    assert c._check_schema({'type':'object','properties':{'x':{'type':'integer'}}}) is None
    with pytest.raises(ValueError,match='external'):
        c._resolve({'$ref':'https://foreign/schema'})


@pytest.mark.parametrize('value,schema',[
    (None,{'type':'string'}), ('wrong',{'type':'integer'}),
    (True,{'type':'number'}),('other',{'type':'string','enum':['ok']}),
    ({}, {'type':'object','required':['id']}),
    ({'id':1,'unknown':2},{'type':'object','properties':{'id':{'type':'integer'}},'additionalProperties':False}),
    ([1,None],{'type':'array','items':{'type':'integer'}}),
    ('x',{'type':'string','minLength':3}),
    (-1,{'type':'integer','minimum':0}), (9,{'type':'integer','maximum':8}),
])
def test_strict_validator_rejects_invalid_api_input(value,schema):
    with pytest.raises(ApiError) as err:
        contracts().validate(value,schema)
    assert err.value.code=='invalid'


def test_valid_nested_schema_resolves_references():
    c=contracts()
    c.validate({'token':'ready','items':[1,2]},
               {'type':'object','required':['token'], 'additionalProperties':False,
                'properties':{'token':{'$ref':'#/components/schemas/Token'},
                              'items':{'type':'array','items':{'type':'integer','minimum':1}}}})


def test_route_match_rejects_wrong_method_and_unsafe_path_tokens():
    c=contracts()
    op=Operation(name='read',owner='J',method='GET',path='/items/{id}',auth='project',
                 mutation=False,regex=re.compile(r'^/items/(?P<id>[^/]+)$'),parameters=(),
                 request_schema=None,response_schema={'type':'object'})
    c.routes=[op]
    matched,params=c.resolve('GET','/items/abc')
    assert matched is op and params=={'id':'abc'}
    with pytest.raises(ApiError,match='unknown'):
        c.resolve('POST','/items/abc')
    for unsafe in ('%2e%2e','%2F','%5C','%00'):
        with pytest.raises(ApiError,match='unsafe'):
            c.resolve('GET','/items/'+unsafe)