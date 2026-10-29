"""WG-F isolated real SQLite port and authenticated-provider fake/fault tests.

Test-only SQLite connection is a fake StateStore; production never constructs one.
"""
from __future__ import annotations
from _studio_v1_fixture import sql_for

from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
import unittest

from nomad_studio.contracts.v1 import CommandEnvelope, QueryEnvelope, Outcome, VerifiedIdentity
from nomad_studio.source.api import build_component
from nomad_studio.source.models import (
    RefObservation, CommitObservation, TreeObservation, TreeEntry, CIObservation, ProviderUnavailable,
)

A = 'a' * 40
B = 'b' * 40
C = 'c' * 40
D = 'd' * 40
E = 'e' * 40
POLICY = {'owned_paths': ['src/**', 'docs/**'], 'protected_paths': ['src/private/**'],
          'ci_workflows': ['build/test'], 'max_tree_entries': 1000}


class Reader:
    credential_scopes = frozenset({'repo:read', 'ci:read'})
    independent_readback = True

    def __init__(self):
        self.head = A
        self.tree_sha = B
        self.err = False
        self.calls = []
        self.tree = (TreeEntry('src', 'tree', C, '040000'),
                     TreeEntry('src/app.py', 'blob', D, '100644'),
                     TreeEntry('src/private', 'tree', C, '040000'),
                     TreeEntry('src/private/secret.py', 'blob', D, '100644'),
                     TreeEntry('src/link', 'symlink', E, '120000'),
                     TreeEntry('src/subrepo', 'submodule', E, '160000'),
                     TreeEntry('docs', 'tree', C, '040000'),
                     TreeEntry('docs/readme.md', 'blob', D, '100644'),
                     TreeEntry('.github', 'tree', C, '040000'),
                     TreeEntry('.github/workflows', 'tree', C, '040000'),
                     TreeEntry('.github/workflows/test.yml', 'blob', D, '100644'))
        self.complete = True
        self.ci_sha = A
        self.ci_status = 'completed'
        self.ci_conclusion = 'success'
        self.ci_workflow = 'build/test'
        self.tamper = None

    def _go(self, name):
        self.calls.append(name)
        if self.err:
            raise ProviderUnavailable('injected provider outage')

    def get_ref(self, repo, ref):
        self._go('ref')
        return RefObservation(repo if self.tamper != 'repo' else 'attacker/other', ref, self.head)

    def get_commit(self, repo, sha):
        self._go('commit')
        return CommitObservation(repo, sha, self.tree_sha)

    def get_tree(self, repo, tree_sha, *, max_entries):
        self._go('tree')
        assert max_entries == 1000
        return TreeObservation(repo, tree_sha, self.tree, self.complete)

    def get_ci(self, repo, run_id):
        self._go('ci')
        return CIObservation(repo, run_id, self.ci_sha, self.ci_workflow, self.ci_status, self.ci_conclusion)


class SqlReader:
    def __init__(self, db):
        self.db = db

    def fetch_one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=()):
        return [dict(x) for x in self.db.execute(sql, params).fetchall()]


class SqlTx(SqlReader):
    def execute(self, sql, params=()):
        return self.db.execute(sql, params).rowcount

    def append_event(self, event):
        self.db.execute('INSERT INTO fake_events VALUES (?, ?, ?)',
                        (event.event_id, event.kind, event.payload_sha256))
        return event.event_id


