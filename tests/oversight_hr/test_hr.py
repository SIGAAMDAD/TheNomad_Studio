"""Isolated synthetic tests: all ports are deliberately fake, NOT external proof."""
from __future__ import annotations

from dataclasses import replace
import ast
from pathlib import Path
import tempfile
import unittest

from oversight_hr import (EvidenceRef, MemoryJournal, SealedReport, SQLiteJournal,
                          Status, build_component)
from oversight_hr.storage import Conflict, digest

D = 'a' * 64
E = 'b' * 64
F = 'c' * 64


class FakeAccess:
    def __init__(self):
        self.blocked = set()
        self.calls = 0

    def permits(self, principal, operation, case_id=None):
        self.calls += 1
        return principal in {'hr', 'investigator', 'reviewer', 'appellate', 'bridge'} and (
            principal, operation, case_id) not in self.blocked


class FakeOwner:
    def __init__(self):
        self.calls = []
    def verify(self, ref, action, subject, revision):
        self.calls.append((ref, action, subject, revision))
        return ref == 'fixture-owner-grant'


class FakeCustody:
    def __init__(self):
        self.available = True
        self.scopes = {'investigator', 'reviewer', 'appellate', 'hr'}
    def check_sealed(self, ref, sha, reporter):
        return self.available and ref.startswith('sealed://') and len(sha) == 64
    def scoped_access(self, case_id, principal):
        return self.available and principal in self.scopes


class FakeEvidence:
    def __init__(self):
        self.available = True
    def verify(self, source_ref, byte_hash, revision, collector):
        return self.available and source_ref.startswith('artifact://') and len(byte_hash) == 64 and bool(revision)


class FakeIndependence:
    def eligible(self, principal, case_id, subject, reporter, implicated, purpose):
        return principal not in {*implicated, subject, reporter, 'blocked'}


class FakeSignedNotice:
    def verify(self, signed):
        # Stub token, not an actual signature or trusted issuer verification.
        return signed.get('signature_b64') == 'fixture-marker-only'


class FakeAssignment:
    def __init__(self):
        self.epoch = {'w-1': 4}
    def current_epoch(self, worker):
        return self.epoch.get(worker)
    def current_lease_epoch(self, worker, job):
        return 12 if worker == 'w-1' and job == 'job-1' else None


class FakeEnforcement:
    def __init__(self):
        self.calls = 0
        self.readback_ok = True
        self.timeout = False
    def apply(self, signed):
        self.calls += 1
        if self.timeout:
            raise TimeoutError('synthetic timeout')
        return 'APPLIED'
    def readback(self, notice_id, action, subject, epoch):
        return self.readback_ok


class FakeArchive:
    def __init__(self):self.valid = True
    def attest_manifest(self, manifest):return self.valid


