"""CAB cross-port contract tests, local fixtures only; not native live-host proof."""
from __future__ import annotations

import sys
from pathlib import Path
from hashlib import sha256
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for relative in ('tests/trust', 'tests/bibles', 'tests/organization', 'tests/production/context'):
    sys.path.insert(0, str(ROOT / relative))

from nomad_studio.contracts.runtime_ports_v1 import Result, Decision
from nomad_studio.production.director.commands import CommandService
from nomad_studio.production.director.workflow import ProductionWorkflow
from nomad_studio.production.intake._support import text_digest
from test_r9_identity import service as native_service
from test_r9_bibles import BibleAuthorityTests
from test_r9_cab_i import env as context_env, insert, NOW
from _r9_fixture import services, bootstrap


def test_native_owner_auth_uses_one_frozen_result_and_exact_action():
    digest = 'c' * 64
    native, _, identity = native_service()
    native.sign('native/owner', 'OWNER_COMMAND', principal_id='owner/1', credential_id='credential/1',
                epoch=4, valid_until_us=10_000_000, issued_at_us=900_000, principal_kind='OWNER',
                enrollment_ref='enrollment/1', action_digest=digest)
    auth = identity.authenticate_owner('native/owner', digest)
    assert type(auth) is Result
    assert auth.record['action_digest'] == digest
    command = {'owner_principal_id': 'owner/1', 'auth_proof_ref': 'native/owner', 'request_digest': digest}
    assert CommandService._authenticated(command, auth)
    assert not CommandService._authenticated({**command, 'request_digest': 'd' * 64}, auth)
    assert not CommandService._authenticated({**command, 'auth_proof_ref': 'other'}, auth)


def test_bibles_feature_scope_and_source_byte_verification():
    fixture = BibleAuthorityTests()
    fixture.setUp()
    try:
        pair = fixture.active()
        snap = fixture.bibles.snapshot_active_pair('proj', {'project_id': 'proj', 'feature_id': 'feature'})
        metadata = fixture.bibles.get_clause_bundle(pair, {'project_id': 'proj', 'feature_id': 'feature'})
        assert type(snap) is Result and type(metadata) is Result
        assert metadata.status == 'OK' and len(metadata.record['clauses']) == 2
        excerpt = fixture.bibles.excerpt(fixture.design['edition_id'], 'REQUIRED')
        assert excerpt['text'] == 'DESIGN required byte check'
        # Altering the trusted source bytes must invalidate the excerpt.
        item = fixture.source.items[fixture.design['edition_id']]
        item['files']['design-rule.md'] = b'tampered' if 'design-rule.md' in item['files'] else b'tampered'
        try:
            fixture.bibles.excerpt(fixture.design['edition_id'], 'REQUIRED')
        except Exception:
            pass
        else:
            raise AssertionError('tampered source must be rejected')
    finally:
        fixture.tearDown()


def test_precontract_context_is_content_addressed_without_fk_violation():
    with TemporaryDirectory() as folder:
        fixture = context_env.__wrapped__(Path(folder))
        db = fixture['db']
        insert(db, 'v1_briefs', project_id='project', brief_id='fresh-brief', feature_id='fresh-feature',
               owner_principal_id='owner', original_text='new feature',
               original_digest=sha256(b'new feature').hexdigest(), owner_command_ref='cmd/new', submitted_at_us=NOW)
        bundle = fixture['context'].build_bundle('project', 'fresh-brief', 'a'*64, None)
        assert type(bundle) is Result and bundle.status == 'OK'
        assert bundle.record['project_id'] == 'project'
        assert bundle.record['feature_id'] == 'fresh-feature'
        assert len(bundle.record['canonical_excerpts']) == 2
        assert not db.read('SELECT * FROM v1_context_bundles WHERE feature_id=?', ('fresh-feature',))
        assert fixture['cas'].get(bundle.record['bundle_ref'])
        assert fixture['context'].build_bundle('other','fresh-brief','a'*64,None).status == 'NOT_FOUND'


def test_brief_compiler_accepts_canonical_bible_metadata_and_verified_context():
    state, project, brief, compiler, bible, identity = services()
    try:
        bootstrap(project, brief)
        metadata, excerpts = [], []
        for kind, label, edition, clause in (
            ('DESIGN', 'design', 'design-1', bible.bundle['design_clauses'][0]),
            ('TECHNICAL', 'technical', 'technical-1', bible.bundle['technical_clauses'][0]),
        ):
            metadata.append({'ref': edition+':'+clause['clause_id'], 'edition_id':edition,
                             'bible_kind':kind, **clause})
            excerpts.append({'ref': label+'/'+clause['clause_id'], 'kind':clause['kind'],
                             'scope':clause['scope'], 'text':clause['text'],
                             'content_digest':clause['text_digest']})
        record = {'project_id':'game-A', 'brief_id':'brief-A', 'feature_id':'feature-A',
                  'bible_pair_digest':bible.pair['pair_digest'], 'bundle_digest':'a'*64,
                  'bundle_ref':'cas/test', 'source_sha':'0'*40,
                  'bible_pins':{
                      'design':{'edition_id':'design-1','epoch':1,'manifest_digest':'a'*64},
                      'technical':{'edition_id':'technical-1','epoch':1,'manifest_digest':'b'*64}},
                  'canonical_excerpts':excerpts}
        class Bible:
            def snapshot_active_pair(self, p, s):
                assert s['project_id'] == p
                return Result('OK','OK',bible.pair)
            def get_clause_bundle(self, p, s):
                assert s['project_id'] == 'game-A'
                return Result('OK','OK',{'project_id':'game-A','bible_pair_digest':p,
                                          'clauses':metadata})
        class Context:
            def build_bundle(self,*args): return Result('OK','OK',record)
        compiler.bible, compiler.context_port = Bible(),Context()
        output = compiler.compile_contract('brief-A', bible.pair, record)
        assert type(output) is Result and output.status == 'OK', output
        assert len(output.record['deliverables']) >= 5
        # A manipulated mandatory clause excerpt must fail, even if metadata is unchanged.
        state.db.execute('DELETE FROM v1_feature_contracts')
        modified = {**record, 'canonical_excerpts':[{**excerpts[0], 'text':'tampered'}, excerpts[1]]}
        compiler.context_port = SimpleNamespace(build_bundle=lambda *args:Result('OK','OK',modified))
        denied = compiler.compile_contract('brief-A', bible.pair, modified)
        assert denied.code == 'EVIDENCE_INVALID'
    finally:
        state.close()
