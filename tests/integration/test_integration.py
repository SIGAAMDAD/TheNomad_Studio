"""Independent greenfield one-wave composition and interface regression tests.

The configured skill contents and slot approval are local TEST fixtures only.
Provider credentials, ChatGPT chats, GitHub/Drive effects and release attestation
are not supplied or simulated as passing external gates.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import time
import unittest

from nomad_studio.app import ComponentRef, DOMAIN_ORDER, EXTENSION_ORDER, open_studio
from nomad_studio.contracts.v1 import CommandEnvelope, QueryEnvelope, VerifiedIdentity
from nomad_studio.integration_wire import gateway_facades
from nomad_studio.registry.catalog import load_catalog

SHA = 'a' * 64
ROLE = 'rpg-build-engineer'
WORKER = 'wg-demo'
JOB = 'job12'
OWNER = 'owner/local'
OWNER_EPOCH = 4
WORKER_EPOCH = 3
CONN = 'conn1'
SKILL_BYTES = b'# Controlled integration fixture: rpg-build-engineer\nlocal-only mock skill\n'
WORKER_OPS = ['shift.start', 'shift.next', 'shift.record', 'shift.checkpoint',
              'shift.finish', 'shift.yield', 'relay.poll', 'scheduler.claim']


def command(domain: str, operation: str, payload=None, *, key: str='idem1',
            worker: str | None=None, shift: str | None=None, job: str | None=None,
            epoch: int | None=None) -> CommandEnvelope:
    return CommandEnvelope('NOMAD/CMD/1', 'request-'+key, domain, operation,
                           key, worker_id=worker, shift_id=shift, job_id=job,
                           assignment_epoch=epoch, payload=payload or {})


def query(domain: str, operation: str, payload=None, *,worker=None,shift=None,job=None) -> QueryEnvelope:
    return QueryEnvelope('NOMAD/QUERY/1', 'qry1', domain, operation,
                         worker_id=worker, shift_id=shift, job_id=job, payload=payload or {})


class IntegratedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nomad-onewave-')
        p = Path(self.tmp.name)
        root = p / 'sealed'
        root.mkdir(mode=0o700)
        role = load_catalog().roles[ROLE]
        self.skill_pin = {ROLE: {'uri':role.skill_uri,'version':role.skill_version,
                                 'sha256':hashlib.sha256(SKILL_BYTES).hexdigest()}}
        self.skill_uri = role.skill_uri
        self.skill_version = role.skill_version
        self.clock = lambda: time.time_ns()//1000
        self.slots = {WORKER: {'role_id': ROLE, 'jobs': [JOB], 'epoch':WORKER_EPOCH,
                                'approved_paths': ['src/nomad/**'],
                                'scope_digest': SHA,'source_ref':'repo@'+SHA,
                                'scopes':WORKER_OPS,
                                'connector_credential_ids':[CONN]}}
        self.runtime = open_studio(sqlite_path=p/'studio.sqlite3',
            owner_principal_id=OWNER, trusted_issuer_ids=('issuer/trusted',),
            ports={'archive_root': str(root),
                   'slot_authority': lambda worker: self.slots.get(worker),
                   'slot_authorizer': lambda identity, worker, role_id, scope:
                      identity.principal_id == OWNER and role_id == ROLE and
                      worker == WORKER and scope.get('scope_sha256') == SHA and
                      scope.get('assignment_epoch') == WORKER_EPOCH,
                   'skill_pins':self.skill_pin,
                   'skill_loader':lambda uri: {'uri':uri,'version':self.skill_version,'content':SKILL_BYTES},
                   'clock_us':self.clock,
                   })
        self.runtime.store.set_actor_epoch(OWNER, OWNER_EPOCH,
                                           issuer_principal_id='issuer/trusted')
        self.owner = VerifiedIdentity(OWNER,'owner-cert','issuer/trusted','mtls',
            frozenset({'owner','owner:console:read','owner:console:launch',
                       'owner:console:control','trust:issue_launch',
                       'registry:prompt','registry:read','state:read'}),
            self.clock() + 300_000_000, OWNER_EPOCH)
        self.connector = VerifiedIdentity('connector/test',CONN,'issuer/trusted','mtls',
            frozenset({'trust:activate'}),self.clock()+300_000_000,OWNER_EPOCH)

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def test_v1_initial_schema_and_all_19_base_components(self):
        self.assertEqual(tuple(self.runtime.components), DOMAIN_ORDER)
        schema = self.runtime.store.schema_identity()
        self.assertEqual(schema['version'], 1)
        self.assertEqual(len(schema['sha256']), 64)
        self.assertIsNone(self.runtime.store.read(lambda r:r.fetch_one(
            "SELECT name FROM sqlite_master WHERE name='state_migrations'")))
        self.assertEqual(self.runtime.store.diagnostics()['journal_mode'], 'wal')
        self.assertNotIn('authorize', gateway_facades(self.runtime.components))
        self.assertEqual(self.runtime.component('state').name,'state')

    def test_all_ten_studio_v1_extensions_are_internally_composed_not_publicly_exposed(self):
        self.assertEqual(tuple(self.runtime.extensions), EXTENSION_ORDER)
        self.assertTrue(all(self.runtime.extension(name) is not None for name in EXTENSION_ORDER))
        # Separate private Oversight modules must never be composition-root imports.
        self.assertFalse(any(name.startswith('oversight_') for name in self.runtime.extensions))
        public = gateway_facades(self.runtime.components)
        self.assertEqual(set(public), set(DOMAIN_ORDER))
        self.assertFalse(set(EXTENSION_ORDER) & set(public))
        # An owner prompt cannot enable the sensitive creative gate by itself.
        creative = self.runtime.extension('creative_enforcement')
        blocked = creative.query(query('creative', 'creative.inspect'), self.owner)
        self.assertNotEqual(blocked.state, 'ACCEPTED')

    def test_extension_port_registry_denies_untrusted_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                open_studio(sqlite_path=Path(tmp) / 'bad-v1.sqlite3',
                    owner_principal_id=OWNER, trusted_issuer_ids=('issuer/trusted',),
                    ports={'extension_ports': {'persona': {'store': object()}}})

    def test_wire_normalizes_only_closed_operations(self):
        ports = gateway_facades(self.runtime.components)
        result = ports['state'].query(query('state', 'state.health'),self.owner)
        self.assertEqual((result.state,result.code),('ACCEPTED','OK'))
        self.assertEqual(ports['state'].query(query('state','state.notreal'),self.owner).code,'NOT_SUPPORTED')
        self.assertEqual(ports['state'].query(query('other','other.health'),self.owner).code,'MALFORMED_REQUEST')
        # Public trust has no internal authorization oracle.
        request = command('trust','trust.authorize',{'worker_id':WORKER})
        self.assertEqual(ports['trust'].execute(request,self.owner).code,'NOT_SUPPORTED')
        self.assertEqual(ports['scheduler'].query(query('scheduler','scheduler.ready'),self.owner).state, 'DENIED')
        # Internal Studio V1 ports must use the closed, authorized wire vocabulary.
        sibling = ComponentRef('state')
        sibling.bind(self.runtime.component('state'))
        self.assertEqual(sibling.query(query('state','state.health'),self.owner).state,'ACCEPTED')

    def test_verification_release_stays_blocked_without_external_attestation(self):
        ident = VerifiedIdentity('qa/operator','qa-cert','issuer/trusted','mtls',
              frozenset({'verification:read'}),self.clock()+100_000_000,OWNER_EPOCH)
        result = self.runtime.component('verification').query(
            query('verification','acceptance_report'),ident)
        self.assertFalse(result.payload.get('accepted',False))
        self.assertIn(result.state,('BLOCKED','NOT_RUN','UNKNOWN'))

    def test_real_launch_activate_shift_and_ticket_replay(self):
        console = self.runtime.component('console')
        preview = console.query(query('console','console.launch_prompt',
                            {'worker_id':WORKER,'mode':'START'}),self.owner)
        self.assertEqual((preview.state,preview.code), ('ACCEPTED','PREVIEW_ONLY'))
        self.assertFalse(preview.payload['copyable'])
        self.assertNotIn('nl1.',preview.payload['prompt_template'])
        issued = console.execute(command('console','console.launch_prompt',
                       {'worker_id':WORKER,'mode':'START','assignment_epoch':WORKER_EPOCH},
                       key='owner-launch-1'),self.owner)
        self.assertEqual((issued.state,issued.code),('ACCEPTED','MANUAL_LAUNCH_READY'))
        self.assertEqual(issued.receipt_id is not None, True)
        self.assertIn('nl1.', issued.payload['prompt_text'])
        ticket = issued.payload['prompt_text'].split('ONE-TIME-LAUNCH-REFERENCE: ')[1].split('\n')[0]
        self.assertTrue(ticket.startswith('nl1.'))
        self.assertNotIn('{{ONE-TIME-LAUNCH-REFERENCE}}',issued.payload['prompt_text'])
        activation = {'ticket':ticket,'worker_id':WORKER,'role_id':ROLE,
                      'job_id':JOB,'epoch':WORKER_EPOCH}
        trust = self.runtime.component('trust')
        result = trust.execute(command('trust','trust.activate',activation,key='activate-1'),self.connector)
        self.assertEqual((result.state,result.code),('ACCEPTED','SESSION_ACTIVATED'))
        self.assertEqual(trust.execute(command('trust','trust.activate',activation,key='activate-2'),
                                       self.connector).code,'REVOKED_GRANT')
        auth = trust.authenticate_session(result.payload['session'],self.connector)
        self.assertEqual((auth.outcome.state, auth.outcome.code),('ACCEPTED','SESSION_VALID'))
        worker = auth.identity
        self.assertIsNotNone(worker)
        self.assertEqual(worker.scopes,frozenset(WORKER_OPS))
        shift = result.payload['shift_id']
        start = gateway_facades(self.runtime.components)['shift'].execute(
            command('shift','shift.start',{'response_id':shift},key='start-1',
                    worker=WORKER, shift=shift,job=JOB,epoch=WORKER_EPOCH),worker)
        self.assertEqual((start.state,start.code),('ACCEPTED','STARTED'))
        y = gateway_facades(self.runtime.components)['shift'].execute(
            command('shift','shift.yield',{'response_id':shift},key='yield-1',
                    worker=WORKER,shift=shift,job=JOB,epoch=WORKER_EPOCH),worker)
        self.assertEqual((y.state,y.code),('ACCEPTED','RESPONSE_CLOSED'))
        events = self.runtime.store.read(lambda r:r.fetch_all(
            "SELECT kind FROM state_events WHERE domain IN ('trust','shift') ORDER BY sequence"))
        kinds = [row['kind'] for row in events]
        self.assertIn('LAUNCH_ISSUED',kinds)
        self.assertIn('STARTED',kinds)
        self.assertIn('YIELD',kinds)

    def test_missing_external_proof_fails_closed(self):
        missing = self.runtime.component('review')
        self.assertIsNotNone(missing)
        identity = VerifiedIdentity('qa/local','qa-cert','issuer/trusted','mtls',
              frozenset({'review:verdict'}),self.clock()+100_000_000,OWNER_EPOCH)
        # Review must rely on independently verified source and QA receipts.
        response = missing.query(query('review','review.verdict'),identity)
        self.assertNotEqual(response.state,'ACCEPTED')
        self.assertFalse(hasattr(self.runtime,'automated_chat_launcher'))


if __name__ == '__main__':
    unittest.main()
