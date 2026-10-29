"""R9 CAB-B local only: exact DDL, transaction rollback and port doubles.

Fake provider responses are NEVER live host/owner authorization receipts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from nomad_studio.production.intake._support import Result, text_digest
from nomad_studio.organization.api import ProjectRegistry
from nomad_studio.production.intake.api import BriefService
from nomad_studio.production.contract.api import FeatureContractCompiler


def frozen_sql() -> str:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / 'CONTRACTS' / 'initial_schema_v1.sql'
        if candidate.is_file():
            return candidate.read_text()
    raise FileNotFoundError('The frozen R9 initial_schema_v1.sql must accompany CAB-B tests')


class ScopedTx:
    def __init__(self, db): self.db = db
    def fetch_one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None
    def fetch_all(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]
    def execute(self, sql, params=()):
        import re
        match = re.match(r'\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)', sql, re.I)
        assert match and match.group(1) in {'v1_projects', 'v1_project_constitutions', 'v1_briefs', 'v1_feature_contracts'}, sql
        return self.db.execute(sql, params)


class FakeCabBStore:
    def __init__(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(frozen_sql())
        self.db.execute('PRAGMA foreign_keys=ON')
        self.lock = RLock()
    def read(self, cb):
        with self.lock: return cb(ScopedTx(self.db))
    def write(self, cb):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                response = cb(ScopedTx(self.db))
                self.db.execute('COMMIT')
                return response
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
    def close(self): self.db.close()


PAIR = {'project_id':'game-A', 'design_edition_id':'design-1', 'technical_edition_id':'technical-1',
        'design_manifest_digest':'a'*64, 'technical_manifest_digest':'b'*64,
        'design_epoch':1, 'technical_epoch':1, 'pair_digest':'c'*64}

def clause(clause_id, text, kind='REQUIREMENT'):
    return {'clause_id':clause_id, 'text':text, 'text_digest':text_digest(text), 'scope':'feature',
            'source_ref':'source:'+clause_id, 'precedence':1, 'kind':kind}


class FakeBible:
    def __init__(self):
        self.pair = dict(PAIR)
        self.bundle = {'project_id':'game-A', 'pair_digest':'c'*64,
                       'design_edition_id':'design-1', 'technical_edition_id':'technical-1',
                       'design_clauses':[clause('DES-1', 'All playable quests must keep checkpoints')],
                       'technical_clauses':[clause('TECH-1', 'Executable scenes require build smoke tests')]}
    def snapshot_active_pair(self, project_id, feature_scope):
        if project_id != self.pair['project_id'] or feature_scope['project_id'] != project_id:
            return Result('REJECTED','WRONG_PROJECT')
        return Result('OK','OK', dict(self.pair))
    def get_clause_bundle(self, pair_digest, scope):
        if pair_digest != self.pair['pair_digest'] or scope['project_id'] != self.pair['project_id']:
            return Result('REJECTED','STALE_BIBLE')
        clauses = []
        for kind, edition, items in (('DESIGN','design-1',self.bundle['design_clauses']),
                                      ('TECHNICAL','technical-1',self.bundle['technical_clauses'])):
            for item in items:
                clauses.append({'ref':edition+':'+item['clause_id'], 'edition_id':edition,
                                'bible_kind':kind, **{k:v for k,v in item.items() if k != 'text'}})
        return Result('OK','OK',{'project_id':scope['project_id'],'bible_pair_digest':pair_digest,
                                 'scope':'*','clauses':clauses})


class FakeContext:
    def __init__(self): self.bundle = context()
    def build_bundle(self, project_id, brief_id, bible_pair_digest, source_fence):
        if source_fence is not None: return Result('REJECTED','INVALID_INPUT')
        return Result('OK', 'OK', self.bundle)


class FakeAuthority:
    def assess_bible_conflict(self, feature_id, decision_or_diff, bible_pair_digest):
        if 'remove checkpoints' in decision_or_diff['original_text'].lower():
            return Result('CONFLICT','BIBLE_CONFLICT', {'violations':['design-1:DES-1']})
        return Result('OK','OK', {})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: str
    code: str


class FakeIdentity:
    def __init__(self): self.allowed_proof = 'amend-proof'
    def verify_owner_decision(self, proof_ref, action, subject_digest, project_id):
        good = proof_ref == self.allowed_proof and action == 'production.amend' and project_id == 'game-A' and len(subject_digest) == 64
        return Decision(good, 'ALLOW' if good else 'DENY', 'OK' if good else 'PERMISSION_DENIED')


OWNER = Result('OK','OK', {'principal_id':'owner-A'}, 'owner-session')


def services():
    state = FakeCabBStore()
    project = ProjectRegistry(state)
    bible = FakeBible()
    identity = FakeIdentity()
    verified_context = FakeContext()
    compiler = FeatureContractCompiler(state, bible, FakeAuthority(), identity, context_port=verified_context)
    compiler.verified_context = verified_context  # test fixture only
    brief = BriefService(state, lambda: 1_800_000_000_000_000, compiler)
    return state, project, brief, compiler, bible, identity


def bootstrap(project, brief, original='Implement playable quest dialogue scene with branching checkpoints and tests', feature_id='feature-A', brief_id='brief-A'):
    spec = {'project_id':'game-A', 'owner_principal_id':'owner-A', 'constitution_digest':'d'*64,
            'initial_state':'ACTIVE', 'repository_ref':'verified/game-source'}
    assert project.register_project(spec, OWNER).status == 'OK'
    # Simulated CAB-D writes in the fixture are separate from CAB-B source.
    db = project.state.db
    if db.execute('SELECT count(*) FROM v1_bible_editions WHERE project_id=?',('game-A',)).fetchone()[0] == 0:
        for kind, label, digest in [('DESIGN','design-1','a'*64),('TECHNICAL','technical-1','b'*64)]:
            db.execute('INSERT INTO v1_bible_editions(project_id,kind,edition_id,epoch,manifest_digest,source_repo,source_commit,owner_activation_ref,active,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('game-A',kind,label,1,digest,'repo','0'*40,'activation-1',1,'{}'))
    owner = Result('OK','OK',{'principal_id':'owner-A'}, 'command-session')
    command = {'project_id':'game-A', 'brief_id':brief_id, 'feature_id':feature_id,
               'original_text':original, 'owner_command_ref':'command-session'}
    assert brief.admit_brief(command, owner).status == 'OK'
    return command


def context(brief_id='brief-A'):
    design, technical = (clause('DES-1', 'All playable quests must keep checkpoints'),
                         clause('TECH-1', 'Executable scenes require build smoke tests'))
    excerpts = [{'ref': label+'/'+item['clause_id'],'kind':item['kind'],
                 'scope':item['scope'],'text':item['text'],'content_digest':item['text_digest']}
                for label,item in [('design',design),('technical',technical)]]
    return {'project_id':'game-A', 'brief_id':brief_id, 'feature_id':'feature-A',
            'bible_pair_digest':'c'*64, 'bundle_digest':'f'*64, 'bundle_ref':'cas/verified',
            'source_sha':'0'*40,
            'bible_pins':{'design':{'edition_id':'design-1','epoch':1,'manifest_digest':'a'*64},
                          'technical':{'edition_id':'technical-1','epoch':1,'manifest_digest':'b'*64}},
            'canonical_excerpts':excerpts,
            'facts': {'preexisting_code': 'UNTRUSTED_FROM_SOURCE'}}
