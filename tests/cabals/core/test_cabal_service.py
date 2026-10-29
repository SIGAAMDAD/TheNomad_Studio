"""CAB-A contract/adversarial coverage against the byte-exact frozen R9 DDL."""
from __future__ import annotations

from dataclasses import asdict, fields
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from nomad_studio.cabals.core import CabalService, Decision, Result

PAIR = 'c' * 64
CONTRACT = 'b' * 64
SCOPE = 'a' * 64
D = 'd' * 64
T = 'e' * 64
FIXTURE = Path(__file__).parent / 'fixtures' / 'frozen_schema.sql'
SQL_SHA256 = sha256(FIXTURE.read_bytes()).hexdigest()


class View:
    def __init__(self, connection):
        self.c = connection

    def fetch_one(self, statement, parameters=()):
        row = self.c.execute(statement, parameters).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, statement, parameters=()):
        return [dict(r) for r in self.c.execute(statement, parameters)]


class MutableView(View):
    def execute(self, statement, parameters=()):
        target = re.search(r'\bINSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(v1_\w+)', statement, re.I)
        if target is None or target.group(1) not in {'v1_cabals','v1_memberships','v1_deliberations'}:
            raise AssertionError('foreign-table write attempted')
        return self.c.execute(statement, parameters).rowcount


class LocalCABStorage:
    """Test-only SQLite adapter; not a provider, no production identity proof."""
    def __init__(self, connection):
        self.c = connection

    def read(self, fn):
        return fn(View(self.c))

    def write_owned(self, fn):
        self.c.execute('BEGIN IMMEDIATE')
        try:
            result = fn(MutableView(self.c))
            self.c.execute('COMMIT')
            return result
        except BaseException:
            self.c.execute('ROLLBACK')
            raise


class FixtureBible:
    def __init__(self):
        self.pair = PAIR
        self.design_epoch = 1

    def snapshot_active_pair(self, project_id, feature_scope):
        return Result('OK','OK', {
            'project_id': project_id,
            'design_edition_id': project_id+'-design',
            'technical_edition_id': project_id+'-tech',
            'design_epoch': self.design_epoch,
            'technical_epoch': 1,
            'design_manifest_digest': D,
            'technical_manifest_digest': T,
            'pair_digest': self.pair,
        })


