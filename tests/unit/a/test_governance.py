"""WG-A isolated contract/negative checks; no fake APSW/GitHub/Drive PASS."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
from studio.governance import Governance, GovernanceError, Principal, canonical_bytes, digest, parse_manifest
from studio.governance.memory import (
    MemoryBlobSource, MemoryIdentity, MemoryImpactCatalog, MemorySubjectCatalog,
    MemoryTransactions,
)


class GovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = MemoryTransactions()
        self.db.create_project('p1', owner_id='owner')
        self.db.create_project('p2', owner_id='other-owner')
        self.auth = MemoryIdentity()
        self.auth.provision('proof-owner', Principal('owner', 'trusted.test', 'human'))
        self.auth.provision('proof-other', Principal('other-owner', 'trusted.test', 'human'))
        self.auth.provision('proof-worker', Principal('worker', 'trusted.test', 'worker'))
        self.auth.provision('proof-human', Principal('member', 'trusted.test', 'human'))
        self.blobs = MemoryBlobSource()
        self.subjects = MemorySubjectCatalog()
        self.impacts = MemoryImpactCatalog()
        self.api = Governance(self.db, self.auth, self.blobs, self.subjects, self.impacts)
        self.owner = self.actor('owner', 'proof-owner', ['p1'])
        self.other_owner = self.actor('other-owner', 'proof-other', ['p2'])
        self.worker = self.actor('worker', 'proof-worker', ['p1'])
        self.member = self.actor('member', 'proof-human', ['p1'])
        with self.db.project('p1') as s:
            s.grants.update({('worker', 'worker'), ('member', 'member')})

    @staticmethod
    def actor(principal: str, proof: str, projects: list[str]) -> dict:
        return {'principal_id': principal, 'issuer': 'trusted.test',
                'trusted_proof_ref': proof, 'allowed_project_ids': projects}

    def raises(self, code: str, func, *args, **kwargs) -> None:
        with self.assertRaises(GovernanceError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def make_manifest(self, text='Canonical clause', key='rule1', status='verified', task='task1', fact_state='verified'):
        return canonical_bytes({'format': 'nomad.bible.manifest.v1', 'clauses': [{
            'clause_key': key, 'classification': 'requirement', 'text': text,
            'scope': {'region': '*'}, 'status': status,
            'source': {'document_id': 'drive-doc', 'revision': 'r1', 'start': 0, 'end': 10},
            'impacted_task_ids': [task]}], 'facts': [{
            'fact_id': 'fact1', 'subject_key': 'canyon', 'predicate': 'terrain',
            'value': 'salt', 'status': fact_state, 'evidence': {'source': 'source:line:123'}}]})

    def new_bible(self, kind='design', request_id=None, *, project='p1', actor=None):
        return self.api.createBible(actor or self.owner, project, {'request_id': request_id or 'create-'+kind,
                                                                    'title': kind+' Bible', 'kind': kind})['bible_id']

    def stage(self, bible, *, kind='design', suffix='1', project='p1', actor=None,
              expected=None, manifest=None):
        content = f'{kind} source {suffix}'.encode()
        cb = self.blobs.put_verified(project, content, 'bible-content')
        mb = self.blobs.put_verified(project, manifest or self.make_manifest(), 'bible-manifest')
        request = {'request_id': 'stage-'+kind+'-'+suffix, 'content_digest': cb, 'manifest_digest': mb}
        if expected is not None:
            request['expected_edition_id'] = expected
        return self.api.stageBible(actor or self.owner, project, bible, request)['intent_id']

    def approve(self, bible, effect, *, suffix='1', snapshot=None, actor=None, project='p1'):
        req = {'request_id': 'decision-'+suffix, 'subject_type': 'bible',
               'subject_id': bible, 'action': 'bible.publish', 'effect_digest': effect}
        if snapshot is not None:
            req['snapshot_id'] = snapshot
        decision_id = self.api.requestDecision(actor or self.owner, project, req)['decision_id']
        self.api.resolveDecision(self.owner if project == 'p1' else self.other_owner,
                                 project, decision_id, {'request_id': 'resolution-'+suffix,
                                 'decision': 'approve', 'expected_revision': 1,
                                 'expected_effect_digest': effect})
        return decision_id

    def publication(self, bible, *, kind='design', suffix='1', expected=None, manifest=None):
        intent = self.stage(bible, kind=kind, suffix=suffix, expected=expected, manifest=manifest)
        effect = self.api.prepare_publication_effect(self.owner, 'p1', bible, intent)
        snap = self.api.getCanon(self.owner, 'p1')['snapshot_id'] if expected is not None else None
        decision = self.approve(bible, effect, suffix=suffix+kind, snapshot=snap)
        req = {'request_id': 'publish-'+kind+'-'+suffix, 'intent_id': intent,
               'decision_id': decision, 'effect_digest': effect}
        if expected is not None:
            req['expected_edition_id'] = expected
        return self.api.publishBible(self.owner, 'p1', bible, req), req, decision

    def full_canon(self):
        b1 = self.new_bible('design')
        b2 = self.new_bible('technical')
        r1, _, _ = self.publication(b1, kind='design')
        r2, _, _ = self.publication(b2, kind='technical')
        return b1, b2, r1, r2

    def test_frozen_wg_a_operator_names_present(self):
        operations = ('listBibles', 'createBible', 'stageBible', 'diffBible',
                      'publishBible', 'getCanon', 'requestDecision', 'resolveDecision',
                      'listDecisions', 'getProjectPolicy', 'setProjectPolicy',
                      'listProjectGrants', 'grantProjectAccess', 'revokeProjectAccess')
        self.assertEqual(len(operations), 14)
        self.assertTrue(all(callable(getattr(self.api, op, None)) for op in operations))

    def test_two_distinct_projects_isolated(self):
        b = self.new_bible()
        self.raises('forbidden', self.api.listBibles, self.owner, 'p2')
        self.raises('forbidden', self.api.listBibles, self.other_owner, 'p1')
        self.raises('missing', self.api.diffBible, self.owner, 'p1', 'guessed')
        b2 = self.new_bible(project='p2', actor=self.other_owner, request_id='create')
        self.assertNotEqual(b, b2)
        self.assertEqual(len(self.api.listBibles(self.owner, 'p1')['bibles']), 1)

    def test_worker_fake_owner_flag_rejected(self):
        b = self.new_bible()
        effect = 'f'*64
        decision = self.api.requestDecision(self.worker, 'p1', {'request_id': 'req',
            'subject_type': 'bible', 'subject_id': b, 'action': 'bible.publish', 'effect_digest': effect})
        spoofed = dict(self.worker, is_owner=True, kind='human', verified_owner_principal_id='owner')
        self.raises('requires_owner', self.api.resolveDecision, spoofed, 'p1', decision['decision_id'],
                    {'request_id': 'resolve', 'decision': 'approve', 'expected_revision': 1,
                     'expected_effect_digest': effect})

    def test_claimed_owner_principal_identity_mismatch(self):
        forged = dict(self.worker, principal_id='owner', is_owner=True)
        self.raises('forbidden', self.api.listBibles, forged, 'p1')
        forged = dict(self.worker, trusted_proof_ref='unverified')
        self.raises('forbidden', self.api.listBibles, forged, 'p1')

    def test_member_not_owner_for_policy_and_grants(self):
        self.raises('requires_owner', self.api.setProjectPolicy, self.member, 'p1',
                    {'request_id': 'x', 'expected_revision': 0, 'policy_digest': 'f'*64, 'rules': []})
        self.raises('requires_owner', self.api.grantProjectAccess, self.member, 'p1',
                    {'request_id': 'y', 'principal_id': 'worker', 'grant_code': 'owner'})

    def test_complete_immutable_design_technical_canon(self):
        b1, b2, r1, r2 = self.full_canon()
        self.assertEqual(r1['snapshot_id'], '')  # first edition, pair not yet complete
        self.assertTrue(r2['snapshot_id'].startswith('canon_'))
        canon = self.api.getCanon(self.worker, 'p1')
        self.assertEqual(canon['design_edition_id'], r1['edition_id'])
        self.assertEqual(canon['technical_edition_id'], r2['edition_id'])
        self.assertEqual(canon['snapshot_id'], r2['snapshot_id'])
        with self.db.project('p1') as s:
            self.assertEqual(len(s.editions), 2)
            self.assertEqual(len(s.consumed_decisions), 2)

    def test_canon_read_fails_until_both_editions_exist(self):
        b = self.new_bible()
        self.publication(b)
        self.raises('missing', self.api.getCanon, self.owner, 'p1')

    def test_replayed_publish_is_identical_and_single_effect(self):
        b = self.new_bible()
        result, req, _ = self.publication(b)
        self.assertEqual(self.api.publishBible(self.owner, 'p1', b, req), result)
        with self.db.project('p1') as s:
            self.assertEqual(len(s.effects), 1)
            self.assertEqual(len(s.editions), 1)
        altered = dict(req, effect_digest='a'*64)
        self.raises('conflict', self.api.publishBible, self.owner, 'p1', b, altered)

    def test_approval_bound_to_effect_and_consumed(self):
        b = self.new_bible()
        intent = self.stage(b)
        effect = self.api.prepare_publication_effect(self.owner, 'p1', b, intent)
        decision = self.approve(b, effect)
        req = {'request_id': 'pub', 'intent_id': intent, 'decision_id': decision,
               'effect_digest': 'd'*64}
        self.raises('conflict', self.api.publishBible, self.owner, 'p1', b, req)
        req['effect_digest'] = effect
        self.api.publishBible(self.owner, 'p1', b, req)
        self.raises('conflict', self.api.publishBible, self.owner, 'p1', b,
                    dict(req, request_id='pub2'))

    def test_wrong_subject_or_rejected_approval(self):
        b = self.new_bible()
        intent = self.stage(b)
        effect = self.api.prepare_publication_effect(self.owner, 'p1', b, intent)
        decision = self.api.requestDecision(self.owner, 'p1', {'request_id': 'd',
           'subject_type': 'bible', 'subject_id': b, 'action': 'bible.publish', 'effect_digest': effect})['decision_id']
        self.api.resolveDecision(self.owner, 'p1', decision,
                                 {'request_id': 'resolve', 'decision': 'reject', 'expected_revision': 1,
                                  'expected_effect_digest': effect})
        self.raises('requires_owner', self.api.publishBible, self.owner, 'p1', b,
                    {'request_id': 'pub', 'intent_id': intent, 'decision_id': decision, 'effect_digest': effect})

    def test_stale_policy_and_canonical_hash(self):
        rules = [{'rule_id': 'r1', 'subject_type': 'bible', 'action': 'bible.publish',
                  'scope_digest': '0'*64, 'disposition': 'owner_required'}]
        req = {'request_id': 'policy1', 'expected_revision': 0, 'rules': rules,
               'policy_digest': digest(rules)}
        result = self.api.setProjectPolicy(self.owner, 'p1', req)
        self.assertEqual(result['revision'], 1)
        self.assertEqual(self.api.setProjectPolicy(self.owner, 'p1', req), result)
        self.raises('stale_revision', self.api.setProjectPolicy, self.owner, 'p1', dict(req, request_id='next'))
        self.raises('conflict', self.api.setProjectPolicy, self.owner, 'p1',
                    dict(req, request_id='new', expected_revision=1, policy_digest='f'*64))

    def test_policy_exact_scope_and_forbidden_overrides(self):
        scope = '1'*64
        rules = [{'rule_id': 'a', 'subject_type': 'task', 'action': 'assign',
                  'scope_digest': scope, 'disposition': 'delegated'},
                 {'rule_id': 'b', 'subject_type': 'task', 'action': 'assign',
                  'scope_digest': scope, 'disposition': 'forbidden'}]
        self.api.setProjectPolicy(self.owner, 'p1', {'request_id': 'p', 'expected_revision': 0,
                        'rules': rules, 'policy_digest': digest(sorted(rules, key=lambda r: r['rule_id']))})
        self.assertEqual(self.api.evaluate_policy(self.worker, 'p1', subject_type='task',
                         action='assign', scope_digest=scope), 'forbidden')
        self.assertEqual(self.api.evaluate_policy(self.worker, 'p1', subject_type='task',
                         action='assign', scope_digest='0'*64), 'owner_required')

    def test_proposed_extractions_excluded_from_context(self):
        b1 = self.new_bible('design')
        b2 = self.new_bible('technical')
        self.publication(b1, kind='design', manifest=self.make_manifest(status='proposed', fact_state='draft'))
        r2, _, _ = self.publication(b2, kind='technical')
        ctx = self.api.compile_context(self.worker, 'p1', r2['snapshot_id'])
        self.assertEqual(len(ctx['entries']), 2)  # only technical verified clause and fact
        self.assertTrue(all(e['bible_id'] == b2 for e in ctx['entries']))

    def test_context_pinned_revision_and_explicit_omissions(self):
        b1, _, r1, r2 = self.full_canon()
        before = self.api.compile_context(self.owner, 'p1', r2['snapshot_id'], max_entries=1)
        self.assertEqual(len(before['entries']), 1)
        self.assertEqual(len(before['omissions']), 3)
        intent = self.stage(b1, kind='design', suffix='2', expected=r1['edition_id'],
                            manifest=self.make_manifest(text='Changed'))
        eff = self.api.prepare_publication_effect(self.owner, 'p1', b1, intent)
        d = self.approve(b1, eff, suffix='2', snapshot=r2['snapshot_id'])
        new = self.api.publishBible(self.owner, 'p1', b1, {'request_id': 'pub2', 'intent_id': intent,
                    'decision_id': d, 'effect_digest': eff, 'expected_edition_id': r1['edition_id']})
        self.assertNotEqual(new['snapshot_id'], r2['snapshot_id'])
        self.assertEqual(self.api.compile_context(self.owner, 'p1', r2['snapshot_id'], max_entries=1), before)

    def test_stale_snapshot_resolution_rejected(self):
        b1, _, r1, r2 = self.full_canon()
        intent = self.stage(b1, kind='design', suffix='2', expected=r1['edition_id'])
        eff = self.api.prepare_publication_effect(self.owner, 'p1', b1, intent)
        proposed = self.api.requestDecision(self.owner, 'p1', {'request_id': 'd-old', 'subject_type': 'bible',
                   'subject_id': b1, 'action': 'bible.publish', 'effect_digest': eff,
                   'snapshot_id': r2['snapshot_id']})['decision_id']
        # Only a proven owner policy edit changes the current snapshot.
        self.api.setProjectPolicy(self.owner, 'p1', {'request_id': 'policy', 'expected_revision': 0,
                                  'rules': [], 'policy_digest': digest([])})
        self.raises('stale_snapshot', self.api.resolveDecision, self.owner, 'p1', proposed,
                    {'request_id': 'r', 'decision': 'approve', 'expected_revision': 1,
                     'expected_effect_digest': eff})

    def test_stage_rejects_stale_base_and_cross_project_blob(self):
        b = self.new_bible()
        self.raises('stale_revision', self.api.stageBible, self.owner, 'p1', b,
                    {'request_id': 'x', 'content_digest': 'a'*64, 'manifest_digest': 'b'*64,
                     'expected_edition_id': 'nonexistent'})
        cb = self.blobs.put_verified('p2', b'other project', 'bible-content')
        mb = self.blobs.put_verified('p2', self.make_manifest(), 'bible-manifest')
        self.raises('forbidden', self.api.stageBible, self.owner, 'p1', b,
                    {'request_id': 'y', 'content_digest': cb, 'manifest_digest': mb})

    def test_blob_checksum_corruption_denied(self):
        b = self.new_bible()
        cb = self.blobs.put_verified('p1', b'valid bytes', 'bible-content')
        mb = self.blobs.put_verified('p1', self.make_manifest(), 'bible-manifest')
        self.blobs.corrupt(cb, b'altered')
        self.raises('conflict', self.api.stageBible, self.owner, 'p1', b,
                    {'request_id': 'z', 'content_digest': cb, 'manifest_digest': mb})

    def test_invalid_manifest_or_duplicate_key(self):
        self.raises('invalid', parse_manifest, b'{bad')
        raw = json.loads(self.make_manifest())
        raw['clauses'].append(raw['clauses'][0])
        self.raises('conflict', parse_manifest, canonical_bytes(raw))
        raw = json.loads(self.make_manifest())
        raw['facts'][0]['evidence'] = {'note': 'model guesses'}
        self.raises('invalid', parse_manifest, canonical_bytes(raw))

    def test_diff_reports_changed_rule_and_pinned_task_references(self):
        b1, _, r1, _ = self.full_canon()
        self.impacts.references[('p1','rule1')] = {'task-outside'}
        self.stage(b1, kind='design', suffix='2', expected=r1['edition_id'],
                   manifest=self.make_manifest(text='New', task='task-new'))
        diff = self.api.diffBible(self.owner, 'p1', b1)
        self.assertEqual(diff['changed_clause_ids'], ['rule1'])
        self.assertEqual(diff['impacted_task_ids'], ['task-new', 'task-outside', 'task1'])

    def test_subject_must_exist_even_with_guessable_id(self):
        self.raises('missing', self.api.requestDecision, self.owner, 'p1',
                    {'request_id': 'req', 'subject_type': 'task', 'subject_id': 'guessed',
                     'action': 'dispatch', 'effect_digest': 'a'*64})

    def test_revocation_before_consumption_and_immutability(self):
        b = self.new_bible()
        effect = 'a'*64
        decision = self.approve(b, effect, suffix='rev')
        revoked = self.api.resolveDecision(self.owner, 'p1', decision,
                        {'request_id': 'revoke', 'decision': 'revoke', 'expected_revision': 2,
                         'expected_effect_digest': effect})
        self.assertEqual(revoked['state'], 'revoked')
        self.assertEqual(self.api.listDecisions(self.owner, 'p1')['decisions'][0]['state'], 'revoked')

    def test_owner_project_grants_and_introspection(self):
        self.subjects.subjects.add(('p1', 'principal', 'member'))
        self.api.grantProjectAccess(self.owner, 'p1', {'request_id': 'g', 'principal_id': 'member',
                                                      'grant_code': 'reviewer'})
        mine = self.api.listProjectGrants(self.member, 'p1')['grants']
        self.assertEqual({x['grant_code'] for x in mine}, {'member', 'reviewer'})
        self.assertTrue(any(x['grant_code'] == 'owner' for x in self.api.listProjectGrants(self.owner, 'p1')['grants']))
        self.api.revokeProjectAccess(self.owner, 'p1', {'request_id': 'rv', 'principal_id': 'member',
                                                       'grant_code': 'reviewer'})
        self.assertEqual(len(self.api.listProjectGrants(self.member, 'p1')['grants']), 1)

    def test_rollback_on_denied_mutation(self):
        b = self.new_bible()
        self.raises('requires_owner', self.api.resolveDecision, self.worker, 'p1', 'nonexistent',
                    {'request_id': 'x', 'decision': 'approve', 'expected_revision': 1,
                     'expected_effect_digest': 'f'*64})
        with self.db.project('p1') as s:
            self.assertFalse(s.decisions)
            self.assertFalse(s.effects)

    def test_effect_requires_approved_scope_and_exact_snapshot(self):
        self.subjects.subjects.add(('p1','task','task1'))
        self.raises('requires_owner', self.api.authorize_effect, self.worker, 'p1',
                    effect_id='effect1', subject_type='task', subject_id='task1', action='task.dispatch',
                    effect_digest='f'*64, snapshot_id=None, decision_id=None, request_id='act',
                    scope_digest='0'*64)
        with self.db.project('p1') as s:
            self.assertEqual(len(s.effects), 0)

    def test_approval_cannot_authorize_wrong_effect(self):
        self.subjects.subjects.add(('p1','task','task1'))
        d = self.api.requestDecision(self.owner, 'p1', {'request_id': 'dtask', 'subject_type': 'task',
                    'subject_id': 'task1', 'action': 'task.dispatch', 'effect_digest': 'a'*64})['decision_id']
        self.api.resolveDecision(self.owner, 'p1', d, {'request_id': 'rdtask', 'decision': 'approve',
                                                    'expected_revision': 1, 'expected_effect_digest': 'a'*64})
        self.raises('requires_owner', self.api.authorize_effect, self.worker, 'p1',
                    effect_id='e', subject_type='task', subject_id='task1', action='task.dispatch',
                    effect_digest='b'*64, snapshot_id=None, decision_id=d, request_id='req',
                    scope_digest='0'*64)

    def test_owner_grant_does_not_leak_to_other_project(self):
        self.raises('forbidden', self.api.getProjectPolicy, self.owner, 'p2')
        self.raises('forbidden', self.api.grantProjectAccess, self.owner, 'p2',
                    {'request_id': 'g', 'principal_id': 'worker', 'grant_code': 'owner'})


    def test_consumed_approval_cannot_be_revoked(self):
        b = self.new_bible()
        _, _, d = self.publication(b)
        self.raises('conflict', self.api.resolveDecision, self.owner, 'p1', d,
                    {'request_id': 'revoke-used', 'decision': 'revoke',
                     'expected_revision': 2,
                     'expected_effect_digest': self.api.listDecisions(self.owner, 'p1')['decisions'][0]['effect_digest']})

    def test_idempotency_collision_rejected_for_mutation(self):
        b = self.new_bible()
        self.raises('conflict', self.api.createBible, self.owner, 'p1',
                    {'request_id': 'create-design', 'title': 'different', 'kind': 'design'})
        self.assertEqual(len(self.api.listBibles(self.owner, 'p1')['bibles']), 1)

    def test_unauthorized_grant_replay_fails_after_revocation(self):
        b = self.new_bible()
        with self.db.project('p1') as s:
            s.grants.remove(('worker', 'worker'))
        self.raises('forbidden', self.api.listBibles, self.worker, 'p1')
        self.raises('forbidden', self.api.createBible, self.worker, 'p1',
                    {'request_id': 'create-design', 'title': 'design Bible', 'kind': 'design'})

    def test_wrong_snapshot_cannot_read_other_project(self):
        _, _, _, last = self.full_canon()
        self.raises('forbidden', self.api.compile_context, self.other_owner,
                    'p1', last['snapshot_id'])
        self.raises('missing', self.api.compile_context, self.other_owner,
                    'p2', last['snapshot_id'])

    def test_rejected_transaction_produces_no_event(self):
        b = self.new_bible()
        with self.db.project('p1') as s:
            event_count = len(s.events)
        self.raises('requires_owner', self.api.resolveDecision, self.worker, 'p1', 'missing',
                    {'request_id': 'no', 'decision': 'approve', 'expected_revision': 1,
                     'expected_effect_digest': 'b'*64})
        with self.db.project('p1') as s:
            self.assertEqual(len(s.events), event_count)

    def test_idempotent_publish_does_not_append_second_event(self):
        b = self.new_bible()
        _, req, _ = self.publication(b)
        with self.db.project('p1') as s:
            n = len(s.events)
        self.api.publishBible(self.owner, 'p1', b, req)
        with self.db.project('p1') as s:
            self.assertEqual(len(s.events), n)
            self.assertTrue(any(e.event_type == 'bible.published' for e in s.events))

    def test_stale_decision_revision_and_digest_rejected(self):
        b = self.new_bible()
        eff = 'a'*64
        d = self.api.requestDecision(self.member, 'p1', {'request_id': 'new',
                    'subject_type': 'bible', 'subject_id': b,
                    'action': 'bible.publish', 'effect_digest': eff})['decision_id']
        self.raises('stale_revision', self.api.resolveDecision, self.owner, 'p1', d,
                    {'request_id': 'resolve1', 'decision': 'approve',
                     'expected_revision': 2, 'expected_effect_digest': eff})
        self.raises('conflict', self.api.resolveDecision, self.owner, 'p1', d,
                    {'request_id': 'resolve2', 'decision': 'approve',
                     'expected_revision': 1, 'expected_effect_digest': 'b'*64})
        with self.db.project('p1') as state:
            self.assertEqual(state.proposals[d].state, 'pending')

    def test_delegated_effect_requires_exact_grant_and_policy(self):
        self.subjects.subjects.add(('p1', 'task', 'task1'))
        rule = {'rule_id': 'delegate-dispatch', 'subject_type': 'task',
                'action': 'task.dispatch', 'scope_digest': '1'*64,
                'disposition': 'delegated'}
        self.api.setProjectPolicy(self.owner, 'p1', {'request_id': 'policy',
                    'expected_revision': 0, 'rules': [rule], 'policy_digest': digest([rule])})
        self.raises('requires_owner', self.api.authorize_effect, self.worker, 'p1',
                    effect_id='effect1', subject_type='task', subject_id='task1',
                    action='task.dispatch', effect_digest='a'*64, snapshot_id=None,
                    decision_id=None, request_id='dispatch', scope_digest='1'*64)
        with self.db.project('p1') as state:
            state.grants.add(('worker', 'delegate:task.dispatch'))
        e = self.api.authorize_effect(self.worker, 'p1', effect_id='effect1',
                subject_type='task', subject_id='task1', action='task.dispatch',
                effect_digest='a'*64, snapshot_id=None, decision_id=None,
                request_id='dispatch', scope_digest='1'*64)
        self.assertEqual(e.effect_id, 'effect1')
        self.assertEqual(len(self.api.listDecisions(self.owner, 'p1')['decisions']), 0)
        self.assertEqual(self.api.authorize_effect(self.worker, 'p1', effect_id='effect1',
                subject_type='task', subject_id='task1', action='task.dispatch',
                effect_digest='a'*64, snapshot_id=None, decision_id=None,
                request_id='dispatch', scope_digest='1'*64), e)
        self.raises('conflict', self.api.authorize_effect, self.worker, 'p1',
                    effect_id='effect2', subject_type='task', subject_id='task1',
                    action='task.dispatch', effect_digest='a'*64, snapshot_id=None,
                    decision_id=None, request_id='dispatch', scope_digest='1'*64)


if __name__ == '__main__':
    unittest.main()
