"""Reject falsified source hashes, package capabilities and role readiness claims."""
import hashlib
import json
from copy import deepcopy

import pytest
from studio.organization.verification import (
    DirectoryContentSource, OrganizationError, checked_path, require_digest,
    require_id, verify_blob, role_readiness, package_manifest,
)


def sha(data):return hashlib.sha256(data).hexdigest()


def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':')).encode()


class Sources:
    def __init__(self, data=()):self.objects=dict(data)
    def get(self, key):return self.objects.get(key)


class Host:
    def tool_available(self, tool):return tool=='compiler'
    def validator_passed(self, digest):return digest=='a'*64


def role():
    body=b'Use only authorized commands.'
    manifest={'role_id':'coder','revision':'1',
              'resources':{'instructions':[{'path':'docs/role.md','sha256':sha(body)}]},
              'required_tools':['compiler'], 'smoke_validators':['a'*64],
              'reasoning_tier':'high','personality':'direct'}
    raw=canonical(manifest)
    return ({'role_id':'coder','revision':'1','manifest_digest':sha(raw)},
            Sources(((sha(raw),raw),(sha(body),body))),manifest)


def test_role_readiness_with_verified_resources_and_host():
    requested,src,body=role()
    status=role_readiness(requested,src,Host())
    assert status.state=='ready' and not status.missing
    assert status.reasoning_tier=='high'
    assert status.personality=='direct'


def test_missing_manifest_or_resource_cannot_be_ready():
    request,src,body=role()
    assert role_readiness(request,Sources(),Host()).state=='unavailable'
    src.objects.pop(body['resources']['instructions'][0]['sha256'])
    state=role_readiness(request,src,Host())
    assert state.state=='unavailable'
    assert state.missing[0].startswith('instructions:')


def test_host_unavailable_missing_tool_validator_is_reported():
    request,src,body=role()
    state=role_readiness(request,src,None)
    assert state.state=='unavailable'
    assert 'tool:compiler' in state.missing
    assert 'smoke:'+'a'*64 in state.missing


@pytest.mark.parametrize('mutation',[
    lambda m:m.update({'role_id':'wrong'}),
    lambda m:m.update({'revision':'old'}),
    lambda m:m.update({'resources':[]}),
    lambda m:m.update({'resources':{'instructions':[]}}),
    lambda m:m['resources'].update({'unknown':[]}),
    lambda m:m['resources'].update({'scripts':[None]}),
    lambda m:m.update({'required_tools':[None]}),
    lambda m:m.update({'smoke_validators':['wrong-digest']}),
    lambda m:m.update({'reasoning_tier':'undefined'}),
    lambda m:m.update({'personality':'x'*513}),
])
def test_role_manifest_malformed_or_unverified_metadata_rejected(mutation):
    request,src,base=role()
    bad=deepcopy(base)
    mutation(bad)
    raw=canonical(bad)
    request['manifest_digest']=sha(raw)
    src.objects[sha(raw)]=raw
    with pytest.raises(OrganizationError):role_readiness(request,src,Host())


@pytest.mark.parametrize('bad',[
    '/absolute/path','../escape','a/../other','a/./other','C:/windows',
    'back\\slash','null\x00byte',None,'',
])
def test_resource_paths_are_explicitly_confined(bad):
    with pytest.raises(OrganizationError):checked_path(bad)


def test_digest_identity_and_content_rechecked():
    for value in (None,'A'*64,'0'*63):
        with pytest.raises(OrganizationError):require_digest(value)
    for value in ('',None,'../bad','x'*129):
        with pytest.raises(OrganizationError):require_id(value)
    body=b'bytes'
    assert verify_blob(Sources(((sha(body),body),)),sha(body))==body
    with pytest.raises(OrganizationError):verify_blob(Sources(((sha(body),b'tampered'),)),sha(body))


def package():
    return {'manifest_version':'1','package_id':'pkg','package_version':'1',
            'kind':'tool','capabilities':[], 'dependencies':[],
            'entrypoints':[], 'resources':[]}


def test_verified_minimal_package_manifest_is_valid():
    doc=package()
    assert package_manifest(canonical(doc),Sources())==doc


@pytest.mark.parametrize('field,value',[
    ('manifest_version','2'),('package_id',''),('package_version',None),
    ('kind','not-a-kind'),('capabilities',['same','same']),
    ('capabilities',[None]),('dependencies',['wrong-digest']),
    ('resources',{}),('entrypoints',{}),('license_id',123),
])
def test_invalid_package_manifest_fails_before_approval(field,value):
    doc=package();doc[field]=value
    with pytest.raises(OrganizationError):package_manifest(canonical(doc),Sources())


def test_package_rejects_extra_keys_and_missing_required_fields():
    with pytest.raises(OrganizationError):package_manifest(canonical({**package(),'unknown':True}),Sources())
    doc=package();del doc['kind']
    with pytest.raises(OrganizationError):package_manifest(canonical(doc),Sources())


def test_package_resource_missing_changed_or_wrong_size_rejected():
    body=b'hello'
    base=package()
    base['resources']=[{'path':'src/module.sh','sha256':sha(body),'byte_size':len(body)}]
    for source,size in ((Sources(),len(body)),(Sources(((sha(body),b'bad'),)),len(body)),
                        (Sources(((sha(body),body),)),99)):
        doc=deepcopy(base);doc['resources'][0]['byte_size']=size
        with pytest.raises(OrganizationError):package_manifest(canonical(doc),source)


def test_package_rejects_duplicate_resources_and_invalid_entrypoint():
    body=b'hello'
    doc=package()
    resource={'path':'src/module.sh','sha256':sha(body),'byte_size':len(body)}
    doc['resources']=[resource,dict(resource)]
    with pytest.raises(OrganizationError):package_manifest(canonical(doc),Sources(((sha(body),body),)))
    doc['resources']=[resource]
    doc['entrypoints']=[{'id':'script','executable_digest':sha(body),'args_schema_digest':sha(body),
                         'output_schema_digest':sha(body),'required_isolation':'sandbox',
                         'executable_path':'src/missing.sh','args_schema_path':'src/module.sh',
                         'output_schema_path':'src/module.sh'}]
    with pytest.raises(OrganizationError):package_manifest(canonical(doc),Sources(((sha(body),body),)))


def test_directory_source_detects_tampering(tmp_path):
    body=b'checked'
    name=sha(body)
    (tmp_path/'resource-index.json').write_bytes(canonical({name:'assets/data'}))
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets/data').write_bytes(body)
    source=DirectoryContentSource(str(tmp_path))
    assert source.get(name)==body
    assert source.get('0'*64) is None
    (tmp_path/'assets/data').write_bytes(b'changed')
    with pytest.raises(OrganizationError,match='digest'):
        source.get(name)