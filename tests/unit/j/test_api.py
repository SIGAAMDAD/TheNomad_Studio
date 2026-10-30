"""WG-J local tests: real contract bytes, no fabricated APSW/GitHub integration."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
import urllib.request
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from studio.api import (ApiError, ConfiguredOpaqueVerifier, DomainRegistry,
                        EventPageService, FrozenContracts, PrincipalProvisioner,
                        StudioOperatorService, StudioWSGI, VerifiedPrincipal, create_http_server)

CONTRACT_DIR = Path(os.environ.get('R5_CONTRACT_DIR', ROOT / 'CONTRACTS'))


class Grants:
    def __init__(self):
        self.mapping = {'owner': {'p1', 'p2'}, 'worker': {'p1'}}
    def granted_project_ids(self, principal_id):
        return self.mapping.get(principal_id, set())


class AtomicFake:
    """Unit-only serialized transaction simulation; not APSW integration."""
    def __init__(self):
        self.cache = {}
        self.called = []

    def execute_idempotent(self, *, owner, project_id, principal_id, request_id,
                           payload_digest, operation_id, command):
        key = (project_id, owner, request_id) if project_id else (principal_id, request_id)
        record = self.cache.get(key)
        if record:
            digest, result = record
            if digest != payload_digest:
                raise ApiError('conflict', 'request_id already used for another request')
            return result
        result = command()
        self.called.append((owner, project_id, principal_id, operation_id))
        self.cache[key] = (payload_digest, result)
        return result


class Domain:
    def __init__(self):
        self.calls = []
        self.result = None
    def __getattr__(self, name):
        def run(actor, **kwargs):
            self.calls.append((name, actor.copy(), kwargs))
            if self.result is not None:
                return self.result
            if name == 'createProject':
                return {'project_id': 'p1', 'revision': 1}
            if name == 'registerWorker':
                return {'worker_id': 'w1', 'state': 'registered'}
            if name == 'listProjects':
                return {'items': [
                    {'project_id': x, 'name': x, 'revision': 1, 'state': 'active'}
                    for x in ('p1', 'p2', 'secret')]}
            return {'project_id': 'p1', 'name': 'p1', 'revision': 1}
        return run


class EventReader:
    def __init__(self, events):
        self.events = events
        self.calls = []
    def scan_project_events(self, *, project_id, after, limit):
        self.calls.append((project_id, after, limit))
        return [e for e in self.events if e['project_id'] == project_id
                and e['event_seq'] > after][:limit]


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = FrozenContracts(CONTRACT_DIR)

    def setUp(self):
        secret_owner = 'owner.' + 'a' * 48
        secret_worker = 'worker.' + 'b' * 48
        self.owner_token, self.worker_token = secret_owner, secret_worker
        verifier = ConfiguredOpaqueVerifier({
            hashlib.sha256(secret_owner.encode()).hexdigest():
                VerifiedPrincipal('owner', 'configured-test', 'human-owner-subject', 'trusted-owner-proof', True),
            hashlib.sha256(secret_worker.encode()).hexdigest():
                VerifiedPrincipal('worker', 'configured-test', 'human-collaborator-subject', 'trusted-worker-proof', False),
        })
        self.grants = Grants()
        self.registry = DomainRegistry()
        self.domain = Domain()
        for owner in 'ABDEFGHIKM':
            self.registry.register_owner(owner, self.domain)
        self.tx = AtomicFake()
        self.reader = EventReader([])
        self.registry.register_owner('J', EventPageService(self.reader))
        self.service = StudioOperatorService(self.contracts, verifier, self.grants,
                                             self.registry, self.tx)
        self.app = StudioWSGI(self.service)

    def call(self, method, path, *, body=None, token=None, query='', raw=None,
             content_type='application/json'):
        env = {}
        setup_testing_defaults(env)
        env.update({'REQUEST_METHOD': method, 'PATH_INFO': path,
                    'QUERY_STRING': query, 'CONTENT_TYPE': content_type})
        if token is not None:
            env['HTTP_AUTHORIZATION'] = 'Bearer ' + token
        encoded = (json.dumps(body).encode() if raw is None and body is not None
                   else raw if raw is not None else b'')
        env['CONTENT_LENGTH'] = str(len(encoded))
        env['wsgi.input'] = io.BytesIO(encoded)
        reply = []
        data = b''.join(self.app(env, lambda status, headers: reply.append((status, headers))))
        status, headers = reply.pop()
        return int(status[:3]), json.loads(data), dict(headers)

    def test_all_72_routed_exactly_and_missing_handler_does_not_claim_success(self):
        self.assertEqual(len(self.contracts.operations), 72)
        for name, op in self.contracts.operations.items():
            example = op.path
            for part in ('project_id','cabal_id','bible_id','decision_id','feature_id',
                         'task_id','session_id','worker_id','shift_id','command_id','review_id','artifact_id','intent_id',
                         'release_id','effect_id','budget_id','message_id','case_id',
                         'notification_id','grant_id'):
                example = example.replace('{'+part+'}', part+'-1')
            route, params = self.contracts.resolve(op.method, example)
            self.assertEqual(route.name, name)
        self.assertEqual(self.registry.missing(self.contracts), [])

    def test_bootstrap_idempotency_and_owner_control(self):
        req = {'request_id':'first','name':'Test','charter_digest':'digest'}
        self.assertEqual(self.call('POST','/v1/projects',body=req,token=self.worker_token)[0],403)
        status, data, _ = self.call('POST','/v1/projects',body=req,token=self.owner_token)
        self.assertEqual((status, data), (200,{'project_id':'p1','revision':1}))
        self.assertEqual(self.call('POST','/v1/projects',body=req,token=self.owner_token)[0],200)
        self.assertEqual(len(self.domain.calls), 1)
        bad = dict(req, name='Altered')
        self.assertEqual(self.call('POST','/v1/projects',body=bad,token=self.owner_token)[1]['code'], 'conflict')
        self.assertEqual(len(self.domain.calls), 1)

    def test_project_fencing_and_membership_recheck(self):
        self.assertEqual(self.call('GET','/v1/projects/p2',token=self.worker_token)[0],403)
        self.assertEqual(self.call('GET','/v1/projects/p1',token=self.worker_token)[0],200)
        self.grants.mapping['worker'].clear()
        self.assertEqual(self.call('GET','/v1/projects/p1',token=self.worker_token)[0],403)
        self.assertEqual(len(self.domain.calls), 1)

    def test_list_projects_filters_backend_cross_project_result(self):
        self.assertEqual([x['project_id'] for x in self.call('GET','/v1/projects',token=self.worker_token)[1]['items']],['p1'])

    def test_owner_claims_ignored(self):
        request = {'request_id':'req','policy_digest':'hash','expected_revision':1,'rules':[],
                   'is_owner':True}
        status,data,_ = self.call('PUT','/v1/projects/p1/policy',body=request,token=self.worker_token)
        self.assertEqual(status,400)
        request.pop('is_owner')
        status,data,_ = self.call('PUT','/v1/projects/p1/policy',body=request,token=self.worker_token)
        self.assertEqual((status,data['code']), (403,'requires_owner'))
        status,data,_ = self.call('PUT','/v1/projects/p1/policy',body=request,token=self.owner_token)
        self.assertEqual(status,500)  # fake domain's response mismatches policy schema

    def test_mutation_replay_conflict_and_scope(self):
        req={'request_id':'t1','worker_id':'w1','browser_chat_ref':'browser-session','role_id':'coder'}
        status,data,_=self.call('POST','/v1/projects/p1/workers',body=req,token=self.worker_token)
        self.assertEqual(status,200)
        self.assertEqual(data['state'],'registered')
        self.assertEqual(self.call('POST','/v1/projects/p1/workers',body=req,token=self.worker_token)[0],200)
        self.assertEqual(len([x for x in self.domain.calls if x[0]=='registerWorker']),1)
        self.assertEqual(self.call('POST','/v1/projects/p1/workers',body={**req,'role_id':'reviewer'},token=self.worker_token)[1]['code'],'conflict')
        self.assertEqual(self.call('POST','/v1/projects/p2/workers',body=req,token=self.worker_token)[0],403)

    def test_reject_unknown_fields_wrong_types_duplicate_json(self):
        req={'request_id':'r','worker_id':42,'browser_chat_ref':'browser-session','role_id':'coder'}
        self.assertEqual(self.call('POST','/v1/projects/p1/workers',body=req,token=self.worker_token)[0],400)
        req['worker_id']='w1'
        req['actor']={'is_owner':True}
        self.assertEqual(self.call('POST','/v1/projects/p1/workers',body=req,token=self.worker_token)[0],400)
        status,_,_=self.call('POST','/v1/projects/p1/workers',raw=b'{"request_id":"a","request_id":"b"}',token=self.worker_token)
        self.assertEqual(status,400)
        self.assertEqual(len(self.domain.calls),0)

    def test_every_frozen_operation_accepts_valid_shapes_and_dispatches_to_its_owner(self):
        def sample(schema):
            schema = self.contracts._resolve(schema)
            if 'enum' in schema:
                return schema['enum'][0]
            kind = schema.get('type')
            if kind == 'object':
                return {key: sample(schema['properties'][key]) for key in schema.get('required', [])}
            if kind == 'array':
                return []
            if kind == 'string':
                return 'x'
            if kind == 'integer':
                return 1
            if kind == 'boolean':
                return True
            if isinstance(kind, list):
                return None if 'null' in kind else sample({'type':kind[0]})
            self.fail(f'unhandled schema kind: {schema}')

        for name, op in self.contracts.operations.items():
            with self.subTest(operation=name):
                path = op.path
                for segment in ('project_id','cabal_id','bible_id','decision_id',
                                'feature_id','task_id','session_id','worker_id','shift_id','command_id','review_id',
                                'artifact_id','intent_id','release_id','effect_id',
                                'budget_id','message_id','case_id','notification_id',
                                'grant_id'):
                    path = path.replace('{'+segment+'}', 'p1' if segment=='project_id' else 'test')
                req = sample(op.request_schema) if op.request_schema is not None else None
                if req is not None:
                    req['request_id'] = name
                self.domain.result = sample(op.response_schema)
                if name == 'listEvents':
                    self.domain.result = None
                status, data, _ = self.call(op.method, path, token=self.owner_token, body=req)
                self.assertEqual(status, 200, (name, data))
                if name != 'listEvents':
                    self.assertEqual(self.domain.calls[-1][0], name)
                else:
                    self.assertEqual(data['events'], [])
        self.assertEqual(len(self.tx.called), sum(int(op.mutation) for op in self.contracts.operations.values()))

    def test_real_loopback_http_server(self):
        server = create_http_server(self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f'http://127.0.0.1:{server.server_port}/v1/projects/p1',
                headers={'Authorization':'Bearer '+self.worker_token},
            )
            with urllib.request.urlopen(request,timeout=3) as response:
                data = json.load(response)
                self.assertEqual(response.status,200)
                self.assertEqual(data['project_id'],'p1')
                self.assertEqual(response.headers['Cache-Control'],'no-store')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_external_effects_are_not_claimed_on_missing_domain(self):
        self.registry._services.pop('I')
        req = {'request_id':'r','receipt_commit_sha':'a'*40,'receipt_digest':'b'*64}
        status,data,_ = self.call('POST','/v1/projects/p1/shifts/s1/end',token=self.owner_token,body=req)
        self.assertEqual((status,data['code']), (503,'capability_unavailable'))
        self.assertEqual(self.tx.called, [])

    def test_project_and_request_id_bound_to_replay(self):
        req = {'request_id':'req','worker_id':'w1','browser_chat_ref':'browser-session','role_id':'coder'}
        self.assertEqual(self.call('POST','/v1/projects/p1/workers',token=self.owner_token,body=req)[0],200)
        self.assertEqual(self.call('POST','/v1/projects/p1/workers',token=self.worker_token,body=req)[1]['code'],'conflict')
        self.assertEqual(len([x for x in self.domain.calls if x[0]=='registerWorker']),1)

    def test_bad_identity_and_bearer_header(self):
        self.assertEqual(self.call('GET','/v1/projects/p1')[0],401)
        self.assertEqual(self.call('GET','/v1/projects/p1',token='forged')[0],401)
        self.assertEqual(self.call('GET','/v1/projects/p1',token=self.worker_token)[0],200)

    def test_query_bounds_and_unknown_param(self):
        url='/v1/projects/p1/events'
        self.assertEqual(self.call('GET',url,token=self.owner_token,query='after=-1')[0],400)
        self.assertEqual(self.call('GET',url,token=self.owner_token,query='limit=501')[0],400)
        self.assertEqual(self.call('GET',url,token=self.owner_token,query='limit=10&limit=20')[0],400)
        self.assertEqual(self.call('GET',url,token=self.owner_token,query='is_owner=1')[0],400)

    def test_event_page_sequence_resume_scope_and_empty_cursor(self):
        self.reader.events=[{'project_id':'p1','event_seq':i,'event_type':'changed','subject_id':None,'payload_digest':'digest'}
                            for i in (1,3,5,7,9)]
        url='/v1/projects/p1/events'
        code, first,_=self.call('GET',url,token=self.worker_token,query='limit=2')
        self.assertEqual(code,200)
        self.assertEqual(first['next_cursor'],3)
        self.assertEqual([e['event_seq'] for e in first['events']],[1,3])
        code,second,_=self.call('GET',url,token=self.worker_token,query='after=3&limit=2')
        self.assertEqual([e['event_seq'] for e in second['events']],[5,7])
        self.assertEqual(self.call('GET',url,token=self.worker_token,query='after=9')[1]['next_cursor'],9)
        self.assertEqual(self.call('GET','/v1/projects/p2/events',token=self.worker_token)[0],403)
        self.assertEqual(self.reader.calls[0],('p1',0,2))

    def test_events_reject_wrong_project_or_out_of_order_backend(self):
        self.reader.scan_project_events = lambda **kwargs: [{'project_id':'p2',
            'event_seq':1,'event_type':'x','subject_id':None,'payload_digest':'x'}]
        status,data,_=self.call('GET','/v1/projects/p1/events',token=self.worker_token)
        self.assertEqual((status,data['code']),(500,'internal'))

    def test_path_and_nonfinite_json_defenses(self):
        self.assertEqual(self.call('GET','/v1/projects/..',token=self.worker_token)[0],400)
        self.assertEqual(self.call('GET','/v1/projects/%2f',token=self.worker_token)[0],400)
        malformed = b'{"request_id":"r","worker_id":NaN,"browser_chat_ref":"x","role_id":"coder"}'
        self.assertEqual(self.call('POST','/v1/projects/p1/workers',token=self.worker_token,raw=malformed)[0],400)
        self.assertEqual(self.call('GET','/v1/projects/p1',token=self.worker_token,query='owner=true')[0],400)

    def test_transport_security_headers(self):
        status,data,headers=self.call('GET','/v1/projects/p1',token=self.worker_token)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')

    def test_principal_provision_uses_verified_id_and_subject_only(self):
        class Registrar:
            def __init__(self):
                self.rows={}
            def ensure_trusted_principal(self, *, principal_id, auth_issuer, auth_subject, kind):
                row = (auth_issuer, auth_subject, kind)
                if principal_id in self.rows and self.rows[principal_id] != row:
                    raise ApiError('conflict','principal identity collision')
                self.rows[principal_id] = row
        store=Registrar()
        provisioner=PrincipalProvisioner(self.service.verifier,store)
        proof=provisioner.provision_bearer(self.owner_token)
        self.assertTrue(proof.owner)
        self.assertEqual(store.rows['owner'],('configured-test','human-owner-subject','human'))
        self.assertEqual(provisioner.provision_bearer(self.owner_token),proof)
        with self.assertRaises(ApiError):
            provisioner.provision_bearer('fake owner')
        self.assertEqual(len(store.rows),1)

    def test_missing_atomic_store_returns_service_unavailable(self):
        self.service.transactions=None
        req={'request_id':'req','name':'x','charter_digest':'digest'}
        status,data,_=self.call('POST','/v1/projects',token=self.owner_token,body=req)
        self.assertEqual((status,data['code']),(503,'capability_unavailable'))

    def test_missing_owner_domain_not_success(self):
        self.registry._services.pop('B')
        self.assertEqual(self.call('GET','/v1/projects/p1/cabals',token=self.worker_token)[0],503)

    def test_response_schema_fail_closed(self):
        self.domain.result={'unexpected':True}
        status,data,_=self.call('GET','/v1/projects/p1',token=self.worker_token)
        self.assertEqual((status,data['code']),(500,'internal'))


if __name__ == '__main__':
    unittest.main()