class HRTests(unittest.TestCase):
    def setUp(self):
        self.access = FakeAccess()
        self.owner = FakeOwner()
        self.custody = FakeCustody()
        self.evidence = FakeEvidence()
        self.independence = FakeIndependence()
        self.assignment = FakeAssignment()
        self.enforcement = FakeEnforcement()
        self.archive = FakeArchive()
        self.journal = MemoryJournal()
        self.deps = dict(access=self.access, owner=self.owner, custody=self.custody,
                         evidence=self.evidence, independence=self.independence,
                         assignment=self.assignment, enforcement=self.enforcement,
                         signed_notice=FakeSignedNotice(), archive=self.archive,
                         journal=self.journal, clock=lambda: 1000.0)
        self.hr = build_component(self.deps)

    def policy(self):
        self.assertEqual(self.hr.set_policy('hr', 'policy-1', 0, D,
                                           'fixture-owner-grant').status, Status.OK)

    def report(self, event_id='sealed-1'):
        return SealedReport(event_id, 'reporter', 'w-1', 'sealed://opaque', D,
                            'artifact://source', '2026-10-29T00:00:00Z', 'CLAIM')

    def intake_screen(self, event_id='sealed-1'):
        case = self.hr.intake_sealed('hr', self.report(event_id)).data['case_id']
        self.assertEqual(self.hr.advance_case('hr', case, 'screen-'+event_id,
                                              'SCREENED').status, Status.OK)
        return case

    def verified(self, event_id='ev-verified', kind='CORROBORATED'):
        return EvidenceRef(event_id, 'artifact://build-1', 'sha-1', E,
                           'investigator', kind)

    def investigating(self):
        case = self.intake_screen()
        self.assertEqual(self.hr.triage('hr', case, 'triage-1', 'CONDUCT',
                                        self.verified()).data['route'], 'CONDUCT')
        self.assertEqual(self.hr.assign_investigator('hr', case, 'assign-1',
                      'investigator').status, Status.OK)
        self.assertEqual(self.hr.advance_case('hr', case, 'start-1',
                      'INVESTIGATING').status, Status.OK)
        return case

    def ready_case(self):
        self.policy()
        case = self.investigating()
        self.assertEqual(self.hr.record_evidence('investigator', case,
                      self.verified()).status, Status.OK)
        self.assertEqual(self.hr.advance_case('hr', case, 'ready-1',
                      'EVIDENCE_READY').status, Status.OK)
        self.assertEqual(self.hr.propose_finding('investigator', case, 'find-1',
                      'CLAUSE-1', E).status, Status.OK)
        self.assertEqual(self.hr.advance_case('hr', case, 'draft-1',
                      'FINDINGS_DRAFT').status, Status.OK)
        self.assertEqual(self.hr.assign_reviewer('hr', case, 'reviewer-1',
                      'reviewer').status, Status.OK)
        self.assertEqual(self.hr.advance_case('hr', case, 'review-1',
                      'SECOND_REVIEW').status, Status.OK)
        self.assertEqual(self.hr.record_second_review('reviewer', case, 'review-outcome-1',
                      'CONFIRM', F).status, Status.OK)
        return case

    def notice(self, **overrides):
        if self.hr._policy()[1] is None:
            self.policy()
        payload = dict(notice_id='notice-1', issuer_id='oversight-fixture',
                       worker_id='w-1', action='PAUSE_NEW_ASSIGNMENTS',
                       assignment_epoch=4, expires_at=1200.0,
                       policy_revision=1, owner_decision_ref='fixture-owner-grant')
        payload.update(overrides)
        return {'payload': payload, 'signature_b64': 'fixture-marker-only'}

    def test_default_dependency_absence_denies_all_privileged_effects(self):
        empty = build_component({})
        self.assertEqual(empty.intake_sealed('hr', self.report()).status, Status.DENIED)
        self.assertEqual(empty.set_policy('hr', 'p', 0, D, 'x').status, Status.DENIED)
        self.assertEqual(empty.apply_signed_notice('hr', self.notice()).status, Status.DENIED)
        self.assertEqual(empty.public_status().data['private_access'], 'NOT_CONNECTED')

    def test_missing_custody_blocks_sealed_intake(self):
        self.hr = build_component({k: v for k, v in self.deps.items() if k != 'custody'})
        self.assertEqual(self.hr.intake_sealed('hr', self.report()).status,
                         Status.BLOCKED_PRIVATE_ACCESS)

    def test_sealed_intake_idempotent_same_case_and_rejects_changed_digest(self):
        one = self.hr.intake_sealed('hr', self.report())
        two = self.hr.intake_sealed('hr', self.report())
        self.assertRegex(one.data['case_id'], r'^CASE-[a-f0-9]{32}$')
        self.assertEqual(two.status, Status.DUPLICATE)
        self.assertEqual(one.data['case_id'], two.data['case_id'])
        self.assertEqual(self.hr.intake_sealed('hr', replace(self.report(), ciphertext_sha256=E)).status,
                         Status.CONFLICT)

    def test_no_plaintext_in_public_status_or_normal_status(self):
        case = self.hr.intake_sealed('hr', self.report()).data['case_id']
        self.assertNotIn('reporter', str(self.hr.case_status('hr', case)))
        self.assertNotIn('sealed://', str(self.hr.case_status('hr', case)))
        self.assertNotIn(case, str(self.hr.public_status()))
        self.assertEqual(self.hr.case_status('anonymous', case).status, Status.DENIED)

    def test_scoped_case_access_is_denied_independently(self):
        case = self.hr.intake_sealed('hr', self.report()).data['case_id']
        self.access.blocked.add(('hr', 'case_read', case))
        self.assertEqual(self.hr.case_status('hr', case).code, 'CASE_ACCESS_DENIED')
        self.assertEqual(self.hr.audit_case('outsider', case).status, Status.DENIED)

    def test_personality_dispute_not_conduct_without_verified_trigger(self):
        case = self.intake_screen()
        route = self.hr.triage('hr', case, 'route-1', 'CONDUCT')
        self.assertEqual(route.data['route'], 'WORK_DISPUTE')

    def test_verified_conduct_trigger_requires_provenance(self):
        case = self.intake_screen()
        self.evidence.available = False
        self.assertEqual(self.hr.triage('hr', case, 'route-1', 'SECURITY',
                                       self.verified()).data['route'], 'WORK_DISPUTE')

    def test_subject_reporter_or_implicated_cannot_investigate(self):
        case = self.intake_screen()
        self.hr.triage('hr', case, 'triage-1', 'WORK_DISPUTE')
        for candidate in ('w-1', 'reporter', 'blocked'):
            self.assertEqual(self.hr.assign_investigator('hr', case,
                             'assign-'+candidate, candidate, ('blocked',)).status,
                             Status.BLOCKED_INDEPENDENCE)

    def test_no_investigator_or_case_key_blocks_investigation(self):
        case = self.intake_screen()
        self.hr.triage('hr', case, 'triage-1', 'WORK_DISPUTE')
        self.assertEqual(self.hr.advance_case('hr', case, 'start', 'INVESTIGATING').status,
                         Status.BLOCKED_INDEPENDENCE)
        self.custody.available = False
        self.assertEqual(self.hr.assign_investigator('hr', case, 'assign',
                             'investigator').status, Status.DENIED)

    def test_illegal_case_jump_rejected(self):
        case = self.hr.intake_sealed('hr', self.report()).data['case_id']
        self.assertEqual(self.hr.advance_case('hr', case, 'jump', 'CLOSED').status, Status.DENIED)
        self.assertEqual(self.hr.advance_case('hr', case, 'spoof-appeal', 'APPEALED').status,
                         Status.DENIED)
        self.assertEqual(self.hr.case_status('hr', case).data['state'], 'SEALED_RECEIVED')

    def test_unsupported_evidence_cannot_promote_to_fact(self):
        case = self.investigating()
        ev = self.verified(kind='CLAIMED')
        self.assertEqual(self.hr.record_evidence('investigator', case, ev).status, Status.OK)
        self.assertEqual(self.hr.advance_case('hr', case, 'ready', 'EVIDENCE_READY').status,
                         Status.WAITING_EVIDENCE)
        self.assertEqual(self.hr.case_status('hr', case).data['evidence_count'], 1)

    def test_unverified_observation_denied(self):
        case = self.investigating()
        self.evidence.available = False
        self.assertEqual(self.hr.record_evidence('investigator', case,
                         self.verified(kind='OBSERVED')).status, Status.WAITING_EVIDENCE)

    def test_observed_is_insufficient_for_adverse_finding(self):
        self.policy()
        case = self.investigating()
        self.hr.record_evidence('investigator', case, self.verified(kind='OBSERVED'))
        self.hr.advance_case('hr', case, 'ready', 'EVIDENCE_READY')
        self.assertEqual(self.hr.propose_finding('investigator', case,
                         'finding', 'clause', E).status, Status.WAITING_EVIDENCE)

    def test_second_reviewer_must_be_distinct_and_independent(self):
        self.policy()
        case = self.investigating()
        self.hr.record_evidence('investigator', case, self.verified())
        self.hr.advance_case('hr', case, 'ready', 'EVIDENCE_READY')
        self.hr.propose_finding('investigator', case, 'finding', 'clause', E)
        self.hr.advance_case('hr', case, 'draft', 'FINDINGS_DRAFT')
        self.assertEqual(self.hr.assign_reviewer('hr', case, 'review-1',
                         'investigator').status, Status.BLOCKED_INDEPENDENCE)
        self.assertEqual(self.hr.advance_case('hr', case, 'review',
                         'SECOND_REVIEW').status, Status.BLOCKED_INDEPENDENCE)

    def test_owner_disposition_required(self):
        case = self.ready_case()
        self.assertEqual(self.hr.advance_case('hr', case, 'owner',
                         'OWNER_DISPOSITION', 'bad').status, Status.OWNER_PENDING)
        self.assertEqual(self.hr.advance_case('hr', case, 'owner',
                         'OWNER_DISPOSITION', 'fixture-owner-grant').status, Status.OK)
        self.assertEqual(self.hr.advance_case('hr', case, 'close', 'CLOSED').status, Status.OK)

    def test_independent_appeal_append_only_and_deduplicated(self):
        case = self.ready_case()
        self.hr.advance_case('hr', case, 'owner', 'OWNER_DISPOSITION', 'fixture-owner-grant')
        self.hr.advance_case('hr', case, 'close', 'CLOSED')
        events_before = len(self.hr.audit_case('hr', case).data['events'])
        self.assertEqual(self.hr.appeal('hr', case, 'ap-0', 'investigator', F,
                         'OVERTURN').status, Status.BLOCKED_INDEPENDENCE)
        first = self.hr.appeal('hr', case, 'ap-1', 'appellate', F, 'OVERTURN')
        self.assertEqual(first.status, Status.OK)
        # Must remain idempotent even after APPEALED state.
        self.assertEqual(self.hr.appeal('hr', case, 'ap-1', 'appellate', F,
                                       'OVERTURN').status, Status.DUPLICATE)
        self.assertEqual(self.hr.case_status('hr', case).data['appeal_count'], 1)
        self.assertEqual(len(self.hr.audit_case('hr', case).data['events']), events_before + 1)

    def test_policy_owner_cas_and_duplicate_safe(self):
        self.assertEqual(self.hr.set_policy('hr', 'policy-1', 0, D, 'bad').status, Status.OWNER_PENDING)
        self.policy()
        self.assertEqual(self.hr.set_policy('hr', 'policy-1', 0, D,
                         'fixture-owner-grant').status, Status.DUPLICATE)
        self.assertEqual(self.hr.set_policy('hr', 'policy-2', 0, E,
                         'fixture-owner-grant').status, Status.CONFLICT)
        self.assertEqual(self.hr.set_policy('hr', 'policy-2', 1, E,
                         'fixture-owner-grant').status, Status.OK)
        self.assertEqual(len(self.journal.audit('policy', 'current')), 2)

    def test_joiner_mover_leaver_requires_owner_and_never_changes_roster(self):
        self.policy()
        for i, kind in enumerate(('JOINER', 'MOVER', 'LEAVER', 'REINSTATE', 'RESTRICT')):
            request_id = 'req-'+str(i)
            initial = self.hr.request_workforce('hr', request_id, 'w-1', kind,
                         'role-X', 'department-X', 4, D)
            self.assertEqual(initial.status, Status.OK)
            self.assertEqual(self.hr.approve_workforce('hr', 'approval-'+str(i),
                         request_id, 'bad', 4).status, Status.OWNER_PENDING)
            self.assertEqual(self.hr.approve_workforce('hr', 'approval-'+str(i),
                         request_id, 'fixture-owner-grant', 3).status, Status.DENIED)
            self.assertEqual(self.hr.approve_workforce('hr', 'approval-'+str(i),
                         request_id, 'fixture-owner-grant', 4).status, Status.OK)
            self.assertEqual(self.hr.workforce_status('hr', request_id).data['studio_effect'],
                             'NOT_APPLIED')
        self.assertEqual(self.assignment.epoch['w-1'], 4)

    def test_unverified_competence_and_small_samples_denied(self):
        self.assertEqual(self.hr.review_competency('hr', 'w-1', 'review-1',
                         'test-role', 'period-1', 1, (('artifact://a', D),), (), 4).status,
                         Status.WAITING_EVIDENCE)
        self.evidence.available = False
        self.assertEqual(self.hr.review_competency('hr', 'w-1', 'review-2',
                         'test-role', 'period-1', 3, (('artifact://a', D),), (), 4).status,
                         Status.WAITING_EVIDENCE)

    def test_competency_and_persona_recommendations_no_privilege(self):
        self.policy()
        review = self.hr.review_competency('hr', 'w-1', 'competency-1',
                    'role-x', 'period-1', 3, (('artifact://accepted', D), ('artifact://accepted-2', F)),
                    (('artifact://rework', E),), 4)
        self.assertEqual(review.status, Status.OK)
        self.assertEqual(review.data['review']['possible_action'], 'COACH')
        p = self.hr.propose_persona_revision('hr', 'persona-1', 'w-1', 3,
                    D, 'directness', E, F, 'reviewer', 'artifact://persona', 'period-1')
        self.assertEqual(p.status, Status.OK)
        self.assertEqual(p.data['persona_review']['permissions_effect'], 'NONE')
        self.assertEqual(self.hr.propose_persona_revision('hr', 'persona-2', 'w-1',
                    3, D, 'grant_tool_access', E, F, 'reviewer', 'artifact://persona', 'period-1').status, Status.DENIED)

    def test_versioned_recommendation_is_never_privileged_effect(self):
        self.policy()
        result = self.hr.recommend('hr', 'rec-1', 'w-1', 'REQUEST_TEMPORARY_PAUSE',
                                   'SECURITY', D, 4, evidence_source_ref='artifact://security',
                                   evidence_source_revision='period-1')
        self.assertEqual(result.status, Status.OK)
        rec = result.data['recommendation']
        self.assertEqual(rec['version'], 'NOMAD/HR/RECOMMENDATION/1')
        self.assertEqual(rec['effect_status'], 'OWNER_PENDING')
        self.assertEqual(self.enforcement.calls, 0)

    def test_notice_missing_port_wrong_transport_unsigned_expired_and_replay(self):
        no_port = build_component({k:v for k,v in self.deps.items() if k!='signed_notice'})
        self.assertEqual(no_port.apply_signed_notice('bridge', self.notice()).status,
                         Status.UNSUPPORTED)
        self.assertEqual(self.hr.apply_signed_notice('bridge',
                         {'signature_ref': 'signature', 'payload': {}}).status, Status.DENIED)
        forged = self.notice()
        forged['signature_b64'] = 'fake'
        self.assertEqual(self.hr.apply_signed_notice('bridge', forged).status, Status.DENIED)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(expires_at=990)).status,
                         Status.DENIED)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice()).status, Status.OK)
        self.assertEqual(self.enforcement.calls, 1)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice()).code,
                         'NOTICE_REPLAY_REJECTED')
        self.assertEqual(self.enforcement.calls, 1)

    def test_notice_epoch_owner_and_action_denials_precede_effect(self):
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(
                         action='PERMANENT_DELETE')).status, Status.DENIED)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(
                         assignment_epoch=3)).status, Status.DENIED)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(
                         owner_decision_ref='worker-message')).status, Status.OWNER_PENDING)
        self.assertEqual(self.enforcement.calls, 0)

    def test_notice_unknown_result_does_not_retry(self):
        self.enforcement.timeout = True
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice()).status,
                         Status.PENDING_UNKNOWN)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice()).status, Status.DENIED)
        self.assertEqual(self.enforcement.calls, 1)

    def test_archive_manifest_hash_negative_no_delete(self):
        case = self.hr.intake_sealed('hr', self.report()).data['case_id']
        done = self.hr.prepare_archive('hr', case, 'archive-1', b'ciphertext-only',
                                        'LEGAL_HOLD', 'sha-1')
        self.assertEqual(done.status, Status.OK)
        manifest = done.data['manifest']
        self.assertFalse(manifest['deletion_approved'])
        self.assertEqual(self.hr.verify_sealed_archive(manifest, b'ciphertext-only').status,
                         Status.OK)
        self.assertEqual(self.hr.verify_sealed_archive(manifest, b'tampered').status,
                         Status.DENIED)

    def test_archive_without_external_attester_fails_closed(self):
        case = self.hr.intake_sealed('hr', self.report()).data['case_id']
        self.archive.valid = False
        self.assertEqual(self.hr.prepare_archive('hr', case, 'archive-1', b'cipher',
                         'STANDARD', 'sha-1').status, Status.NOT_CONNECTED)

    def test_no_external_effect_on_default_constructor_query_and_dry_request(self):
        hr = build_component({})
        hr.public_status()
        hr.capacity_summary(500, 800)
        hr.case_status('worker', 'CASE-'+'f'*32)
        self.assertEqual(self.enforcement.calls, 0)

    def test_synthetic_500_profiles_800_requests_never_prove_live_chat_scale(self):
        self.policy()
        for i in range(800):
            worker = f'worker-{i % 500:03d}'
            r = self.hr.request_workforce('hr', 'load-'+str(i), worker,
                    'MOVER', 'role-x', 'dept-y', i // 500, D)
            self.assertEqual(r.status, Status.OK)
        counts = self.hr.capacity_summary(500, 800)
        self.assertEqual(counts.data['synthetic_profiles'], 500)
        self.assertEqual(counts.data['synthetic_events'], 800)
        self.assertEqual(counts.data['real_chat_capacity'], 'NOT_MEASURED')
        self.assertEqual(len(self.journal.audit('workforce', 'load-799')), 1)

    def test_journal_receipt_collision_and_cas(self):
        self.assertTrue(self.journal.commit('x','k',0,{'a':1},'evt',D,{'ok':1}))
        self.assertFalse(self.journal.commit('x','k',0,{'a':1},'evt',D,{'ok':1}))
        with self.assertRaises(Conflict):
            self.journal.receipt('evt',E)
        with self.assertRaises(Conflict):
            self.journal.commit('x','k',0,{'a':2},'new',E,{})

    def test_sqlite_restart_preserves_idempotency_cas_and_append_only_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'private-metadata.sqlite')
            j = SQLiteJournal(path)
            self.assertTrue(j.commit('policy','current',0,{'revision':1},'id-1',D,{'r':1}))
            j.close()
            j2 = SQLiteJournal(path)
            self.assertEqual(j2.get('policy','current')[0], 1)
            self.assertEqual(j2.receipt('id-1',D).value['r'], 1)
            self.assertFalse(j2.commit('policy','current',0,{},'id-1',D,{}))
            self.assertEqual(len(j2.audit('policy','current')), 1)
            j2.close()

    def test_project_neutral_source_has_no_forbidden_import_or_external_writer(self):
        srcdir = Path(__file__).resolve().parents[2] / 'oversight_hr'
        forbidden = {'godot', 'fmod', 'github', 'googleapiclient', 'requests',
                     'subprocess', 'urllib', 'socket', 'nomad_studio', 'gameproduction'}
        for source in srcdir.glob('*.py'):
            tree = ast.parse(source.read_text())
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports += [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module.split('.')[0])
            self.assertTrue(forbidden.isdisjoint(imports), (source, imports))
        self.assertFalse((srcdir / 'gameproduction').exists())


    def test_concurrent_duplicate_sealed_intake_returns_one_persisted_case(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.hr.intake_sealed('hr', self.report()), range(32)))
        self.assertEqual(len({r.data['case_id'] for r in results}), 1)
        self.assertEqual(sum(r.status == Status.OK for r in results), 1)
        self.assertEqual(len(self.journal.audit('cases', results[0].data['case_id'])), 1)

    def test_repeated_case_transition_and_evidence_ids_are_idempotent(self):
        case = self.intake_screen()
        self.assertEqual(self.hr.advance_case('hr', case, 'screen-sealed-1',
                         'SCREENED').status, Status.DUPLICATE)
        self.hr.triage('hr', case, 'triage-1', 'WORK_DISPUTE')
        self.assertEqual(self.hr.triage('hr', case, 'triage-1', 'WORK_DISPUTE').status,
                         Status.DUPLICATE)
        self.hr.assign_investigator('hr', case, 'assign-1', 'investigator')
        self.hr.advance_case('hr', case, 'start', 'INVESTIGATING')
        ev = self.verified()
        self.hr.record_evidence('investigator', case, ev)
        self.hr.advance_case('hr', case, 'ready', 'EVIDENCE_READY')
        self.assertEqual(self.hr.record_evidence('investigator', case, ev).status,
                         Status.DUPLICATE)

    def test_appeal_cannot_close_without_verified_owner_ratification(self):
        case = self.ready_case()
        self.hr.advance_case('hr', case, 'owner', 'OWNER_DISPOSITION', 'fixture-owner-grant')
        self.hr.advance_case('hr', case, 'close', 'CLOSED')
        self.hr.appeal('hr', case, 'appeal-1', 'appellate', F, 'OVERTURN')
        self.assertEqual(self.hr.advance_case('hr', case, 'bad-close', 'CLOSED').status,
                         Status.DENIED)
        self.assertEqual(self.hr.resolve_appeal('hr', case, 'resolution-1', 'appeal-1',
                         'fake-owner-text').status, Status.OWNER_PENDING)
        answer = self.hr.resolve_appeal('hr', case, 'resolution-1', 'appeal-1',
                         'fixture-owner-grant')
        self.assertEqual(answer.status, Status.OK)
        self.assertEqual(answer.data['studio_effect'], 'NOT_APPLIED')
        self.assertEqual(self.hr.resolve_appeal('hr', case, 'resolution-1', 'appeal-1',
                         'fixture-owner-grant').status, Status.DUPLICATE)

    def test_reopen_requires_new_verified_evidence_and_preserves_finding_versions(self):
        case = self.ready_case()
        self.hr.advance_case('hr', case, 'owner', 'OWNER_DISPOSITION', 'fixture-owner-grant')
        self.hr.advance_case('hr', case, 'close', 'CLOSED')
        self.assertEqual(self.hr.advance_case('hr', case, 'raw-reopen',
                         'REOPENED').status, Status.DENIED)
        self.evidence.available = False
        self.assertEqual(self.hr.reopen_case('hr', case, 'reopen-1', self.verified(), 1).status,
                         Status.WAITING_EVIDENCE)
        self.evidence.available = True
        self.assertEqual(self.hr.reopen_case('hr', case, 'reopen-1', self.verified(), 1).status,
                         Status.OK)
        _, record = self.journal.get('cases', case)
        self.assertEqual(len(record['finding_versions']), 2)
        self.assertIsNone(record['finding'])
        self.assertEqual(record['disposition']['status'], 'OWNER_VERIFIED_RECORD_ONLY')

    def test_second_review_requires_verified_outcome(self):
        self.policy()
        case = self.investigating()
        self.hr.record_evidence('investigator', case, self.verified())
        self.hr.advance_case('hr', case, 'ready', 'EVIDENCE_READY')
        self.hr.propose_finding('investigator', case, 'find', 'CLAUSE', E)
        self.hr.advance_case('hr', case, 'draft', 'FINDINGS_DRAFT')
        self.hr.assign_reviewer('hr', case, 'reviewer-1', 'reviewer')
        self.hr.advance_case('hr', case, 'review', 'SECOND_REVIEW')
        self.assertEqual(self.hr.advance_case('hr', case, 'disposition',
                         'OWNER_DISPOSITION', 'fixture-owner-grant').status, Status.DENIED)
        self.assertEqual(self.hr.record_second_review('investigator', case,
                         'bad-review', 'CONFIRM', F).status, Status.DENIED)
        self.assertEqual(self.hr.record_second_review('reviewer', case,
                         'review-outcome', 'CONFIRM', F).status, Status.OK)
        self.assertEqual(self.hr.advance_case('hr', case, 'disposition',
                         'OWNER_DISPOSITION', 'fixture-owner-grant').status, Status.OK)

    def test_recommendations_and_persona_need_independently_verified_source(self):
        self.policy()
        self.assertEqual(self.hr.recommend('hr', 'rec-no-proof', 'w-1', 'COACH',
                         'CAPABILITY', D, 4).status, Status.WAITING_EVIDENCE)
        self.assertEqual(self.hr.propose_persona_revision('hr', 'persona-no-proof',
                         'w-1', 1, D, 'directness', E, F, 'reviewer').status,
                         Status.WAITING_EVIDENCE)

    def test_broken_ports_fail_closed_without_exception(self):
        class Broken:
            def __getattr__(self, name):
                def fail(*args): raise ConnectionError('disconnected')
                return fail
        defaults = dict(self.deps)
        defaults['access'] = Broken()
        defaults['custody'] = Broken()
        defaults['owner'] = Broken()
        self.assertEqual(build_component(defaults).intake_sealed('hr', self.report()).status,
                         Status.DENIED)
        defaults['access'] = self.access
        self.assertEqual(build_component(defaults).intake_sealed('hr', self.report()).status,
                         Status.BLOCKED_PRIVATE_ACCESS)
        self.assertEqual(build_component(defaults).set_policy('hr', 'p1', 0,
                         D, 'fixture-owner-grant').status, Status.OWNER_PENDING)

    def test_lease_epoch_required_for_scoped_fence_and_policy_staleness_denied(self):
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(
                         notice_id='fence-0', action='REQUEST_LEASE_FENCE')).status, Status.DENIED)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(
                         notice_id='fence-1', action='REQUEST_LEASE_FENCE',
                         job_id='job-1', lease_epoch=11)).status, Status.DENIED)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(
                         notice_id='fence-2', action='REQUEST_LEASE_FENCE',
                         job_id='job-1', lease_epoch=12)).status, Status.OK)
        self.assertEqual(self.hr.apply_signed_notice('bridge', self.notice(
                         notice_id='stale-policy', policy_revision=2)).status, Status.DENIED)

    def test_notice_can_reconcile_unknown_without_reapplying(self):
        msg = self.notice()
        self.enforcement.timeout = True
        self.enforcement.readback_ok = False
        self.assertEqual(self.hr.apply_signed_notice('bridge', msg).status,
                         Status.PENDING_UNKNOWN)
        self.assertEqual(self.hr.reconcile_notice('bridge', 'notice-1', 'reconcile-1').status,
                         Status.PENDING_UNKNOWN)
        self.assertEqual(self.enforcement.calls, 1)
        self.enforcement.readback_ok = True
        self.assertEqual(self.hr.reconcile_notice('bridge', 'notice-1', 'reconcile-1').status,
                         Status.OK)
        self.assertEqual(self.enforcement.calls, 1)
        _, rec = self.journal.get('notice_intents', 'notice-1')
        self.assertEqual(rec['outcome'], 'ENACTED_READBACK')

    def test_archive_bytes_are_never_persisted_to_hr_metadata_journal(self):
        case = self.hr.intake_sealed('hr', self.report()).data['case_id']
        original = b'PRIVATE_FAKE_ENCRYPTED_BYTES_only_for_test'
        result = self.hr.prepare_archive('hr', case, 'archive-1', original,
                                          'LEGAL_HOLD', 'rev-1')
        self.assertEqual(result.status, Status.OK)
        _, record = self.journal.get('archives', case + ':archive-1')
        self.assertNotIn(original.decode(), str(record))
        self.assertEqual(record['deletion'], 'FORBIDDEN')

    def test_policy_history_persists_previous_revision_metadata(self):
        self.policy()
        self.hr.set_policy('hr', 'policy-2', 1, E, 'fixture-owner-grant')
        _, policy = self.journal.get('policy', 'current')
        self.assertEqual(policy['revision'], 2)
        self.assertEqual(len(policy['history']), 1)
        self.assertEqual(policy['history'][0]['digest'], D)



if __name__ == '__main__':
    unittest.main()
