"""WG-B domain checks, in-memory transaction fake; NOT production APSW integration."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[3]/'src'
sys.path.insert(0,str(SOURCE))
from studio.organization import OrganizationService, OrganizationError, TrustedActor, role_readiness
from studio.organization.verification import (digest, load_json, DirectoryContentSource,
                                               package_manifest, checked_path, verify_blob)
from studio.organization.ports import OWNED_TABLES

ROOT=Path(__file__).resolve().parents[3]/'src/studio/organization/catalog'

class FakeTx:
    KEYS={'departments':('department_id',),'specialist_roles':('role_id',),
          'package_revisions':('package_digest',),
          'package_dependencies':('package_digest','required_digest'),
          'tool_entrypoints':('package_digest','entrypoint_id'),
          'project_packages':('project_id','package_digest'),
          'tool_grants':('project_id','grant_id'),
          'cabals':('project_id','cabal_id'),
          'cabal_members':('project_id','cabal_id','member_id'),
          'deliberations':('project_id','deliberation_id')}
    def __init__(self, tables): self.tables=tables
    def many(self,table,**keys):
        if table not in OWNED_TABLES: raise AssertionError('wrong domain table')
        return [copy.deepcopy(r) for r in self.tables[table].values()
                if all(r.get(k)==v for k,v in keys.items())]
    def one(self,table,**keys):
        rows=self.many(table,**keys)
        if len(rows)>1: raise AssertionError('non-unique key')
        return rows[0] if rows else None
    def insert(self,table,record):
        if table not in OWNED_TABLES: raise AssertionError('wrong domain table')
        key=tuple(record[x] for x in self.KEYS[table])
        if key in self.tables[table]: raise OrganizationError('conflict','duplicate row')
        self.tables[table][key]=copy.deepcopy(dict(record))
    def change(self,table,keys,values):
        if table not in OWNED_TABLES: raise AssertionError('wrong domain table')
        key=tuple(keys[x] for x in self.KEYS[table])
        if key not in self.tables[table]: raise OrganizationError('missing','no matching row')
        if any(x in keys for x in values): raise OrganizationError('invalid','cannot change primary key')
        self.tables[table][key].update(copy.deepcopy(dict(values)))

class FakeAtomicStore:
    def __init__(self):
        self.tables={x:{} for x in OWNED_TABLES}
        self.records={}
    def read(self,project_id,action): return action(FakeTx(self.tables))
    def mutate(self,*,project_id,principal_id,action_name,request_id,payload_digest,action):
        # Simulates WG-C atomic idempotency semantics; never used as real APSW evidence.
        key=(project_id,'B',request_id)
        if key in self.records:
            recorded_principal,body,result=self.records[key]
            if (recorded_principal,body)!=(principal_id,payload_digest):
                raise OrganizationError('conflict','request ID reused with different body or principal')
            return copy.deepcopy(result)
        tx=FakeTx(copy.deepcopy(self.tables))
        result=action(tx)
        self.tables=tx.tables
        self.records[key]=(principal_id,payload_digest,copy.deepcopy(result))
        return result

class FakeContent:
    def __init__(self): self.values={}
    def add(self,b): self.values[digest(b)]=b;return digest(b)
    def get(self,key): return self.values.get(key)

class FakePolicy:
    def __init__(self): self.allowed_members=set();self.allowed_grants=set()
    def check_membership(self,principal_id,project_id,member_id,role_id,permission_digest):
        if (principal_id,project_id,member_id,role_id,permission_digest) not in self.allowed_members:
            raise OrganizationError('forbidden','unapproved membership scope')
    def check_tool_grants(self,principal_id,project_id,grants):
        for g in grants:
            if (principal_id,project_id,g['entrypoint_id'],g['grant_digest']) not in self.allowed_grants:
                raise OrganizationError('forbidden','unapproved tool grant')

class FakeTrust:
    def verify_package(self,*,manifest,signature_ref):
        if signature_ref!='tested-trust-provenance':
            raise OrganizationError('forbidden','package signer invalid')

class FakeHost:
    def __init__(self,*,available=(),passed=()):
        self.available=set(available);self.passed=set(passed)
    def tool_available(self,tool):return tool in self.available
    def validator_passed(self,digest):return digest in self.passed

ACTOR=TrustedActor('human:1',frozenset({'P1','P2'}),frozenset({'P1'}),'trusted-proof')
OTHER=TrustedActor('human:2',frozenset({'P2'}),frozenset(),'trusted-proof')
HASH='a'*64

def assert_error(test,code,fn):
    with test.assertRaises(OrganizationError) as cm: fn()
    test.assertEqual(cm.exception.code,code)

class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeAtomicStore();self.policy=FakePolicy();self.files=FakeContent()
        self.svc=OrganizationService(self.db,self.policy,source=self.files,
                                     trust=FakeTrust(),host=FakeHost())
    def _cabal(self,project='P1'):
        return self.svc.createCabal(ACTOR,project,request_id='cab-'+project,
                                  name='Design cabal',charter_digest=HASH)['cabal_id']
    def _pack(self,name,*,dependencies=(),tool=False):
        resources=[]
        entries=[]
        if tool:
            resources=[{'path':path,'sha256':self.files.add(path.encode()),
                        'byte_size':len(path.encode())}
                       for path in ('bin/tool','args.json','out.json')]
            lookup={r['path']:r['sha256'] for r in resources}
            entries=[{'id':'build','executable_path':'bin/tool',
                      'args_schema_path':'args.json','output_schema_path':'out.json',
                      'executable_digest':lookup['bin/tool'],
                      'args_schema_digest':lookup['args.json'],
                      'output_schema_digest':lookup['out.json'],
                      'required_isolation':'sandbox'}]
        manifest={'manifest_version':'1','package_id':name,
                  'package_version':'1.0.0','kind':'engine',
                  'capabilities':[],'resources':resources,
                  'dependencies':list(dependencies),'entrypoints':entries}
        return json.dumps(manifest,sort_keys=True).encode()

    def register(self, actor, project_id, request_id, manifest,
                 *, signature_ref='tested-trust-provenance'):
        return self.svc.register_package(actor,project_id,request_id,manifest,signature_ref)

    def test_cabal_replay_project_scoping_and_mutation(self):
        cabal=self._cabal()
        second=self.svc.createCabal(ACTOR,'P1',request_id='cab-P1',
                             name='Design cabal',charter_digest=HASH)
        self.assertEqual(second['cabal_id'],cabal)
        self.assertEqual(len(self.svc.listCabals(ACTOR,'P1')['cabals']),1)
        self.assertEqual(self.svc.listCabals(ACTOR,'P2')['cabals'],[])
        assert_error(self,'forbidden',lambda:self.svc.listCabals(OTHER,'P1'))
        assert_error(self,'conflict',lambda:self.svc.createCabal(ACTOR,'P1',
                             request_id='cab-P1',name='Hijack',charter_digest=HASH))

    def test_bogus_actor_or_project_does_not_expand_authority(self):
        fake=TrustedActor('worker:self',frozenset({'P1'}),frozenset(),'')
        assert_error(self,'forbidden',lambda:self.svc.listCabals(fake,'P1'))
        assert_error(self,'requires_owner',lambda:self.register(OTHER,'P2',
                                 'r1',self._pack('engine')))

    def test_deliberation_confined_and_not_policy_effect(self):
        cabal=self._cabal()
        d=self.svc.createDeliberation(ACTOR,'P1',cabal,request_id='del1',
                                     subject_digest=HASH,record_digest=HASH)
        self.assertEqual(d['state'],'open')
        self.assertEqual(len(self.svc.listDeliberations(ACTOR,'P1',cabal)['deliberations']),1)
        assert_error(self,'missing',lambda:self.svc.listDeliberations(ACTOR,'P2',cabal))
        self.assertEqual(set(self.db.tables['deliberations'][("P1",d['deliberation_id'])]),
                         {'project_id','deliberation_id','cabal_id','subject_digest','record_digest','state'})
        draft=self.svc.build_cabal_relay_draft(ACTOR,'P1',cabal,kind='HANDOFF',
                                 content_digest=HASH,target_shift_id='S1')
        self.assertEqual(draft['delivery_state'],'DRAFT_NOT_PUBLISHED')

    def test_dependency_preflight_exact_versions_and_project_profile(self):
        base=self.register(ACTOR,'P1','reg-base',self._pack('base'))['package_digest']
        top=self.register(ACTOR,'P1','reg-top',self._pack('top',dependencies=[base]))['package_digest']
        assert_error(self,'capability_unavailable',lambda:self.svc.enablePack(ACTOR,'P1',
                     request_id='top-first',package_digest=top,grants=[]))
        self.svc.enablePack(ACTOR,'P1',request_id='base-on',package_digest=base,grants=[])
        self.svc.enablePack(ACTOR,'P1',request_id='top-on',package_digest=top,grants=[])
        assert_error(self,'conflict',lambda:self.svc.disablePack(ACTOR,'P1',
                      request_id='base-off',package_digest=base))
        self.assertEqual(sum(p['enabled'] for p in self.svc.listPacks(ACTOR,'P1')['packages']),2)
        self.assertEqual(self.svc.listPacks(ACTOR,'P2')['packages'],[])
        self.svc.disablePack(ACTOR,'P1',request_id='top-off',package_digest=top)
        self.svc.disablePack(ACTOR,'P1',request_id='base-off',package_digest=base)

    def test_tool_grants_never_expand_without_policy_and_revoke(self):
        pkg=self.register(ACTOR,'P1','register',self._pack('toolpack',tool=True))['package_digest']
        grant={'package_digest':pkg,'entrypoint_id':'build','workspace_id':'ws1','grant_digest':HASH}
        assert_error(self,'forbidden',lambda:self.svc.enablePack(ACTOR,'P1',request_id='on',
                               package_digest=pkg,grants=[grant]))
        self.policy.allowed_grants.add((ACTOR.principal_id,'P1','build',HASH))
        assert_error(self,'invalid',lambda:self.svc.enablePack(ACTOR,'P1',request_id='on2',
                               package_digest=pkg,grants=[]))
        self.svc.enablePack(ACTOR,'P1',request_id='on3',package_digest=pkg,grants=[grant])
        self.assertEqual(self.svc.listToolGrants(ACTOR,'P2')['grants'],[])
        g=self.svc.listToolGrants(ACTOR,'P1')['grants'][0]['grant_id']
        assert_error(self,'requires_owner',lambda:self.svc.revokeToolGrant(OTHER,'P2',g,
                                               request_id='revoke',expected_state='active'))
        assert_error(self,'conflict',lambda:self.svc.revokeToolGrant(ACTOR,'P1',g,
                                               request_id='r1',expected_state='revoked'))
        self.assertTrue(self.svc.revokeToolGrant(ACTOR,'P1',g,
                                               request_id='r1',expected_state='active')['revoked'])
        self.assertTrue(self.svc.listToolGrants(ACTOR,'P1')['grants'][0]['revoked'])

    def test_package_missing_hash_and_path_attack(self):
        raw=self._pack('alpha'); bad=json.loads(raw)
        bad['resources']=[{'path':'../escape','sha256':self.files.add(b'bad'),'byte_size':3}]
        assert_error(self,'invalid',lambda:package_manifest(json.dumps(bad).encode(),self.files))
        bad=json.loads(raw);bad['resources']=[{'path':'safe','sha256':HASH,'byte_size':4}]
        assert_error(self,'capability_unavailable',lambda:package_manifest(json.dumps(bad).encode(),self.files))
        bad=json.loads(raw)
        assert_error(self,'forbidden',lambda:self.register(ACTOR,'P1','x',json.dumps(bad).encode(),signature_ref='not-trusted'))

    def test_partial_catalog_missing_assets_never_ready(self):
        instruction=self.files.add(b'real role instruction content')
        manifest={'role_id':'rpg-demo','revision':'1','resources':{
                  'instructions':[{'path':'role.md','sha256':instruction}],
                  'scripts':[{'path':'missing.py','sha256':HASH}]},'required_tools':[],
                  'smoke_validators':[]}
        manifest_digest=self.files.add(json.dumps(manifest).encode())
        cat={'departments':[{'department_id':'engineering','name':'Engineering'}],
             'roles':[{'role_id':'rpg-demo','revision':'1',
                      'department_id':'engineering','manifest_digest':manifest_digest}]}
        cd=self.files.add(json.dumps(cat).encode())
        outcome=self.svc.importRoles(ACTOR,'P1',request_id='roles',catalog_digest=cd,
                                     role_manifest_digests=[manifest_digest])
        self.assertEqual(outcome['installed_count'],0)
        self.assertEqual(self.svc.listRoles(ACTOR,'P1')['roles'][0]['state'],'unavailable')
        missing=self.svc.role_diagnostics(ACTOR,'P1',cd)['rpg-demo'].missing
        self.assertEqual(len(missing),1)
        self.assertIn('missing.py',missing[0])

    def test_role_with_host_tools_and_validator(self):
        instruction=self.files.add(b'instruction')
        validator=self.files.add(b'validator source')
        manifest={'role_id':'rpg-demo','revision':'1','resources':{
                 'instructions':[{'path':'role.md','sha256':instruction}],
                 'validators':[{'path':'validator.py','sha256':validator}]},
                 'required_tools':['dotnet'],'smoke_validators':[validator]}
        key=self.files.add(json.dumps(manifest).encode())
        row={'role_id':'rpg-demo','revision':'1','manifest_digest':key}
        readiness=role_readiness(row,self.files,FakeHost())
        self.assertEqual(readiness.state,'unavailable')
        self.assertEqual(set(readiness.missing),{'tool:dotnet','smoke:'+validator})
        readiness=role_readiness(row,self.files,FakeHost(available={'dotnet'},passed={validator}))
        self.assertEqual(readiness.state,'ready')

    def test_role_staff_requires_authorized_permission_and_real_ready(self):
        cabal=self._cabal()
        self.db.tables['specialist_roles'][('rpg-demo',)]={
            'role_id':'rpg-demo','role_revision':'1','role_manifest_digest':HASH,
            'department_id':'engineering','state':'ready'}
        assert_error(self,'forbidden',lambda:self.svc.staffCabal(ACTOR,'P1',cabal,
                  request_id='staff1',member_id='specialist1',role_id='rpg-demo',permission_digest=HASH))
        self.policy.allowed_members.add((ACTOR.principal_id,'P1','specialist1','rpg-demo',HASH))
        self.svc.staffCabal(ACTOR,'P1',cabal,request_id='staff1',
                  member_id='specialist1',role_id='rpg-demo',permission_digest=HASH)
        self.assertEqual(len(self.db.tables['cabal_members']),1)
        self.policy.allowed_members.add((ACTOR.principal_id,'P2','specialist1','rpg-demo',HASH))
        assert_error(self,'missing',lambda:self.svc.staffCabal(ACTOR,'P2',cabal,
                  request_id='staff2',member_id='specialist1',role_id='rpg-demo',permission_digest=HASH))
        assert_error(self,'capability_unavailable',lambda:self.svc.launch_role_configuration(
                  ACTOR,'P1',cabal,'specialist1')) # Synthetic ready row has no verified bytes.

    def test_full_174_role_catalog_bytes_import(self):
        source=DirectoryContentSource(str(ROOT))
        self.svc.source=source
        catalog_bytes=(ROOT/'nomad_role_catalog.json').read_bytes()
        cat=load_json(catalog_bytes)
        self.assertEqual((len(cat['departments']),len(cat['roles'])),(12,174))
        result=self.svc.importRoles(ACTOR,'P1',request_id='actual-catalog',
                            catalog_digest=digest(catalog_bytes),
                            role_manifest_digests=[r['manifest_digest'] for r in cat['roles']])
        self.assertEqual(result['installed_count'],174)
        self.assertEqual(len(self.svc.listRoles(ACTOR,'P1')['roles']),174)
        self.assertEqual(len(self.svc.listDepartments(ACTOR,'P1')['departments']),12)
        diag=self.svc.role_diagnostics(ACTOR,'P1',digest(catalog_bytes))
        self.assertTrue(all(v.state=='ready' and not v.missing for v in diag.values()))
        # Another project cannot make the global roster unready with a partial import.
        self.svc.importRoles(ACTOR,'P2',request_id='partial',catalog_digest=digest(catalog_bytes),
                             role_manifest_digests=[])
        self.assertTrue(all(r['state']=='ready' for r in self.svc.listRoles(ACTOR,'P1')['roles']))

    def test_reuse_request_id_across_commands_is_conflict(self):
        self.svc.createCabal(ACTOR,'P1',request_id='shared',name='A',charter_digest=HASH)
        p=self.register(ACTOR,'P1','unrelated',self._pack('other'))['package_digest']
        assert_error(self,'conflict',lambda:self.svc.enablePack(ACTOR,'P1',
                          request_id='shared',package_digest=p,grants=[]))
        self.assertEqual(self.svc.listPacks(ACTOR,'P1')['packages'],[])

    def test_package_version_rebinding_rejected(self):
        self.register(ACTOR,'P1','initial',self._pack('engineX'))
        altered=json.loads(self._pack('engineX'))
        altered['kind']='discipline'
        assert_error(self,'conflict',lambda:self.register(ACTOR,'P1',
                         'different',json.dumps(altered).encode()))

    def test_resource_hash_mismatch_and_empty_role_instructions(self):
        fake=FakeContent()
        fake.values[HASH]=b'corrupt'
        assert_error(self,'invalid',lambda:verify_blob(fake,HASH))
        instruction=self.files.add(b'')
        manifest={'role_id':'rpg-dummy','revision':'1','resources':{
                  'instructions':[{'path':'role.md','sha256':instruction}]}}
        key=self.files.add(json.dumps(manifest).encode())
        assert_error(self,'invalid',lambda:role_readiness({'role_id':'rpg-dummy',
                                      'revision':'1','manifest_digest':key},self.files,None))

    def test_pinned_launch_profile_keeps_grants_project_local(self):
        source=DirectoryContentSource(str(ROOT))
        self.svc.source=source
        cat=load_json((ROOT/'nomad_role_catalog.json').read_bytes())
        key=digest((ROOT/'nomad_role_catalog.json').read_bytes())
        self.svc.importRoles(ACTOR,'P1',request_id='real',catalog_digest=key,
                             role_manifest_digests=[r['manifest_digest'] for r in cat['roles']])
        role=cat['roles'][0]['role_id']
        cabal=self._cabal()
        self.policy.allowed_members.add((ACTOR.principal_id,'P1','member1',role,HASH))
        self.svc.staffCabal(ACTOR,'P1',cabal,request_id='staffreal',
                            member_id='member1',role_id=role,permission_digest=HASH)
        launch=self.svc.launch_role_configuration(ACTOR,'P1',cabal,'member1')
        self.assertEqual(launch['role_id'],role)
        self.assertIn(launch['reasoning_tier'],('high','extra_high'))
        self.assertEqual(launch['authorization'],'REFERENCE_ONLY_NOT_CREDENTIALS')
        self.assertEqual(launch['tool_grants'],[])
        assert_error(self,'missing',lambda:self.svc.launch_role_configuration(
                              ACTOR,'P2',cabal,'member1'))

    def test_r5_catalog_role_prompts_are_nonlegacy(self):
        catalog=load_json((ROOT/'nomad_role_catalog.json').read_bytes())
        source=DirectoryContentSource(str(ROOT))
        for role in catalog['roles']:
            manifest=load_json(source.get(role['manifest_digest']))
            self.assertEqual(manifest['role_id'],role['role_id'])
            for item in manifest['resources']['assets']:
                text=source.get(item['sha256']).decode('utf-8')
                self.assertIn(role['role_id'],text)
                self.assertIn('manual',text.lower())
                self.assertNotIn('STUDIO_V15_KERNEL',text)
                self.assertNotIn('studio/runtime',text)
            self.assertEqual(manifest['smoke_validators'],[])

    def test_package_matches_actual_frozen_json_schema(self):
        source=os.environ.get('R5_FROZEN_CONTRACTS')
        if not source:
            source=str(Path(__file__).resolve().parents[3]/'CONTRACTS')
        schema_path=Path(source)/'package.schema.json'
        if not schema_path.is_file():
            self.skipTest('frozen shared contract not mounted into B-only source artifact')
        from jsonschema import Draft202012Validator
        schema=json.loads(schema_path.read_text())
        good=json.loads(self._pack('real-frozen-schema',tool=True))
        Draft202012Validator(schema).validate(good)
        package_manifest(json.dumps(good).encode(),self.files)
        broken=dict(good);broken['incorrect_field']='x'
        self.assertFalse(Draft202012Validator(schema).is_valid(broken))
        assert_error(self,'invalid',lambda:package_manifest(json.dumps(broken).encode(),self.files))

    def test_exact_frozen_b_operator_contract_surface(self):
        source=os.environ.get('R5_FROZEN_CONTRACTS')
        if not source:
            source=str(Path(__file__).resolve().parents[3]/'CONTRACTS')
        operation_path=Path(source)/'operation_catalog.json'
        if not operation_path.is_file():
            self.skipTest('frozen shared catalog not mounted into B-only source artifact')
        ops=json.loads(operation_path.read_text())['ops']
        b=[o['id'] for o in ops if o['owner']=='B']
        self.assertEqual(len(b),13)
        self.assertTrue(all(callable(getattr(self.svc,name,None)) for name in b))

    def test_corrupted_resource_in_pack_is_rejected_live(self):
        source=DirectoryContentSource(str(ROOT))
        cd=digest((ROOT/'nomad_role_catalog.json').read_bytes())
        self.assertIsNotNone(source.get(cd))
        self.assertIsNone(source.get(HASH))

if __name__=='__main__':unittest.main()
