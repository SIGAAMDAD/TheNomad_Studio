"""Frozen Studio API contract validation: malicious drift is rejected at startup."""
import copy
import json
import re
from pathlib import Path

import pytest

from studio.api.contracts import FrozenContracts
from studio.api.errors import ApiError

CONTRACTS = Path(__file__).resolve().parents[2] / 'CONTRACTS'


def catalog(tmp_path, *, openapi=None, operation_catalog=None, operation_semantics=None):
    dest=tmp_path / 'contracts'
    dest.mkdir()
    overrides={'openapi.json':openapi,'operation_catalog.json':operation_catalog,
               'operation_semantics.json':operation_semantics}
    for name,override in overrides.items():
        source=json.loads((CONTRACTS/name).read_text())
        if override is not None:
            override(source)
        (dest/name).write_text(json.dumps(source))
    return dest


def reject_catalog(tmp_path, name, update):
    options={name:update}
    with pytest.raises((ValueError,KeyError)):
        FrozenContracts(catalog(tmp_path,**options))


def test_successful_catalog_loads_all_frozen_operations():
    contracts=FrozenContracts(CONTRACTS)
    assert len(contracts.routes)==72
    assert len(contracts.operations)==72


@pytest.mark.parametrize('target,mutate',[
    ('openapi',lambda d: d.update(openapi='2.0')),
    ('operation_catalog',lambda d: d.update(contract_version='0.0')),
    ('operation_catalog',lambda d: d['ops'].append(copy.deepcopy(d['ops'][0]))),
    ('operation_semantics',lambda d: d['operations'].append(copy.deepcopy(d['operations'][0]))),
    ('operation_catalog',lambda d: d['ops'][0].update(method='WRONG')),
    ('operation_catalog',lambda d: d['ops'][0].update(auth='untrusted')),
    ('operation_catalog',lambda d: d['ops'][0].update(mutation=not d['ops'][0]['mutation'])),
    ('operation_catalog',lambda d: d['ops'][0].update(response_fields={})),
    ('operation_catalog',lambda d: d['ops'][0].update(request_fields={'invented':{'type':'string','required':True}})),
    ('operation_semantics',lambda d: d['operations'][0].update(path='/not-the-route')),
    ('openapi',lambda d: d['components']['schemas'][next(iter(d['components']['schemas']))].update(invalidKeyword=True)),
])
def test_contracts_reject_drift_in_any_frozen_source(tmp_path,target,mutate):
    reject_catalog(tmp_path,target,mutate)


def validator():
    return FrozenContracts.__new__(FrozenContracts)


@pytest.mark.parametrize('bad',[
    {'$ref':'https://host.example/schema'},
    {'type':'unsafe-type'},
    {'type':'string','additionalUnknownKeyword':True},
    {'type':'array','items':{'type':'unsafe'}},
    {'type':'object','properties':{'sub':{'invalid':True}}},
])
def test_schema_rejects_external_ref_unknown_types_keywords_and_nested_drift(bad):
    f=validator()
    f.schemas={}
    with pytest.raises(ValueError):
        f._check_schema(bad)


def test_schema_validates_nested_required_and_rejects_extra_fields():
    f=validator();f.schemas={}
    schema={'type':'object','required':['key'],'additionalProperties':False,
            'properties':{'key':{'type':'string','minLength':2},
                          'values':{'type':'array','items':{'type':'integer','minimum':1,'maximum':3}}}}
    f.validate({'key':'ok','values':[1,2,3]},schema)
    for value in ({}, {'key':'x'}, {'key':'ok','extraneous':1},
                  {'key':'ok','values':[0]}, {'key':'ok','values':[4]},
                  {'key':'ok','values':['bad']}, {'key':'ok','values':{}}):
        with pytest.raises(ApiError):
            f.validate(value,schema)


def test_schema_rejects_bad_literal_enums_and_unsupported_primitive_types():
    f=validator();f.schemas={}
    for value, schema in [('bad',{'type':'string','enum':['good']}),
                          (True, {'type':'integer'}),
                          (None, {'type':'string'})]:
        with pytest.raises(ApiError):
            f.validate(value,schema)


def test_route_resolver_rejects_unknown_operations_and_encoded_slash():
    f=FrozenContracts(CONTRACTS)
    with pytest.raises(ApiError):
        f.resolve('GET','/unknown-command')
    candidate=next(op for op in f.routes if '{' in op.path)
    encoded=re.sub(r'\{[^{}]+\}', '%2F',candidate.path)
    with pytest.raises(ApiError):
        f.resolve(candidate.method,encoded)