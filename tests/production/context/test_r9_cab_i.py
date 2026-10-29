"""CAB-I source-owned R9 port, byte-fence, lease and simulated-provider tests.

The fixture schema is byte-for-byte copied from frozen R8 target DDL. Synthetic
providers are intentionally not production evidence.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from nomad_studio.production.context.core import (Decision, SourceFence, bytes_digest, digest)
from nomad_studio.production.context.service import ContextService
from nomad_studio.production.context.adapter import ProjectAdapterService
from nomad_studio.production.repository.service import RepositoryService
from nomad_studio.artifacts.runtime import ArtifactService
from nomad_studio.publication.runtime import EffectService

SHA = 'a'*64
CONTRACT = 'b'*64
SCOPE = 'c'*64
PARENT = '1'*40
NOW = 1000000000


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:', isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript((Path(__file__).parent / 'fixtures/initial_schema_v1.sql').read_text())

    def read(self, sql, args=()):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def write(self, action):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            result = action(self.conn)
            self.conn.execute('COMMIT')
            return result
        except Exception:
            self.conn.execute('ROLLBACK')
            raise


def insert(db, table, **fields):
    keys = list(fields)
    db.conn.execute('INSERT INTO ' + table + '(' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')',
                    tuple(fields.values()))


class Cas:
    def __init__(self):
        self.data = {}

    def put(self, data, sha):
        assert sha256(data).hexdigest() == sha
        ref = 'cas-' + sha
        self.data[ref] = data
        return ref

    def get(self, ref):
        return self.data[ref]


class Provider:
    def __init__(self):
        self.head_sha = PARENT
        self.calls = 0
        self.receipts = {}
        self.effects = {}
        self.timeout_once = False
        self.simulated = True

    def project_id(self, repo):
        return 'project' if repo == 'example/repo' else 'foreign'

    def is_protected(self, repo, branch):
        return branch in ('main', 'refs/heads/main', 'master')

    def head(self, repo, branch):
        return self.head_sha

    def commit_feature_branch(self, repo, branch, expected, patches, key):
        self.calls += 1
        if expected != self.head_sha:
            raise OSError('optimistic lock failed')
        new = sha256(json.dumps(patches, sort_keys=True).encode()).hexdigest()[:40]
        self.head_sha = new
        receipt = 'native-' + key
        self.receipts[receipt] = {'commit_sha': new, 'expected_parent_sha': expected, 'receipt_ref': receipt}
        return {'status': 'OK', 'commit_sha': new, 'receipt_ref': receipt}

    def readback(self, repo, branch, receipt):
        return self.receipts[receipt]

    def provider_for(self, target):
        return 'fixture-provider'

    def verify_source_fence(self, intent):
        return intent['expected_source_commit'] == self.head_sha

    def dispatch(self, intent, key):
        self.calls += 1
        if self.timeout_once:
            self.timeout_once = False
            self.effects[intent['effect_id']] = intent
            raise TimeoutError('simulated lost acknowledgement')
        self.effects[intent['effect_id']] = intent

    def observe(self, intent):
        return {'effect_id': intent['effect_id'], 'provider_id': 'fixture-provider',
                'idempotency_key': intent['idempotency_key'], 'payload_digest': intent['payload_digest'],
                'applied': intent['effect_id'] in self.effects, 'simulated': self.simulated,
                'native_receipt_ref': 'fixture-native-receipt'}

    def verify_native_observation(self, obs):
        return obs['simulated'] is False

    def verify_commit(self, repo, sha):
        return sha in {PARENT, self.head_sha}

    def project_snapshot(self, project, feature):
        return {'repo': 'example/repo', 'branch': 'feature/foo', 'source_sha': self.head_sha}


class Bibles:
    def active_pair(self, project):
        return {'project_id': project, 'pair_digest': SHA,
                'design_edition_id': 'design', 'technical_edition_id': 'technical',
                'design_manifest_digest': 'd'*64, 'technical_manifest_digest': 'e'*64,
                'design_epoch': 1, 'technical_epoch': 1}

    def excerpt(self, edition, clause):
        return {'kind': 'REQUIREMENT', 'scope': 'feature', 'text': f'canonical {edition} {clause}'}

    def verify_edition(self, row):
        return row['manifest_digest'] == 'e'*64


class Scope:
    def allows(self, run, fence, path):
        return path.startswith('game/features/') and fence.resource_scope_digest == SCOPE

    def permits(self, run, intent):
        return intent['scope_digest'] == SCOPE

    def verify_interface(self, run, fence, bundle):
        return bundle['scope']['feature_contract_digest'] == CONTRACT


class Reviewer:
    def verify(self, feature, patch_sha, review_ref):
        return review_ref == 'trusted-review' and feature == 'feature'


class Quality:
    def accepted_outcome(self, feature, receipt):
        return {'project_id': 'project', 'feature_id': feature,
                'bible_pair_digest': SHA, 'feature_contract_digest': CONTRACT,
                'source_commit': PARENT, 'canon_digest': '9'*64,
                'acceptance_ref': receipt}


class Identity:
    def verify_task_author(self, lease, actor, ref):
        return actor == 'worker' and lease['worker_id'] == actor and ref == 'trusted-author'


class Owner:
    def verify(self, proof, intent):
        return False


class AdapterRegistry:
    def resolve(self, project, edition):
        return {'adapter_id': 'python-fixture', 'adapter_digest': 'f'*64,
                'toolchain_digest': '0'*64, 'source_repo': 'example/repo'}


class Runner:
    def __init__(self, tmp_path):
        self.path = tmp_path / 'feature.py'
        self.path.write_text('def answer():\n    return 42\n')
        self.calls = 0

    def authenticate(self, runner_ref, proof):
        return runner_ref == 'fixture-runner' and proof == 'fixture-proof'

    def execute(self, params):
        self.calls += 1
        script = ("import py_compile, importlib.util; py_compile.compile(%r,doraise=True); "
                  "spec=importlib.util.spec_from_file_location('feature',%r); "
                  "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
                  "assert m.answer()==42" % (str(self.path), str(self.path)))
        p = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=10)
        return {'exit_code': p.returncode, 'log_bytes': p.stdout + p.stderr,
                'runner_ref': params['runner_ref'], 'adapter_digest': params['adapter_digest'],
                'toolchain_digest': params['toolchain_digest'], 'input_digest': digest(params),
                'observed_at_us': NOW, 'simulated': True}

    def verify_native(self, observed, proof):
        return False


@pytest.fixture
def env(tmp_path):
    db, cas, provider = Database(), Cas(), Provider()
    insert(db, 'v1_projects', project_id='project', owner_id='owner', constitution_digest='0'*64,
           state='ACTIVE', authority_epoch=1)
    insert(db, 'v1_briefs', project_id='project', brief_id='brief', feature_id='feature', owner_principal_id='owner',
           original_text='fixture', original_digest='8'*64, owner_command_ref='owner-cmd', submitted_at_us=NOW)
    insert(db, 'v1_feature_contracts', project_id='project', feature_id='feature', revision=1, brief_id='brief',
           original_brief_digest='8'*64, bible_pair_digest=SHA, contract_digest=CONTRACT,
           scope_digest=SCOPE, deliverables_digest='7'*64, acceptance_digest='6'*64,
           budget_limit_units=1000, payload_json='{}')
    insert(db, 'v1_feature_runs', project_id='project', feature_id='feature', brief_id='brief',
           active_contract_digest=CONTRACT, bible_pair_digest=SHA, phase='IMPLEMENTING',
           revision=1, graph_revision=1, updated_at_us=NOW)
    insert(db, 'v1_graph_revisions', feature_id='feature', graph_revision=1, graph_digest='4'*64,
           contract_digest=CONTRACT, bible_pair_digest=SHA, record_json='{}')
    insert(db, 'v1_tasks', feature_id='feature', task_id='task', graph_revision=1, cabal_id='cabal',
           task_digest='3'*64, state='LEASED', budget_limit_units=100, payload_json='{}')
    insert(db, 'v1_worker_leases', lease_id='work-lease', task_id='task', worker_id='worker',
           session_id='session', epoch=1, expires_at_us=NOW+100000, fenced=0)
    for kind, name, manifest in [('DESIGN','design','d'*64), ('TECHNICAL','technical','e'*64)]:
        insert(db, 'v1_bible_editions', project_id='project', kind=kind, edition_id=name, epoch=1,
               manifest_digest=manifest, source_repo='example/repo', source_commit=PARENT,
               owner_activation_ref='auth', active=1, payload_json='{}')
        insert(db, 'v1_bible_clauses', edition_id=name, clause_id='c1', kind='REQUIREMENT', scope='feature',
               content_digest=bytes_digest(f'canonical {name} c1'.encode()), priority=0)
    now = lambda: NOW
    return {
        'db': db, 'cas': cas, 'provider': provider, 'runner': Runner(tmp_path),
        'context': ContextService(db, cas, Bibles(), provider, Quality(), now),
        'repo': RepositoryService(db, cas, provider, Scope(), Reviewer(), now),
        'artifacts': ArtifactService(db, cas, Identity(), now),
        'effects': EffectService(db, cas, provider, Scope(), Owner(), now),
        'adapter': ProjectAdapterService(db, cas, Bibles(), provider, AdapterRegistry(), Runner(tmp_path), now),
    }


def fence(branch='feature/foo'):
    return SourceFence('project', 'example/repo', branch, PARENT, SCOPE, 'feature', SHA)


def put(cas, data):
    sha = bytes_digest(data)
    return sha, cas.put(data, sha)


def test_context_pins_two_bibles_and_canonical_excerpt(env):
    svc = env['context']
    b = svc.build_bundle('project', 'brief', SHA, fence())
    assert b.status == 'OK'
    assert b.record['source_sha'] == PARENT
    assert b.record['bible_pins']['technical']['manifest_digest'] == 'e'*64
    excerpt = svc.get_verified_excerpt(b.record['bundle_digest'], 'design/c1')
    assert excerpt.status == 'OK'
    assert excerpt.record['text'] == 'canonical design c1'
    assert svc.build_bundle('other', 'brief', SHA, fence()).status == 'NOT_FOUND'
    env['cas'].data[b.evidence_refs[0]] = b'tampered'
    assert svc.get_verified_excerpt(b.record['bundle_digest'], 'design/c1').code == 'EVIDENCE_INVALID'


def test_canon_projected_from_trusted_acceptance_not_bible_mutation(env):
    res = env['context'].apply_accepted_feature_outcomes('feature', {'receipt_id':'qa-receipt'})
    assert res.status == 'OK'
    assert env['context'].apply_accepted_feature_outcomes('feature', {'receipt_id':'qa-receipt'}).receipt_ref == res.receipt_ref
    assert len(env['db'].read('SELECT * FROM v1_canon_outcomes')) == 1
    assert len(env['db'].read('SELECT * FROM v1_bible_editions')) == 2


def test_artifacts_verify_exact_bytes_worker_epoch_and_cross_scope(env):
    sha, ref = put(env['cas'], b'{"quest":"real bytes"}')
    manifest = {'artifact_id':'artifact', 'project_id':'project','feature_id':'feature',
                'task_id':'task','kind':'QUEST_GRAPH','resource_path':'game/quests/intro.json',
                'content_sha256':sha, 'producer_principal':'worker',
                'bible_pair_digest':SHA, 'feature_contract_digest':CONTRACT, 'byte_proof_ref':ref}
    proofs = {'project_id':'project','feature_id':'feature','bible_pair_digest':SHA,
              'feature_contract_digest':CONTRACT,'graph_revision':1,
              'producer_principal':'worker','proof_ref':'trusted-author'}
    a = env['artifacts']
    assert a.submit_artifacts('task',1,[manifest],proofs).status == 'OK'
    assert a.verify_artifact_bytes('artifact',sha).status == 'OK'
    assert a.get_feature_manifest('feature',CONTRACT).record['artifacts'] == [manifest]
    assert a.submit_artifacts('task',1,[dict(manifest, project_id='foreign')],proofs).code == 'WRONG_PROJECT'
    assert a.submit_artifacts('task',2,[manifest],proofs).code == 'LEASE_FENCED'
    env['cas'].data[ref] = b'tamper'
    assert a.verify_artifact_bytes('artifact',sha).code == 'EVIDENCE_INVALID'


def test_prefix_leases_casefold_idempotency_and_protected_branch(env):
    repo, cas = env['repo'], env['cas']
    sha, ref = put(cas, b'print(42)')
    patch = {'resource_path':'game/features/Alpha/code.py', 'content_sha256':sha,'byte_proof_ref':ref}
    first = repo.prepare_feature_patch('feature',[patch],fence())
    assert first.status == 'OK'
    assert repo.prepare_feature_patch('feature',[patch],fence()).receipt_ref == first.receipt_ref
    other_sha, other_ref = put(cas,b'print(99)')
    assert repo.prepare_feature_patch('feature',[dict(patch, content_sha256=other_sha, byte_proof_ref=other_ref)],fence()).code == 'IDEMPOTENCY_CONFLICT'
    child = {'resource_path':'game/features/alpha/code.py/nested', 'content_sha256':sha,'byte_proof_ref':ref}
    assert repo.prepare_feature_patch('feature',[child],fence()).code == 'LEASE_CONFLICT'
    assert repo.prepare_feature_patch('feature',[dict(patch, resource_path='../danger.py')],fence()).code == 'SCOPE_EXCEEDED'
    assert repo.prepare_feature_patch('feature',[patch],fence('main')).code == 'SCOPE_EXCEEDED'
    integration = repo.integrate_feature('feature',1,{'archive_id':first.record['archive_id'],
                                 'patch_digest':first.record['patch_digest'], 'review_ref':'trusted-review'},fence())
    assert integration.status == 'OK', integration
    assert repo.readback_commit(integration.receipt_ref,'example/repo','feature/foo').status == 'OK'
    assert repo.integrate_feature('feature',1,{'archive_id':first.record['archive_id'],
                                 'patch_digest':first.record['patch_digest'], 'review_ref':'trusted-review'},fence()).receipt_ref == integration.receipt_ref
    assert env['provider'].calls == 1
    assert repo.integrate_feature('feature',1,{'archive_id':first.record['archive_id'],
                                 'patch_digest':first.record['patch_digest'], 'review_ref':'untrusted'},fence()).code == 'EVIDENCE_INVALID'


def effect(intent_id='effect', key='key'):
    return {'effect_id':intent_id, 'project_id':'project','feature_id':'feature',
            'action':'FEATURE_BRANCH_WRITE','target_ref':'repo:example/repo',
            'bible_pair_digest':SHA,'feature_contract_digest':CONTRACT,
            'scope_digest':SCOPE,'payload_digest':'2'*64,'idempotency_key':key}


def allow():
    return Decision(True,'ALLOW','OK','scope')


def test_effects_changed_intent_key_collision_and_unknown_provider_readback(env):
    svc = env['effects']
    assert svc.prepare_effect(effect(),allow(),allow()).status == 'ACCEPTED'
    assert svc.prepare_effect(effect(),allow(),allow()).status == 'ACCEPTED'
    assert svc.prepare_effect(effect('different'),allow(),allow()).code == 'IDEMPOTENCY_CONFLICT'
    assert svc.prepare_effect(dict(effect(),payload_digest='1'*64),allow(),allow()).code == 'IDEMPOTENCY_CONFLICT'
    env['provider'].timeout_once = True
    res = svc.dispatch_once('effect','key')
    assert res.status == 'UNKNOWN_EXTERNAL'
    assert env['provider'].calls == 1
    assert svc.dispatch_once('effect','key').status == 'UNKNOWN_EXTERNAL'
    assert env['provider'].calls == 1
    observed = env['provider'].observe(effect())
    # Fixture observation is explicitly simulated and cannot become production proof.
    assert svc.reconcile_readback('effect',observed).status == 'UNKNOWN_EXTERNAL'
    assert env['db'].read('SELECT state FROM v1_effect_intents')[0]['state'] == 'UNKNOWN_EXTERNAL'
    assert svc.prepare_effect(dict(effect('protected','second'),action='PROTECTED_MAINLINE_MERGE',owner_proof_ref='fake'),allow(),allow()).code == 'OWNER_ACTION_REQUIRED'


def test_adapter_executes_actual_python_import_and_compile_simulated_not_native(env):
    svc = env['adapter']
    resolved = svc.resolve_adapter('project','technical',PARENT)
    assert resolved.status == 'OK', resolved
    auth = {'runner_ref':'fixture-runner','runner_proof_ref':'fixture-proof'}
    result = svc.run_import('project',PARENT,resolved.record['adapter_ref'],auth)
    assert result.status == 'NOT_VERIFIED' and result.record['exit_code'] == 0
    assert result.record['log_digest'] == bytes_digest(b'')
    assert svc.run_build('project',PARENT,resolved.record['adapter_ref'],auth).status == 'NOT_VERIFIED'
    assert svc.run_feature_smoke('feature',PARENT,['smoke'],resolved.record['adapter_ref'],auth).status == 'NOT_VERIFIED'
    assert svc.verify_assets('feature',PARENT,'manifest',resolved.record['adapter_ref'],auth).status == 'NOT_FOUND'
    assert svc.run_build('other',PARENT,resolved.record['adapter_ref'],auth).code == 'WRONG_PROJECT'


def test_source_lease_expiration_reclaimed_and_stale_parent_fenced(env):
    svc, cas = env['repo'], env['cas']
    sha, ref = put(cas, b'file')
    path = {'resource_path':'game/features/A/file.txt', 'content_sha256':sha,'byte_proof_ref':ref}
    first = svc.prepare_feature_patch('feature',[path],fence())
    assert first.status == 'OK'
    # Expiry is retired in the very same writer transaction as a new nested reservation.
    env['repo'].clock_us = lambda: NOW + 600_000_001
    child = dict(path, resource_path='game/features/A/file.txt/subfile')
    second = svc.prepare_feature_patch('feature',[child],fence())
    assert second.status == 'OK'
    assert env['db'].read('SELECT count(*) AS n FROM v1_source_leases WHERE active=1')[0]['n'] == 1
    env['provider'].head_sha = '2'*40
    assert svc.integrate_feature('feature',1,{'archive_id':second.record['archive_id'],
                        'patch_digest':second.record['patch_digest'], 'review_ref':'trusted-review'},fence()).code == 'SOURCE_CONFLICT'
    assert env['provider'].calls == 0


def test_source_paths_reject_aliases_and_cross_tenant(env):
    svc, cas = env['repo'], env['cas']
    sha, ref = put(cas,b'a')
    for path in ['game/features/../evil', 'game//features/file', '/absolute/file',
                 'C:/drive/file', 'game/features/CON.txt', 'game/features/a\\b',
                 'game/features/na\u00efve.txt', 'game/features/file.']:
        p = {'resource_path':path,'content_sha256':sha,'byte_proof_ref':ref}
        assert svc.prepare_feature_patch('feature',[p],fence()).status in {'REJECTED','CONFLICT'}, path
    foreign = SourceFence('foreign', 'example/repo', 'feature/foo', PARENT, SCOPE, 'feature', SHA)
    assert svc.prepare_feature_patch('feature',[{'resource_path':'game/features/file',
                          'content_sha256':sha,'byte_proof_ref':ref}],foreign).code == 'WRONG_PROJECT'


def test_changed_intent_same_effect_and_fixture_native_confirmation_logic(env):
    svc = env['effects']
    item = effect('effect2','key2')
    assert svc.prepare_effect(item,allow(),allow()).status == 'ACCEPTED'
    assert svc.prepare_effect(dict(item,scope_digest='8'*64),allow(),allow()).status in ('CONFLICT','REJECTED')
    # Synthetic observation exercises readback transition, not production proof.
    env['provider'].simulated = False
    assert svc.dispatch_once('effect2','key2').status == 'OK'
    assert env['provider'].calls == 1
    assert svc.dispatch_once('effect2','key2').status == 'OK'
    assert env['provider'].calls == 1
    assert env['db'].read('SELECT state FROM v1_effect_intents WHERE effect_id=?',('effect2',))[0]['state'] == 'COMMITTED'
    assert svc.reconcile_readback('effect2', {'effect_id':'fake'}).code == 'EVIDENCE_INVALID'


def test_artifact_collision_and_manifest_source_proof_negative(env):
    a = env['artifacts']
    sha, ref = put(env['cas'], b'valid')
    manifest = {'artifact_id':'asset', 'project_id':'project','feature_id':'feature',
                'task_id':'task','kind':'ASSET','resource_path':'game/assets/rock.glb',
                'content_sha256':sha, 'producer_principal':'worker',
                'bible_pair_digest':SHA, 'feature_contract_digest':CONTRACT, 'byte_proof_ref':ref}
    proofs = {'project_id':'project','feature_id':'feature','bible_pair_digest':SHA,
              'feature_contract_digest':CONTRACT,'graph_revision':1,
              'producer_principal':'worker','proof_ref':'trusted-author'}
    assert a.submit_artifacts('task',1,[manifest],proofs).status == 'OK'
    other_sha, other_ref = put(env['cas'],b'changed')
    assert a.submit_artifacts('task',1,[dict(manifest, content_sha256=other_sha,
                                             byte_proof_ref=other_ref)],proofs).code == 'IDEMPOTENCY_CONFLICT'
    assert a.submit_artifacts('task',1,[dict(manifest,kind='SOURCE')],proofs).code == 'INVALID_INPUT'
    assert a.submit_artifacts('task',1,[dict(manifest,resource_path='../foreign')],proofs).code == 'SCOPE_EXCEEDED'
    env['db'].conn.execute('UPDATE v1_worker_leases SET fenced=1 WHERE lease_id=?',('work-lease',))
    assert a.submit_artifacts('task',1,[manifest],proofs).code == 'LEASE_FENCED'
