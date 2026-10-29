"""WG-AP isolated contract tests. All identities, receipts and decisions are mocks.

These tests do not constitute real confidential storage or signature validation.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import inspect
import unittest

from oversight_investigations import (
    Conflict, Denied, EvidenceSubmission, IntakeReceipt, IntegrityFailure, Invalid,
    Principal, SourceReceipt, Unsupported, build_component,
)
from oversight_investigations.store import InMemoryCaseStore

NOW = datetime(2026, 10, 29, 5, 0, tzinfo=timezone.utc)
SHA_A = 'a' * 64
SHA_B = 'b' * 64
SHA_C = 'c' * 64


class FakeClock:
    def __init__(self):
        self.value = NOW

    def now(self):
        return self.value


class FakeIdentity:
    def __init__(self):
        self.revoked = set()

    def resolve(self, credential):
        if credential in self.revoked or credential is None:
            return None
        return Principal(credential, 1, NOW + timedelta(days=90))


class FakeAuthorization:
    def __init__(self):
        self.denials = set()
        self.calls = []

    def allow(self, principal, operation, case_id, access_scope, now):
        self.calls.append((principal.identity, operation, case_id, access_scope))
        if (principal.identity, operation) in self.denials:
            return False
        if principal.identity == 'visitor':
            return False
        return True


class FakeSealVerifier:
    def __init__(self):
        self.missing = False
        self.calls = []

    def verify_seal(self, ciphertext_ref, manifest_sha256, principal, now):
        self.calls.append((ciphertext_ref, manifest_sha256))
        return not self.missing and manifest_sha256 == SHA_B


class FakeIntakeVerifier:
    def __init__(self):
        self.expire = False
        self.mismatch = False

    def verify_intake(self, **kwargs):
        return IntakeReceipt(
            intake_id=kwargs['intake_id'],
            ciphertext_ref=kwargs['ciphertext_ref'],
            manifest_sha256=kwargs['manifest_sha256'],
            subject_worker_id='forged-subject' if self.mismatch else kwargs['subject_worker_id'],
            reporter_id=kwargs['reporter_id'],
            policy_revision=kwargs['policy_revision'],
            source_occurred_at=NOW - timedelta(days=1),
            verifier_id='trusted-intake-mock',
            valid_until=NOW - timedelta(seconds=1) if self.expire else NOW + timedelta(days=1),
        )


class FakeSourceVerifier:
    def __init__(self):
        self.classes = {}
        self.expire = False
        self.mismatch = False
        self.calls = 0

    def verify_source(self, submitted, principal, now):
        self.calls += 1
        return SourceReceipt(
            evidence_id=submitted.evidence_id,
            content_sha256=SHA_C if self.mismatch else submitted.content_sha256,
            source_ref=submitted.source_ref,
            source_revision=submitted.source_revision,
            source_event_id=submitted.source_event_id,
            source_occurred_at=submitted.source_occurred_at,
            origin_ref=submitted.origin_ref,
            verifier_id='trusted-source-mock',
            author_id=submitted.author_id,
            access_scope=submitted.access_scope,
            classification=self.classes.get(submitted.evidence_id, 'CLAIMED'),
            valid_until=NOW - timedelta(seconds=1) if self.expire else NOW + timedelta(days=1),
        )


class FakeEligibility:
    def __init__(self):
        self.denied = set()
        self.calls = []

    def eligible(self, case_id, candidate_id, role, now):
        self.calls.append((case_id, candidate_id, role))
        return (candidate_id, role) not in self.denied


class FakeOwnerDecision:
    def __init__(self):
        self.authorized = False
        self.calls = []

    def verify_decision(self, **kwargs):
        self.calls.append(kwargs)
        return self.authorized and kwargs['case_chain_sha256'] != '0' * 64


class TestInvestigations(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.identity = FakeIdentity()
        self.auth = FakeAuthorization()
        self.seal = FakeSealVerifier()
        self.source = FakeSourceVerifier()
        self.intake = FakeIntakeVerifier()
        self.owner = FakeOwnerDecision()
        self.eligibility = FakeEligibility()
        self.store = InMemoryCaseStore()
        self.deps = dict(identity=self.identity, authorization=self.auth,
                         sealed_custody=self.seal, source_verification=self.source,
                         intake_verification=self.intake,
                         owner_decision=self.owner, clock=self.clock,
                         investigator_eligibility=self.eligibility,
                         case_store=self.store)
        self.svc = build_component(self.deps)

    def opened(self):
        return self.svc.open_case('intake', intake_id='intake-1',
                                  ciphertext_ref='sealed-intake',
                                  manifest_sha256=SHA_B,
                                  subject_worker_id='subject', reporter_id='reporter')['case_id']

    def investigating(self):
        case_id = self.opened()
        self.svc.transition('triage', case_id, target='SCREENED', request_id='screen')
        self.svc.transition('triage', case_id, target='TRIAGED', request_id='triage')
        self.svc.assign_investigator('lead', case_id, investigator_id='investigator',
                                      request_id='assign')
        self.svc.transition('triage', case_id, target='INVESTIGATING', request_id='investigate')
        return case_id

    def submitted(self, ident='item-1', origin='origin-1', author='author-1',
                  counter_to=None, corrects=None):
        return EvidenceSubmission(
            evidence_id=ident, content_sha256=SHA_A,
            manifest_sha256=SHA_B, ciphertext_ref='sealed-' + ident,
            source_ref='source-' + ident, source_revision='rev-1',
            source_event_id='event-' + ident,
            source_occurred_at=NOW - timedelta(hours=2),
            origin_ref=origin, access_scope='case-evidence', author_id=author,
            counter_to=counter_to, corrects=corrects)

    def record(self, case_id, ident='item-1', klass='CLAIMED', origin='origin-1',
               author='author-1', counter_to=None, corrects=None):
        self.source.classes[ident] = klass
        return self.svc.record_evidence('investigator', case_id,
                                        submission=self.submitted(ident, origin, author,
                                                                  counter_to, corrects),
                                        request_id='record-' + ident)

    def finding(self, case_id, conclusion='NOT_SUPPORTED', facts=(), inferences=(),
                allegations=(), counters=()):
        return self.svc.propose_finding(
            'investigator', case_id, conclusion=conclusion,
            policy_clause='POLICY-1', policy_revision=1,
            fact_refs=facts, inference_refs=inferences,
            allegation_refs=allegations, counter_refs=counters,
            sealed_reason_sha256=SHA_C, request_id='finding-1')

    def closed(self):
        case_id = self.investigating()
        self.record(case_id)
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        self.finding(case_id, allegations=('item-1',))
        self.svc.transition('lead', case_id, target='FINDINGS_DRAFT', request_id='draft')
        self.svc.second_review('reviewer', case_id, accepted=True, request_id='second')
        self.svc.transition('lead', case_id, target='SECOND_REVIEW', request_id='second-phase')
        self.svc.transition('lead', case_id, target='OWNER_DISPOSITION', request_id='owner-phase')
        self.owner.authorized = True
        self.svc.owner_disposition('owner', case_id, decision_ref='owner-decision-1',
                                   disposition='NO_ACTION', request_id='decision')
        self.svc.transition('lead', case_id, target='CLOSED', request_id='close')
        return case_id

    def test_missing_ports_fail_closed(self):
        with self.assertRaises(Unsupported):
            build_component({}).open_case('claimed-owner', intake_id='i',
                ciphertext_ref='abc', manifest_sha256=SHA_B,
                subject_worker_id=None, reporter_id=None)

    def test_open_case_sealed_and_redacted(self):
        case_id = self.opened()
        self.assertRegex(case_id, r'^CASE-[0-9a-f]{32}$')
        result = self.svc.query_status('lead', case_id)
        self.assertEqual(result['state'], 'SEALED_RECEIVED')
        self.assertNotIn('reporter', repr(result))
        self.assertNotIn('sealed-intake', repr(result))
        self.assertEqual(self.svc.public_metrics(), {'total_cases': 1})

    def test_no_plaintext_seal_fallback(self):
        self.seal.missing = True
        with self.assertRaises(Denied):
            self.opened()
        self.assertEqual(self.svc.public_metrics()['total_cases'], 0)

    def test_unbound_or_expired_intake_attestation_denied(self):
        self.intake.mismatch = True
        with self.assertRaises(Denied):
            self.opened()
        self.intake.mismatch = False
        self.intake.expire = True
        with self.assertRaises(Denied):
            self.opened()
        self.assertEqual(self.svc.public_metrics(), {'total_cases': 0})

    def test_duplicate_intake_returns_original(self):
        original = self.opened()
        self.assertEqual(self.opened(), original)
        self.assertEqual(self.svc.public_metrics()['total_cases'], 1)

    def test_conflicting_duplicate_intake_rejected(self):
        self.opened()
        with self.assertRaises(Conflict):
            self.svc.open_case('intake', intake_id='intake-1',
                ciphertext_ref='sealed-intake', manifest_sha256=SHA_B,
                subject_worker_id='someone-else', reporter_id='reporter')

    def test_bad_digest_rejected_at_intake(self):
        with self.assertRaises(Denied):
            self.svc.open_case('intake', intake_id='intake-1',
                ciphertext_ref='sealed-intake', manifest_sha256=SHA_A,
                subject_worker_id='subject', reporter_id='reporter')

    def test_reject_bad_transition(self):
        case_id = self.opened()
        with self.assertRaises(Denied):
            self.svc.transition('lead', case_id, target='OWNER_DISPOSITION', request_id='jump')

    def test_investigation_requires_assigned_worker(self):
        case_id = self.opened()
        self.svc.transition('lead', case_id, target='SCREENED', request_id='s')
        self.svc.transition('lead', case_id, target='TRIAGED', request_id='t')
        with self.assertRaises(Denied):
            self.svc.transition('lead', case_id, target='INVESTIGATING', request_id='i')

    def test_self_subject_reporter_assignment_denied(self):
        case_id = self.opened()
        self.svc.transition('lead', case_id, target='SCREENED', request_id='s')
        self.svc.transition('lead', case_id, target='TRIAGED', request_id='t')
        for identity in ('subject', 'reporter'):
            with self.subTest(identity=identity), self.assertRaises(Denied):
                self.svc.assign_investigator('lead', case_id,
                    investigator_id=identity, request_id='bind-' + identity)

    def test_cannot_assign_evidence_author_on_reopening(self):
        case_id = self.investigating()
        self.record(case_id, author='disputed-author')
        self.svc.mark_blocked('lead', case_id, target='BLOCKED_INDEPENDENCE',
                               request_id='block')
        with self.assertRaises(Denied):
            self.svc.assign_investigator('lead', case_id,
                investigator_id='disputed-author', request_id='bad-assignment')

    def test_claimed_alone_never_supported(self):
        case_id = self.investigating()
        self.record(case_id)
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        with self.assertRaises(Denied):
            self.finding(case_id, 'SUPPORTED', allegations=('item-1',))
        self.finding(case_id, 'NOT_SUPPORTED', allegations=('item-1',))
        self.assertEqual(self.store.read(case_id).finding.sufficiency, 'INSUFFICIENT')

    def test_not_supported_explicit_no_evidence_dismissal(self):
        case_id = self.investigating()
        result = self.svc.dismiss_no_evidence('lead', case_id,
                    policy_clause='POLICY-1', request_id='dismiss')
        self.assertEqual(result['state'], 'DISMISSED_NO_EVIDENCE')
        self.assertEqual(self.store.read(case_id).events[-1].kind, 'NOT_SUPPORTED')

    def test_two_independent_observations_sufficient(self):
        case_id = self.investigating()
        self.record(case_id, ident='e1', klass='OBSERVED', origin='source-one')
        self.record(case_id, ident='e2', klass='OBSERVED', origin='source-two')
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        self.finding(case_id, 'SUPPORTED', facts=('e1', 'e2'))
        self.assertEqual(self.store.read(case_id).finding.sufficiency, 'SUFFICIENT')

    def test_same_origin_not_independent(self):
        case_id = self.investigating()
        self.record(case_id, ident='e1', klass='OBSERVED', origin='same')
        self.record(case_id, ident='e2', klass='OBSERVED', origin='same')
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        with self.assertRaises(Denied):
            self.finding(case_id, 'SUPPORTED', facts=('e1', 'e2'))

    def test_corroborated_fact_sufficient(self):
        case_id = self.investigating()
        self.record(case_id, klass='CORROBORATED')
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        self.finding(case_id, 'SUPPORTED', facts=('item-1',))

    def test_classification_must_match_reference(self):
        case_id = self.investigating()
        self.record(case_id, klass='INFERRED')
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        with self.assertRaises(Denied):
            self.finding(case_id, 'SUPPORTED', facts=('item-1',))
        self.finding(case_id, 'NOT_SUPPORTED', inferences=('item-1',))

    def test_counterevidence_blocks_supported_finding(self):
        case_id = self.investigating()
        self.record(case_id, ident='proof', klass='CORROBORATED')
        self.record(case_id, ident='counter', klass='OBSERVED', counter_to='proof')
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        with self.assertRaises(Denied):
            self.finding(case_id, 'SUPPORTED', facts=('proof',), counters=('counter',))
        self.finding(case_id, 'NOT_SUPPORTED', facts=('proof',), counters=('counter',))

    def test_corrections_append_without_rewriting_original(self):
        case_id = self.investigating()
        self.record(case_id, ident='old', klass='OBSERVED')
        before = self.store.read(case_id).evidence['old']
        before_chain = self.store.read(case_id).custody[1].record_sha256
        self.record(case_id, ident='new', klass='OBSERVED', corrects='old')
        current = self.store.read(case_id)
        self.assertEqual(current.evidence['old'], before)
        self.assertEqual(current.custody[1].record_sha256, before_chain)
        self.assertEqual(current.evidence['new'].corrects, 'old')

    def test_expired_source_attestation_denied_without_event(self):
        case_id = self.investigating()
        old_count = len(self.store.read(case_id).events)
        self.source.expire = True
        with self.assertRaises(Denied):
            self.record(case_id)
        self.assertEqual(len(self.store.read(case_id).events), old_count)

    def test_tampered_source_hash_denied(self):
        case_id = self.investigating()
        self.source.mismatch = True
        with self.assertRaises(Denied):
            self.record(case_id)
        self.assertEqual(self.store.read(case_id).evidence, {})

    def test_custody_access_append_only(self):
        case_id = self.investigating()
        self.record(case_id)
        original = tuple(self.store.read(case_id).custody)
        self.svc.record_custody('custodian', case_id, evidence_id='item-1',
                               operation='ACCESS', target_principal=None,
                               request_id='custody-access')
        after = self.store.read(case_id).custody
        self.assertEqual(tuple(after[:len(original)]), original)
        self.assertEqual(after[-1].kind, 'ACCESS')

    def test_tampered_local_chain_detected(self):
        case_id = self.investigating()
        with self.store._lock:
            first = self.store._cases[case_id].events[0]
            self.store._cases[case_id].events[0] = replace(first, metadata_sha256=SHA_A)
        with self.assertRaises(IntegrityFailure):
            self.svc.audit_integrity('auditor', case_id)

    def test_modified_recorded_manifest_detected(self):
        case_id = self.investigating()
        self.record(case_id)
        with self.store._lock:
            old = self.store._cases[case_id].evidence['item-1']
            self.store._cases[case_id].evidence['item-1'] = replace(
                old, manifest_sha256=SHA_C)
        with self.assertRaises(IntegrityFailure):
            self.svc.query_status('lead', case_id)

    def test_appeal_reviewer_requires_current_eligibility(self):
        case_id = self.closed()
        self.svc.appeal('subject', case_id, appeal_id='appeal-one',
                        new_evidence_refs=(), sealed_ground_sha256=SHA_C,
                        request_id='file-appeal')
        self.eligibility.denied.add(('candidate', 'APPEAL_REVIEWER'))
        with self.assertRaises(Denied):
            self.svc.assign_appeal_reviewer('lead', case_id,
                appeal_id='appeal-one', reviewer_id='candidate',
                request_id='try-candidate')

    def test_event_id_reused_with_different_intent_denied(self):
        case_id = self.opened()
        self.svc.transition('lead', case_id, target='SCREENED', request_id='one-id')
        with self.assertRaises(Conflict):
            self.svc.transition('lead', case_id, target='TRIAGED', request_id='one-id')

    def test_independent_second_review_denies_original_investigator(self):
        case_id = self.investigating()
        self.record(case_id)
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        self.finding(case_id, allegations=('item-1',))
        self.svc.transition('lead', case_id, target='FINDINGS_DRAFT', request_id='draft')
        with self.assertRaises(Denied):
            self.svc.second_review('investigator', case_id, accepted=True, request_id='bad-review')
        self.svc.second_review('independent', case_id, accepted=True, request_id='good-review')
        self.assertEqual(self.store.read(case_id).second_reviewer_id, 'independent')

    def test_rejected_review_blocks_and_resumes(self):
        case_id = self.investigating()
        self.record(case_id)
        self.svc.transition('lead', case_id, target='EVIDENCE_READY', request_id='ready')
        self.finding(case_id, allegations=('item-1',))
        self.svc.transition('lead', case_id, target='FINDINGS_DRAFT', request_id='draft')
        self.svc.second_review('independent', case_id, accepted=False, request_id='reject')
        self.assertEqual(self.svc.query_status('lead', case_id)['state'], 'BLOCKED_INDEPENDENCE')
        self.svc.resume('lead', case_id, request_id='resume')
        self.assertEqual(self.svc.query_status('lead', case_id)['state'], 'FINDINGS_DRAFT')

    def test_owner_decision_cannot_be_forged(self):
        case_id = self.closed()
        self.assertEqual(self.svc.query_status('lead', case_id)['state'], 'CLOSED')
        self.assertEqual(self.owner.calls[0]['action'], 'NO_ACTION')
        self.assertEqual(self.svc.audit_integrity('auditor', case_id)['external_blob_integrity'],
                         'NOT_VERIFIED')

    def test_appeal_reviewer_must_be_independent(self):
        case_id = self.closed()
        self.svc.appeal('subject', case_id, appeal_id='appeal-one',
                        new_evidence_refs=(), sealed_ground_sha256=SHA_C,
                        request_id='file-appeal')
        for candidate in ('subject', 'reporter', 'investigator', 'reviewer', 'author-1'):
            with self.subTest(candidate=candidate), self.assertRaises(Denied):
                self.svc.assign_appeal_reviewer('lead', case_id,
                    appeal_id='appeal-one', reviewer_id=candidate,
                    request_id='bad-' + candidate)
        self.svc.assign_appeal_reviewer('lead', case_id, appeal_id='appeal-one',
                                        reviewer_id='appeal-independent', request_id='bind-appeal')

    def test_appeal_overturn_requires_owner_ratification(self):
        case_id = self.closed()
        self.svc.appeal('subject', case_id, appeal_id='appeal-one',
                        new_evidence_refs=(), sealed_ground_sha256=SHA_C,
                        request_id='file-appeal')
        self.source.classes['later'] = 'OBSERVED'
        self.svc.record_evidence('investigator', case_id,
              submission=self.submitted('later', origin='source-later'),
              request_id='later-evidence')
        self.svc.add_appeal_evidence('subject', case_id, appeal_id='appeal-one',
                                     evidence_id='later', request_id='appeal-attach')
        self.svc.assign_appeal_reviewer('lead', case_id, appeal_id='appeal-one',
                                        reviewer_id='appeal-independent', request_id='bind-appeal')
        self.svc.resolve_appeal('appeal-independent', case_id, appeal_id='appeal-one',
                                outcome='OVERTURN', policy_revision=2,
                                request_id='resolve-appeal')
        self.assertEqual(self.svc.query_status('lead', case_id)['state'], 'WAITING_OWNER')
        self.owner.authorized = False
        with self.assertRaises(Denied):
            self.svc.ratify_appeal('owner', case_id, appeal_id='appeal-one',
                                   decision_ref='ratification', request_id='ratify')
        self.owner.authorized = True
        self.svc.ratify_appeal('owner', case_id, appeal_id='appeal-one',
                               decision_ref='ratification', request_id='ratify')
        after = self.store.read(case_id)
        self.assertEqual(after.state, 'REOPENED')
        self.assertTrue(after.appeals['appeal-one'].ratified)
        self.assertEqual(after.policy_revision, 2)
        self.assertEqual(after.evidence['later'].source_revision, 'rev-1')
        self.assertTrue(any(ev.kind == 'FINDING_PROPOSED' for ev in after.events))

    def test_appeal_replay_one_effect(self):
        case_id = self.closed()
        kwargs = dict(appeal_id='appeal-one', new_evidence_refs=(),
                      sealed_ground_sha256=SHA_C, request_id='file')
        self.svc.appeal('subject', case_id, **kwargs)
        before = len(self.store.read(case_id).events)
        self.svc.appeal('subject', case_id, **kwargs)
        self.assertEqual(len(self.store.read(case_id).events), before)
        with self.assertRaises(Conflict):
            self.svc.appeal('subject', case_id, appeal_id='different',
                            new_evidence_refs=(), sealed_ground_sha256=SHA_C,
                            request_id='file')

    def test_revocation_applies_even_on_replay(self):
        case_id = self.opened()
        self.svc.transition('lead', case_id, target='SCREENED', request_id='screen')
        self.auth.denials.add(('lead', 'ADVANCE_CASE'))
        with self.assertRaises(Denied):
            self.svc.transition('lead', case_id, target='SCREENED', request_id='screen')

    def test_unauthorized_query_denied(self):
        case_id = self.opened()
        with self.assertRaises(Denied):
            self.svc.query_status('visitor', case_id)
        self.assertEqual(self.svc.public_metrics(), {'total_cases': 1})

    def test_scope_denial_prevents_custody_mutation(self):
        case_id = self.investigating()
        self.auth.denials.add(('investigator', 'WRITE_EVIDENCE'))
        with self.assertRaises(Denied):
            self.record(case_id)
        self.assertEqual(len(self.store.read(case_id).custody), 1)

    def test_eligibility_revocation_denies_assignment(self):
        case_id = self.opened()
        self.svc.transition('lead', case_id, target='SCREENED', request_id='s')
        self.svc.transition('lead', case_id, target='TRIAGED', request_id='t')
        self.eligibility.denied.add(('investigator', 'INVESTIGATOR'))
        with self.assertRaises(Denied):
            self.svc.assign_investigator('lead', case_id,
                investigator_id='investigator', request_id='assigned')
        self.assertIsNone(self.store.read(case_id).investigator_id)

    def test_missing_investigator_eligibility_port_denies(self):
        case_id = self.opened()
        self.svc.transition('lead', case_id, target='SCREENED', request_id='s')
        self.svc.transition('lead', case_id, target='TRIAGED', request_id='t')
        self.svc.eligibility = None
        with self.assertRaises(Unsupported):
            self.svc.assign_investigator('lead', case_id,
                investigator_id='investigator', request_id='assigned')

    def test_owner_notice_fails_closed_and_no_effects(self):
        self.assertTrue(self.svc._no_external_effects)
        with self.assertRaises(Unsupported):
            self.svc.request_signed_notice({'payload': {}, 'signature_b64': 'fake'})
        self.assertEqual(self.svc.public_metrics()['total_cases'], 0)

    def test_project_neutral_no_provider_or_game_imports(self):
        import oversight_investigations.api as api
        import oversight_investigations.model as model
        import oversight_investigations.store as store
        import ast
        for module in (api, model, store):
            tree = ast.parse(inspect.getsource(module))
            imports = [node.module or '' for node in ast.walk(tree)
                       if isinstance(node, ast.ImportFrom)]
            imports += [alias.name for node in ast.walk(tree)
                        if isinstance(node, ast.Import) for alias in node.names]
            for root in imports:
                self.assertNotIn(root.split('.')[0],
                   {'requests', 'socket', 'urllib', 'subprocess', 'github', 'google',
                    'godot', 'fmod', 'gameproduction', 'nomad_studio'})
        self.assertEqual(self.svc.public_metrics(), {'total_cases': 0})

    def test_unsupported_private_seal_port(self):
        deps = dict(self.deps)
        deps['sealed_custody'] = None
        with self.assertRaises(Unsupported):
            build_component(deps).open_case('intake', intake_id='new-intake',
                ciphertext_ref='sealed-intake', manifest_sha256=SHA_B,
                subject_worker_id='subject', reporter_id='reporter')

    def test_unsupported_source_verification_port(self):
        case_id = self.investigating()
        self.svc.source = None
        with self.assertRaises(Unsupported):
            self.record(case_id)

    def test_malformed_digest_rejected(self):
        case_id = self.investigating()
        bad = replace(self.submitted(), content_sha256='not-sha')
        with self.assertRaises(Invalid):
            self.svc.record_evidence('investigator', case_id,
                                     submission=bad, request_id='malformed')


if __name__ == '__main__':
    unittest.main()