class FakeStateStore:
    def __init__(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE fake_events(event_id TEXT PRIMARY KEY, kind TEXT, payload_sha TEXT)')
        self.db.executescript(sql_for('source'))

    def read(self, callback):
        return callback(SqlReader(self.db))

    def transact(self, callback, *, idempotency_key=None):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            output = callback(SqlTx(self.db))
            self.db.execute('COMMIT')
            return output
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()


class Scheduler:
    name = 'scheduler'

    def __init__(self):
        self.snapshot = {
            'principal_id': 'worker-verified', 'worker_id': 'worker-1', 'job_id': 'job-1',
            'assignment_epoch': 8, 'lease_generation': 12, 'authority_epoch': 9,
            'mapping_epoch': 1, 'policy_digest': None, 'resource_scope': ['src/**'],
            'expected_head': A, 'expires_at_us': 3_000_000_000_000_000,
        }
        self.state = 'ACCEPTED'
        self.received = []
        self.on_query = None

    def query(self, query, identity):
        self.received.append((query, identity))
        if self.on_query:
            self.on_query()
        return Outcome(state=self.state, code='CURRENT', payload=dict(self.snapshot))

    def execute(self, command, identity):
        raise AssertionError('source must not claim or mutate scheduler')


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStateStore()
        self.reader = Reader()
        self.scheduler = Scheduler()
        self.identity = VerifiedIdentity('worker-verified', 'worker-cred', 'gateway', 'mtls',
                                         frozenset({'source:read', 'source:verify'}),
                                         3_000_000_000_000_000, 9)
        self.owner = VerifiedIdentity('real-owner', 'owner-cred', 'gateway', 'mtls',
                                      frozenset({'source:read', 'source:admin', 'owner:approve'}),
                                      3_000_000_000_000_000, 9)
        self.source = build_component({'store': self.store, 'source_reader': self.reader,
                                       'scheduler': self.scheduler, 'clock_us': lambda: 2_000_000_000_000_000})
        self.configure()

    def tearDown(self):
        self.store.close()

    def command(self, op, payload=None, **kw):
        return CommandEnvelope(version='NOMAD/CMD/1', domain='source', operation=op,
                               request_id='request-1', idempotency_key=kw.pop('idempotency_key', 'k1'),
                               job_id='job-1', worker_id='worker-1', assignment_epoch=8,
                               source_sha=A, payload=payload or {}, **kw)

    def query(self, op, payload):
        return QueryEnvelope(version='NOMAD/QUERY/1', domain='source', operation=op,
                             request_id='q1', payload=payload)

    def configure(self):
        payload = {'action': 'upsert', 'target': 'Terrain3D', 'repository': 'verified/Terrain3D',
                   'ref': 'refs/heads/dev', 'policy': POLICY, 'expected_mapping_epoch': 0}
        self.assertEqual(self.source.execute(self.command('protect', payload, idempotency_key='setup'), self.owner).state, 'ACCEPTED')
        info = self.source.query(self.query('protect', {'target': 'Terrain3D'}), self.identity)
        self.assertEqual(info.state, 'ACCEPTED')
        self.scheduler.snapshot['policy_digest'] = info.payload['policy_digest']

    def claim(self, *, changes=None, **updates):
        data = {'target': 'Terrain3D', 'base_sha': A, 'lease_generation': 12,
                'policy_digest': self.scheduler.snapshot['policy_digest'],
                'resource_scope': ['src/**'], 'changes': [] if changes is None else changes}
        data.update(updates)
        return data

    def test_studio_v1_source_tables_are_in_initial_schema(self):
        self.assertIn('CREATE TABLE', sql_for('source'))
        self.assertIsNotNone(self.store.db.execute("SELECT name FROM sqlite_master WHERE name='source_targets'").fetchone())

    def test_resolve_exact_head_and_tree_without_blob_download(self):
        result = self.source.query(self.query('resolve', {'target': 'Terrain3D'}), self.identity)
        self.assertEqual(result.state, 'ACCEPTED')
        self.assertEqual(result.payload['head_sha'], A)
        self.assertEqual(result.payload['symlinks'], 1)
        self.assertEqual(result.payload['submodules'], 1)
        self.assertEqual(self.reader.calls[-3:], ['ref', 'commit', 'tree'])

    def test_faked_worker_source_sha_cannot_pass(self):
        result = self.source.execute(self.command('verify_base', self.claim(base_sha=C)), self.identity)
        self.assertEqual((result.state, result.code), ('CONFLICT', 'REBASE_REQUIRED'))
        self.assertFalse(self.store.read(lambda db: db.fetch_all('SELECT * FROM source_proofs')))

    def test_head_movement_after_scheduler_claim_fences(self):
        self.reader.head = C
        result = self.source.execute(self.command('verify_base', self.claim()), self.identity)
        self.assertEqual((result.state, result.code), ('CONFLICT', 'REBASE_REQUIRED'))

    def test_head_change_during_fence_is_caught_by_final_provider_check(self):
        self.scheduler.on_query = lambda: setattr(self.reader, 'head', C)
        r = self.source.execute(self.command('verify_base', self.claim()), self.identity)
        self.assertEqual((r.state, r.code), ('CONFLICT', 'REBASE_REQUIRED'))
        self.assertEqual(self.store.read(lambda db: db.fetch_one('SELECT COUNT(*) AS n FROM source_proofs'))['n'], 0)

    def test_owner_mapping_rejects_bad_git_ref(self):
        payload = {'action': 'upsert', 'target': 'NomadMain', 'repository': 'verified/NomadMain',
                   'ref': 'refs/heads/bad.lock', 'policy': POLICY, 'expected_mapping_epoch': 0}
        self.assertEqual(self.source.execute(self.command('protect', payload), self.owner).code, 'INVALID_SCHEMA')

    def test_provider_get_ci_failure_is_unknown(self):
        self.reader.err = True
        payload = {k:v for k,v in self.claim(run_id='ci-1').items() if k != 'changes'}
        self.assertEqual(self.source.execute(self.command('readback_ci', payload), self.identity).state, 'UNKNOWN')

    def test_faked_ci_success_for_different_head_denied(self):
        self.reader.ci_sha = C
        result = self.source.execute(self.command('readback_ci', {k:v for k,v in self.claim(run_id='r123').items() if k!='changes'}), self.identity)
        self.assertEqual(result.code, 'SOURCE_SHA_MISMATCH')
        self.assertEqual(result.state, 'DENIED')

    def test_verified_ci_proof_has_exact_sha_and_audit(self):
        payload = {k:v for k,v in self.claim(run_id='r123').items() if k!='changes'}
        r = self.source.execute(self.command('readback_ci', payload), self.identity)
        self.assertEqual(r.state, 'ACCEPTED')
        self.assertEqual(r.payload['subject_sha'], A)
        self.assertEqual(r.payload['fence']['lease_generation'], 12)
        self.assertEqual(self.store.read(lambda db: db.fetch_one('SELECT COUNT(*) AS n FROM source_proofs'))['n'], 1)

    def test_ci_pending_or_failed_never_verifies(self):
        payload = {k:v for k,v in self.claim(run_id='r123').items() if k!='changes'}
        self.reader.ci_status = 'queued'
        self.assertEqual(self.source.execute(self.command('readback_ci', payload), self.identity).state, 'BLOCKED')
        self.reader.ci_status, self.reader.ci_conclusion = 'completed', 'failure'
        self.assertEqual(self.source.execute(self.command('readback_ci', payload), self.identity).code, 'QA_PENDING')

    def test_independent_provider_outage_is_unknown(self):
        self.reader.err = True
        self.assertEqual((self.source.execute(self.command('verify_base', self.claim()), self.identity).state), 'UNKNOWN')
        self.assertEqual(self.source.query(self.query('resolve', {'target': 'Terrain3D'}), self.identity).code, 'PROVIDER_UNKNOWN')

    def test_no_publisher_credentials_used_to_read(self):
        self.reader.credential_scopes = frozenset({'repo:read', 'repo:write', 'ci:read'})
        self.assertEqual(self.source.query(self.query('resolve', {'target':'Terrain3D'}), self.identity).code, 'UNTRUSTED_CONNECTOR')
        self.reader.credential_scopes = frozenset({'repo:read', 'ci:read'})
        self.reader.independent_readback = False
        self.assertEqual(self.source.query(self.query('resolve', {'target':'Terrain3D'}), self.identity).code, 'UNTRUSTED_CONNECTOR')

    def test_invalid_provider_tree_entry_kind_is_unknown(self):
        self.reader.tree += (TreeEntry('src/strange', 'not-a-git-object', E, '100644'),)
        self.assertEqual(self.source.query(self.query('resolve', {'target':'Terrain3D'}), self.identity).code, 'PROVIDER_UNKNOWN')

    def test_invalid_provider_repo_is_unknown(self):
        self.reader.tamper = 'repo'
        self.assertEqual(self.source.query(self.query('resolve', {'target':'Terrain3D'}), self.identity).state, 'UNKNOWN')

    def test_tree_incomplete_or_oversized_unknown(self):
        self.reader.complete = False
        self.assertEqual(self.source.query(self.query('resolve', {'target':'Terrain3D'}), self.identity).code, 'PROVIDER_UNKNOWN')

    def test_lease_generation_fences(self):
        self.scheduler.snapshot['lease_generation'] += 1
        self.assertEqual(self.source.execute(self.command('verify_base', self.claim()), self.identity).code, 'STALE_LEASE')

    def test_assignment_epoch_fences(self):
        self.scheduler.snapshot['assignment_epoch'] += 1
        self.assertEqual(self.source.execute(self.command('verify_base', self.claim()), self.identity).code, 'STALE_ASSIGNMENT')

    def test_authority_epoch_fences(self):
        self.scheduler.snapshot['authority_epoch'] += 1
        self.assertEqual(self.source.execute(self.command('verify_base', self.claim()), self.identity).code, 'STALE_ASSIGNMENT')

    def test_mapping_epoch_fences(self):
        self.scheduler.snapshot['mapping_epoch'] += 1
        self.assertEqual(self.source.execute(self.command('verify_base', self.claim()), self.identity).code, 'REBASE_REQUIRED')

    def test_expired_scheduler_lease_denied(self):
        self.scheduler.snapshot['expires_at_us'] = 1
        self.assertEqual(self.source.execute(self.command('verify_base', self.claim()), self.identity).code, 'STALE_LEASE')

    def test_sibling_path_prefix_collision_denied(self):
        path = [{'action': 'add', 'path': 'src2/trick.py'}]
        self.assertEqual(self.source.execute(self.command('check_paths', self.claim(changes=path)), self.identity).code, 'INVALID_SCOPE')

    def test_rename_into_protected_directory_denied(self):
        changes = [{'action': 'rename', 'source': 'src/app.py', 'destination': 'src/private/pwn.py'}]
        self.assertEqual(self.source.execute(self.command('check_paths', self.claim(changes=changes)), self.identity).code, 'PROTECTED_PATH')

    def test_delete_outside_scope_denied(self):
        self.assertEqual(self.source.execute(self.command('check_paths', self.claim(changes=[{'action':'delete','source':'docs/readme.md'}])),self.identity).code, 'INVALID_SCOPE')

    def test_symlink_submodule_and_ancestor_denied(self):
        for path in ('src/link', 'src/link/exploit.py', 'src/subrepo/sub.py'):
            with self.subTest(path=path):
                self.assertNotEqual(self.source.execute(self.command('check_paths', self.claim(changes=[{'action':'modify','path':path}])),self.identity).state,'ACCEPTED')

    def test_ascii_unicode_homoglyph_case_and_traversal_fail_closed(self):
        for path in ('src/аpp.py', 'SRC/APP.PY', 'src/../private/a.py', 'src\\evil.py',
                     '/src/app.py', 'src/a//b.py', 'src/CON.txt', 'src/evil.py '):
            with self.subTest(path=path):
                r = self.source.execute(self.command('check_paths', self.claim(changes=[{'action':'add','path':path}])),self.identity)
                self.assertNotEqual(r.state, 'ACCEPTED')

    def test_tree_unicode_confusable_target_denied(self):
        self.reader.tree += (TreeEntry('src/аpp2.py', 'blob', D, '100644'),)
        changes = [{'action':'add','path':'src/app2.py'}]
        self.assertEqual(self.source.execute(self.command('check_paths', self.claim(changes=changes)),self.identity).code, 'INVALID_PATH')

    def test_valid_changed_path_proof_uses_actual_git_object(self):
        r = self.source.execute(self.command('verify_base', self.claim(changes=[{'action':'modify','path':'src/app.py'}])),self.identity)
        self.assertEqual(r.state,'ACCEPTED')
        self.assertEqual(r.payload['observed_path_object_shas']['src/app.py'],D)
        self.assertTrue(r.receipt_id)

    def test_idempotent_proof_is_one_event_and_conflicting_replay_denied(self):
        c=self.command('verify_base', self.claim(changes=[{'action':'modify','path':'src/app.py'}]))
        r=self.source.execute(c,self.identity)
        again=self.source.execute(c,self.identity)
        self.assertEqual(r.receipt_id,again.receipt_id)
        self.assertEqual(self.store.read(lambda db:db.fetch_one('SELECT COUNT(*) AS n FROM source_proofs'))['n'],1)
        mutated=self.command('verify_base', self.claim(changes=[{'action':'add','path':'src/new.py'}]))
        self.assertEqual(self.source.execute(mutated,self.identity).code,'IDEMPOTENCY_CONFLICT')

    def test_closed_request_and_unknown_operation_denied(self):
        self.assertEqual(self.source.execute(self.command('check_paths', self.claim(secret='bad')),self.identity).code,'INVALID_SCHEMA')
        self.assertEqual(self.source.execute(self.command('pretend', {}),self.identity).code,'MALFORMED_REQUEST')

    def test_non_owner_cannot_change_mapping(self):
        payload={'action':'disable','target':'Terrain3D','expected_mapping_epoch':1}
        self.assertEqual(self.source.execute(self.command('protect',payload),self.identity).code,'INVALID_SCOPE')

    def test_target_mapping_requires_current_owner_epoch(self):
        payload={'action':'disable','target':'Terrain3D','expected_mapping_epoch':0}
        self.assertEqual(self.source.execute(self.command('protect',payload),self.owner).code,'STALE_ASSIGNMENT')

    def test_disable_target_fences_uses(self):
        payload={'action':'disable','target':'Terrain3D','expected_mapping_epoch':1}
        self.assertEqual(self.source.execute(self.command('protect',payload),self.owner).state,'ACCEPTED')
        self.assertEqual(self.source.query(self.query('resolve', {'target':'Terrain3D'}),self.identity).code,'NOT_CONFIGURED')

    def test_protect_is_idempotent(self):
        payload={'action':'disable','target':'Terrain3D','expected_mapping_epoch':1}
        c=self.command('protect',payload)
        a=self.source.execute(c,self.owner)
        b=self.source.execute(c,self.owner)
        self.assertEqual(a.receipt_id,b.receipt_id)
        self.assertEqual(a.state,'ACCEPTED')
        self.assertEqual(self.source.query(self.query('protect',{'target':'Terrain3D'}),self.identity).payload['mapping_epoch'],2)

    def test_prompt_claim_cannot_modify_policy_even_with_claimed_owner_scopes(self):
        fake_owner = replace(self.owner, authenticated_via='prompt')
        payload = {'action': 'disable', 'target': 'Terrain3D', 'expected_mapping_epoch': 1}
        self.assertEqual(self.source.execute(self.command('protect', payload), fake_owner).code, 'IDENTITY_UNVERIFIED')

    def test_unhashable_target_returns_denial_not_server_error(self):
        self.assertEqual(self.source.query(self.query('resolve', {'target': []}), self.identity).code, 'INVALID_SCOPE')

    def test_check_paths_has_no_persisted_proof_receipt(self):
        r = self.source.execute(self.command('check_paths', self.claim(changes=[{'action': 'modify', 'path': 'src/app.py'}])), self.identity)
        self.assertEqual(r.state, 'ACCEPTED')
        self.assertIsNone(r.receipt_id)
        self.assertEqual(self.store.read(lambda db: db.fetch_one('SELECT COUNT(*) AS n FROM source_proofs'))['n'], 0)

    def test_any_valid_project_target_is_unconfigured_until_owner_registers(self):
        for key in ('NomadFramework', 'unrelated-service', 'documentation.team', 'project:alpha'):
            self.assertEqual(self.source.query(self.query('resolve', {'target':key}), self.identity).code,
                             'NOT_CONFIGURED')

    def test_authorized_owner_can_register_and_resolve_unrelated_project(self):
        payload = {'action': 'upsert', 'target': 'unrelated-service',
                   'repository': 'verified/unrelated-service', 'ref': 'refs/heads/dev',
                   'policy': POLICY, 'expected_mapping_epoch': 0}
        result = self.source.execute(self.command('protect', payload, idempotency_key='project-neutral'), self.owner)
        self.assertEqual((result.state,result.code),('ACCEPTED','SOURCE_POLICY_UPDATED'))
        lookup = self.source.query(self.query('resolve', {'target':'unrelated-service'}), self.identity)
        self.assertEqual(lookup.state, 'ACCEPTED')
        self.assertEqual(lookup.payload['repository'], 'verified/unrelated-service')

    def test_invalid_project_targets_are_rejected(self):
        for bad in ('.', '../bad', '/absolute', 'bad\\path', '', 'x'*129, []):
            self.assertEqual(self.source.query(self.query('resolve', {'target':bad}), self.identity).code,
                             'INVALID_SCOPE')

    def test_missing_scheduler_never_verifies(self):
        source=build_component({'store':self.store,'source_reader':self.reader,'clock_us':lambda:2_000_000_000_000_000})
        self.assertEqual(source.execute(self.command('verify_base',self.claim()),self.identity).code,'NOT_CONFIGURED')

    def test_expired_identity_denied(self):
        identity=replace(self.identity,expires_at_us=1)
        self.assertEqual(self.source.query(self.query('resolve',{'target':'Terrain3D'}),identity).code,'EXPIRED_GRANT')


if __name__ == '__main__':
    unittest.main()