def seed(connection):
    connection.executescript(FIXTURE.read_text())
    for project, feature, brief, contract, scope in [
        ('A','featureA','briefA', CONTRACT, SCOPE),
        ('B','featureB','briefB', 'f'*64, '9'*64),
    ]:
        connection.execute('INSERT INTO v1_projects VALUES (?,?,?,?,?)',
                           (project,'owner','constit','ACTIVE',1))
        for kind, suff, manifest in [('DESIGN','design',D),('TECHNICAL','tech',T)]:
            connection.execute('INSERT INTO v1_bible_editions VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (project,kind,project+'-'+suff,1,manifest,'repo','0'*40,'owner-proof',1,'{}'))
        connection.execute('INSERT INTO v1_briefs VALUES (?,?,?,?,?,?,?,?)',
                           (project,brief,feature,'owner','brief','orig',brief+'-command',1))
        connection.execute('INSERT INTO v1_feature_contracts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (project,feature,1,brief,'orig',PAIR,contract,scope,'d','a',100,'{}'))
        connection.execute('INSERT INTO v1_feature_runs VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (project,feature,brief,contract,PAIR,'IN_PRODUCTION',1,1,None,100))
        connection.execute('INSERT INTO v1_graph_revisions VALUES (?,?,?,?,?,?)',
                           (feature,1, 'graph',contract,PAIR,'{}'))
    for skill in ('engineering','writing','qa'):
        connection.execute('INSERT INTO v1_skill_capabilities VALUES (?,?,?,?,?)',
                           (skill,'s'*64,'HIGH', 'A', '{}'))
    for worker, skills in [('alice',('engineering','writing')),
                           ('bob',('writing',)), ('carol',('qa',))]:
        connection.execute('INSERT INTO v1_verified_identities VALUES (?,?,?,?,?,?)',
                           (worker,worker+'-cred','provider',1,10000,worker+'-proof'))
        for skill in skills:
            connection.execute('INSERT INTO v1_worker_capabilities VALUES (?,?,?,?)',
                               (worker,skill,worker+'-'+skill+'-verified',1))


def task(cabal_id, *, feature='featureA', project='A', worker_skill='engineering'):
    return {'project_id':project,'feature_id':feature,'task_id':'task1','graph_revision':1,
            'cabal_id':cabal_id,'bible_pair_digest':PAIR,'feature_contract_digest':CONTRACT,
            'dependencies':[], 'required_skills':[worker_skill], 'owned_resources':['game/test.cpp'],
            'input_digests':[], 'outputs':['output'], 'acceptance_ids':['smoke'],
            'budget_limit_units':10,'task_digest':'7'*64}


def question(text: str, members: list[str]):
    return {'question_text': text, 'question_digest': sha256(text.encode('utf-8')).hexdigest(),
            'member_ids': members}


class FixtureAuthority:
    def __init__(self):
        self.conflict = False

    def assess_bible_conflict(self, feature_id, decision_or_diff, bible_pair_digest):
        return Result('CONFLICT','BIBLE_CONFLICT') if self.conflict else Result('OK','OK')


class CABATests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:', isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        seed(self.c)
        self.bible = FixtureBible()
        self.actor = 'alice'
        self.now = 1000
        self.authority = FixtureAuthority()
        self.service = CabalService(LocalCABStorage(self.c),self.bible,self.authority,
                                    clock_us=lambda: self.now,
                                    authenticated_worker=lambda: self.actor)

    def tearDown(self):
        self.c.close()

    def reserved(self):
        result = self.service.reserve_cabals('featureA',PAIR,['engineering','qa','writing'])
        self.assertEqual(('OK','OK'), (result.status,result.code))
        return result.record['cabal_id']

    def persist_task(self, record):
        self.c.execute('INSERT INTO v1_tasks VALUES (?,?,?,?,?,?,?,?,?)',
                       (record['feature_id'],record['task_id'],record['graph_revision'],
                        record['cabal_id'],record['task_digest'],'READY',None,10,json.dumps(record,sort_keys=True)))

    def test_frozen_port_result_shapes(self):
        self.assertEqual([f.name for f in fields(Result)],
                         ['status','code','record','receipt_ref','evidence_refs'])
        self.assertEqual([f.name for f in fields(Decision)],
                         ['allowed','status','code','affected_ref','evidence_refs'])

    def test_multi_member_admission_and_temporary_subgroup(self):
        parent = self.reserved()
        roster = self.service.get_cabal_roster('A',parent)
        self.assertEqual('OK',roster.status)
        self.assertEqual({'alice','carol'}, {m['worker_id'] for m in roster.record['members']})
        subgroup = self.service.open_deliberation(parent,'featureA',
                        question('How to implement this feature?', ['alice','carol']))
        self.assertEqual('OK',subgroup.status)
        child = subgroup.record['cabal_id']
        self.assertNotEqual(child,parent)
        self.assertEqual({'alice','carol'},
                         {m['worker_id'] for m in self.service.get_cabal_roster('A',child).record['members']})
        self.assertEqual(1,self.c.execute('SELECT COUNT(*) FROM v1_deliberations').fetchone()[0])
        self.assertEqual([],self.c.execute('PRAGMA foreign_key_check').fetchall())

    def test_membership_never_grants_owner_authority(self):
        parent = self.reserved()
        row = self.c.execute('SELECT role_ref FROM v1_memberships WHERE cabal_id=? LIMIT 1',(parent,)).fetchone()
        self.assertIn(row[0],{'engineering','qa','writing'})
        self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM v1_owner_decisions').fetchone()[0])
        self.assertEqual(2,self.c.execute('SELECT COUNT(*) FROM v1_bible_editions WHERE project_id=?',('A',)).fetchone()[0])

    def test_wrong_membership_and_cross_project_task_denied(self):
        parent = self.reserved()
        item = task(parent)
        self.persist_task(item)
        self.assertTrue(self.service.authorize_cabal_task(parent,'alice',item).allowed)
        self.assertEqual('UNAUTHENTICATED',self.service.authorize_cabal_task(parent,'bob',item).code)
        self.actor = 'bob'
        self.assertEqual('PERMISSION_DENIED',self.service.authorize_cabal_task(parent,'bob',item).code)
        self.actor = 'alice'
        self.assertEqual('WRONG_PROJECT',self.service.authorize_cabal_task(parent,'alice',
                                                        {**item,'project_id':'B'}).code)
        self.assertEqual('WRONG_FEATURE',self.service.authorize_cabal_task(parent,'alice',
                           {**item,'task_id':'missing'}).code)

    def test_qualified_worker_allows_any_verified_skill_not_only_primary_role(self):
        parent = self.reserved()
        item = task(parent,worker_skill='writing')
        self.persist_task(item)
        self.assertTrue(self.service.authorize_cabal_task(parent,'alice',item).allowed)

    def test_task_payload_cannot_broaden_resource_or_skill_with_same_digest(self):
        parent = self.reserved()
        original = task(parent)
        self.persist_task(original)
        changed = {**original, 'owned_resources':['other/repo.py']}
        self.assertEqual('EVIDENCE_INVALID',
                         self.service.authorize_cabal_task(parent,'alice',changed).code)
        changed = {**original, 'required_skills':['writing']}
        self.assertEqual('EVIDENCE_INVALID',
                         self.service.authorize_cabal_task(parent,'alice',changed).code)

    def test_deliberation_replay_returns_original_group(self):
        parent = self.reserved()
        request = question('Question A', ['alice'])
        first = self.service.open_deliberation(parent,'featureA',request)
        self.assertEqual('OK',first.status)
        second = self.service.open_deliberation(parent,'featureA',request)
        self.assertEqual('OK',second.status)
        self.assertEqual(first.record,second.record)
        self.assertEqual(1,self.c.execute('SELECT COUNT(*) FROM v1_deliberations').fetchone()[0])

    def test_wrong_cabal_membership_and_foreign_cabal_denied(self):
        parent = self.reserved()
        item = task(parent)
        self.persist_task(item)
        self.assertEqual('SCOPE_EXCEEDED',self.service.authorize_cabal_task('other','alice',item).code)
        self.assertEqual('NOT_FOUND',self.service.get_cabal_roster('B',parent).code)

    def test_deliberation_requires_bible_compliant_content_and_actual_digest(self):
        parent = self.reserved()
        self.authority.conflict = True
        self.assertEqual('BIBLE_CONFLICT', self.service.open_deliberation(
            parent,'featureA',question('Contradict requirement', ['alice'])).code)
        self.authority.conflict = False
        bad = {**question('Question', ['alice']), 'question_digest':'1'*64}
        self.assertEqual('INVALID_INPUT', self.service.open_deliberation(parent,'featureA',bad).code)
        self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM v1_deliberations').fetchone()[0])

    def test_feature_pause_blocks_new_authority(self):
        parent = self.reserved()
        item = task(parent)
        self.persist_task(item)
        self.c.execute("UPDATE v1_feature_runs SET phase='PAUSED' WHERE feature_id='featureA'")
        self.assertEqual('PERMISSION_DENIED', self.service.authorize_cabal_task(parent,'alice',item).code)
        self.assertEqual('PERMISSION_DENIED', self.service.open_deliberation(parent,'featureA',
                         question('Paused question', ['alice'])).code)
        self.assertEqual('PERMISSION_DENIED', self.service.reserve_cabals('featureA',PAIR,
                         ['engineering']).code)

    def test_stale_active_bible_and_charter_epoch_fail_closed(self):
        parent = self.reserved()
        self.c.execute("UPDATE v1_bible_editions SET active=0 WHERE project_id='A' AND kind='DESIGN'")
        self.assertEqual('STALE_BIBLE',self.service.get_cabal_roster('A',parent).code)
        self.c.execute("UPDATE v1_bible_editions SET active=1 WHERE project_id='A' AND kind='DESIGN'")
        self.c.execute("UPDATE v1_projects SET authority_epoch=2 WHERE project_id='A'")
        self.assertEqual('REVISION_CONFLICT',self.service.get_cabal_roster('A',parent).code)

    def test_stale_snapshot_epoch_and_wrong_bible_pin(self):
        parent = self.reserved()
        self.bible.design_epoch = 2
        self.assertEqual('STALE_BIBLE',self.service.get_cabal_roster('A',parent).code)
        self.bible.design_epoch = 1
        self.bible.pair = '0'*64
        self.assertEqual('STALE_BIBLE',self.service.get_cabal_roster('A',parent).code)

    def test_stale_feature_graph_and_contract(self):
        parent = self.reserved()
        item = task(parent)
        self.persist_task(item)
        self.assertEqual('REVISION_CONFLICT',self.service.authorize_cabal_task(parent,'alice',
                        {**item,'graph_revision':2}).code)
        self.assertEqual('REVISION_CONFLICT',self.service.authorize_cabal_task(parent,'alice',
                        {**item,'feature_contract_digest':'6'*64}).code)
        self.c.execute("UPDATE v1_feature_runs SET bible_pair_digest=? WHERE feature_id='featureA'",('6'*64,))
        self.assertEqual('STALE_BIBLE',self.service.authorize_cabal_task(parent,'alice',item).code)

    def test_expired_identity_and_revoked_qualification(self):
        parent = self.reserved()
        item = task(parent)
        self.persist_task(item)
        self.c.execute("UPDATE v1_worker_capabilities SET available=0 WHERE worker_id='alice'")
        self.assertEqual('PERMISSION_DENIED',self.service.authorize_cabal_task(parent,'alice',item).code)
        self.c.execute("UPDATE v1_worker_capabilities SET available=1 WHERE worker_id='alice'")
        self.now = 10001
        self.assertEqual('PERMISSION_DENIED',self.service.authorize_cabal_task(parent,'alice',item).code)

    def test_deliberation_initiator_and_subgroup_subset_gate(self):
        parent = self.reserved()
        self.actor = 'bob'
        self.assertEqual('PERMISSION_DENIED',self.service.open_deliberation(parent,'featureA',
                         question('Question A', ['alice'])).code)
        self.actor = 'alice'
        self.assertEqual('PERMISSION_DENIED',self.service.open_deliberation(parent,'featureA',
                         question('Question B', ['bob'])).code)
        self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM v1_deliberations').fetchone()[0])

    def test_no_admission_without_verified_capability_and_identity(self):
        self.c.execute("UPDATE v1_verified_identities SET valid_until_us=999 WHERE principal_id='carol'")
        result = self.service.reserve_cabals('featureA',PAIR,['engineering','qa'])
        self.assertEqual(('BLOCKED_LOCAL','CAPACITY_LIMIT'),(result.status,result.code))
        self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM v1_cabals').fetchone()[0])

    def test_duplicate_reservation_is_stable_and_cannot_grow_members(self):
        parent = self.reserved()
        other = self.service.reserve_cabals('featureA',PAIR,['writing','qa','engineering'])
        self.assertEqual(parent,other.record['cabal_id'])
        self.assertEqual(2,self.c.execute('SELECT COUNT(*) FROM v1_memberships').fetchone()[0])
        self.c.execute("UPDATE v1_worker_capabilities SET available=0 WHERE worker_id='alice' AND skill_id='engineering'")
        self.assertEqual('CAPACITY_LIMIT',self.service.reserve_cabals('featureA',PAIR,['writing','qa','engineering']).code)

    def test_subgroup_cannot_act_as_parent_or_add_parent_members(self):
        parent = self.reserved()
        opened = self.service.open_deliberation(parent,'featureA',
                   question('Single-member temporary team', ['alice']))
        self.assertEqual('OK',opened.status)
        child = opened.record['cabal_id']
        child_task = task(child)
        self.persist_task(child_task)
        self.assertTrue(self.service.authorize_cabal_task(child,'alice',child_task).allowed)
        self.actor = 'carol'
        self.assertEqual('PERMISSION_DENIED',self.service.authorize_cabal_task(child,'carol',child_task).code)
        self.actor = 'alice'
        self.assertEqual('SCOPE_EXCEEDED',self.service.authorize_cabal_task(parent,'alice',child_task).code)

    def test_writer_transaction_rollback_on_constraint_failure(self):
        self.c.execute("CREATE TRIGGER cab_a_test_failure BEFORE INSERT ON v1_memberships "
                       "WHEN NEW.worker_id='carol' BEGIN SELECT RAISE(ABORT,'forced_failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.reserve_cabals('featureA',PAIR,['engineering','qa'])
        self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM v1_cabals').fetchone()[0])
        self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM v1_memberships').fetchone()[0])

    def test_project_scoped_qualification_cannot_cross_projects(self):
        self.c.execute("UPDATE v1_skill_capabilities SET project_scope_id='B' WHERE skill_id='qa'")
        self.assertEqual('CAPACITY_LIMIT',self.service.reserve_cabals(
                         'featureA',PAIR,['engineering','qa']).code)

    def test_cabal_pair_tamper_and_foreign_membership_fk(self):
        parent = self.reserved()
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('INSERT INTO v1_memberships VALUES (?,?,?,?,?)',
                           ('B', parent, 'alice', 'engineering', 9999))
        self.c.execute('UPDATE v1_cabals SET bible_pair_digest=? WHERE cabal_id=?', ('0'*64,parent))
        self.assertEqual('WRONG_FEATURE',self.service.get_cabal_roster('A',parent).code)

    def test_bad_payloads_rejected_not_crashing(self):
        parent = self.reserved()
        item = task(parent)
        for mutation in ({**item,'input_digests':[['malformed']]},
                         {**item,'budget_limit_units':True},
                         {**item,'extra':'untrusted'},
                         {**item,'required_skills':'engineering'}):
            self.assertEqual('INVALID_INPUT',self.service.authorize_cabal_task(parent,'alice',mutation).code)
        self.assertEqual('INVALID_INPUT',self.service.open_deliberation(parent,'featureA',
                         {'question_text':'Question', 'question_digest':sha256(b'Question').hexdigest(), 'member_ids':'alice'}).code)

    def test_no_foreign_table_mutation(self):
        parent = self.reserved()
        self.assertIsNotNone(parent)
        self.assertEqual(0, self.c.execute('SELECT COUNT(*) FROM v1_events').fetchone()[0])
        self.assertEqual(0, self.c.execute('SELECT COUNT(*) FROM v1_owner_decisions').fetchone()[0])
        self.assertEqual([],self.c.execute('PRAGMA foreign_key_check').fetchall())


if __name__ == '__main__':
    unittest.main()
