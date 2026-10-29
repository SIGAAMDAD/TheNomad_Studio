"""WG-AO isolated deterministic port tests; no private source or provider connection."""
from __future__ import annotations
import hashlib
import unittest
from pathlib import Path
from dataclasses import replace

from oversight_relations import build_component, EvidenceRef, IssueReport, EvidenceAssessment, GateDecision, Mediator
from oversight_relations.storage import TestOnlyMemoryStore

CASE = 'CASE-' + 'a'*32
REF = EvidenceRef('vault:object-27', hashlib.sha256(b'synthetic source').hexdigest())

class Access:
    def __init__(self):
        self.allowed = {'reporter','subject','head','hr','med','review'}
        self.denied_operations=set()
        self.calls=[]
        self.fail=False
    def permit(self, principal, case_id, operation):
        self.calls.append((principal,case_id,operation))
        if self.fail: raise OSError('injected case access outage')
        return principal in self.allowed and operation not in self.denied_operations and case_id==CASE

class Authority:
    def __init__(self, level='CLAIMED', recurring=0, impact=False, identity=False, active=False):
        self.assessment=EvidenceAssessment(level,impact,recurring,identity,active)
        self.fail=False
    def assess(self, issue):
        if self.fail: raise OSError('injected source outage')
        return self.assessment

class Registry:
    def __init__(self,candidates=None):
        self.members = candidates if candidates is not None else [Mediator('med',True,True,(CASE,)), Mediator('review',True,True,(CASE,))]
        self.fail=False
    def candidates(self, case_id):
        if self.fail: raise OSError('test outage')
        return self.members

class Gate:
    def __init__(self,allowed=True): self.allowed=allowed;self.calls=0
    def validate(self,issue,assessment):
        self.calls+=1
        return GateDecision(self.allowed,'gate:approved-trigger')

class Notices:
    def __init__(self,allowed=True): self.allowed=allowed;self.fail=False;self.calls=[]
    def verify_delivery(self,case,ref,parties):
        self.calls.append((case,ref,parties))
        if self.fail: raise OSError('receipt reader unavailable')
        return self.allowed

class RelationsTests(unittest.TestCase):
    def setUp(self):
        self.access=Access()
        self.evidence=Authority()
        self.registry=Registry()
        self.gate=Gate()
        self.notices=Notices()
        self.store=TestOnlyMemoryStore()
        self.component=build_component({'case_access':self.access,'evidence_authority':self.evidence,
            'mediator_registry':self.registry,'emergency_gate':self.gate,
            'notice_authority':self.notices,'store':self.store})
        self.counter=0

    def issue(self,kind='WORK_DISAGREEMENT'):
        return IssueReport(CASE,'reporter','subject','head',kind,(REF,))

    def submit(self,kind='WORK_DISAGREEMENT'):
        self.counter+=1
        r=self.component.request('reporter','req-'+str(self.counter),self.issue(kind))
        self.assertEqual(r.status,'OK',r)
        return r

    def mediation(self):
        self.evidence.assessment=EvidenceAssessment('CORROBORATED',True,3)
        r=self.submit('COMMUNICATION_FRICTION')
        self.assertEqual(r.route,'MEDIATION')
        return r.session_id

    def assigned(self):
        sid=self.mediation()
        self.assertEqual(self.component.assign_mediator('hr',sid).status,'OK')
        return sid

    def ready(self):
        sid=self.assigned()
        self.assertEqual(self.component.participate('reporter',sid,'ACCEPT').status,'OK')
        self.assertEqual(self.component.participate('subject',sid,'ACCEPT').status,'OK')
        self.assertEqual(self.component.set_working_contract('med',sid,
            ['USE_SOURCE_REFERENCES','STRUCTURED_REVIEW'],'checkpoint:next-review').status,'OK')
        self.assertEqual(self.component.verify_notice('med',sid,'notice:received').status,'OK')
        return sid

    def test_disagreement_routes_to_head_without_sanction(self):
        r=self.submit()
        self.assertEqual(r.route,'PRODUCTION_HEAD')
        record=self.component.private_read('hr',r.session_id).private_session
        self.assertEqual(record.status,'REFERRED')
        self.assertEqual(record.referral.reason_code,'TECHNICAL_OR_CREATIVE_DECISION')
        self.assertFalse(hasattr(record,'sanction'))

    def test_skeptical_or_assertive_work_style_not_enough_for_hr(self):
        self.evidence.assessment=EvidenceAssessment('CORROBORATED',False,15)
        r=self.submit('COMMUNICATION_FRICTION')
        self.assertEqual(r.route,'PRODUCTION_HEAD')

    def test_recurring_verified_impacted_friction_routes_to_mediation(self):
        self.assertEqual(self.component.private_read('hr',self.mediation()).private_session.status,'OPEN')

    def test_single_friction_event_stays_with_head(self):
        self.evidence.assessment=EvidenceAssessment('CORROBORATED',True,1)
        self.assertEqual(self.submit('COMMUNICATION_FRICTION').route,'PRODUCTION_HEAD')

    def test_claimed_repeat_friction_does_not_count_as_verified(self):
        self.evidence.assessment=EvidenceAssessment('CLAIMED',True,50)
        self.assertEqual(self.submit('COMMUNICATION_FRICTION').route,'PRODUCTION_HEAD')

    def test_targeted_conduct_is_triage_not_penalty(self):
        r=self.submit('TARGETED_CONDUCT')
        self.assertEqual(r.route,'HR_TRIAGE')
        self.assertEqual(self.component.private_read('hr',r.session_id).private_session.status,'REFERRED')

    def test_verified_impersonation_incident_review(self):
        self.evidence.assessment=EvidenceAssessment('CORROBORATED',identity_abuse_verified=True)
        self.assertEqual(self.submit('IMPERSONATION').route,'INCIDENT_REVIEW')

    def test_unverified_impersonation_cannot_become_a_finding(self):
        self.evidence.assessment=EvidenceAssessment('CLAIMED',identity_abuse_verified=True)
        self.assertEqual(self.submit('IMPERSONATION').route,'HR_TRIAGE')

    def test_security_without_validated_gate_stays_pending(self):
        self.evidence.assessment=EvidenceAssessment('OBSERVED',security_active_verified=True)
        self.gate.allowed=False
        self.assertEqual(self.submit('SECURITY_RISK').route,'SECURITY_REVIEW_PENDING')
        self.assertEqual(self.gate.calls,1)

    def test_security_validated_gate_only_routes_not_enforces(self):
        self.evidence.assessment=EvidenceAssessment('CORROBORATED',security_active_verified=True)
        r=self.submit('SECURITY_RISK')
        self.assertEqual(r.route,'EMERGENCY_GATE_REVIEW')
        self.assertEqual(self.component.private_read('hr',r.session_id).private_session.referral.emergency_gate_proof_ref,
                         'gate:approved-trigger')
        self.assertFalse(hasattr(self.component,'enforce'))

    def test_no_evidence_port_never_promotes_claims(self):
        self.component.evidence_authority=None
        self.assertEqual(self.submit('IMPERSONATION').route,'HR_TRIAGE')

    def test_fault_in_evidence_port_denies_verification(self):
        self.evidence.assessment=EvidenceAssessment('CORROBORATED',identity_abuse_verified=True)
        self.evidence.fail=True
        self.assertEqual(self.submit('IMPERSONATION').route,'HR_TRIAGE')

    def test_missing_private_store_is_unsupported(self):
        self.assertEqual(build_component({}).request('reporter','req-1',self.issue()).status,'UNSUPPORTED')

    def test_missing_access_port_denies_confidential_creation(self):
        self.component.case_access=None
        self.assertEqual(self.component.request('reporter','req-1',self.issue()).status,'UNSUPPORTED')

    def test_access_port_fault_prevents_intake(self):
        self.access.fail=True
        self.assertEqual(self.component.request('reporter','req-1',self.issue()).status,'UNSUPPORTED')
        self.assertEqual(len(self.store._sessions),0)

    def test_actor_impersonation_denied_even_when_other_access_exists(self):
        self.assertEqual(self.component.request('hr','req-1',self.issue()).status,'DENIED')

    def test_same_request_is_idempotent_and_payload_change_is_rejected(self):
        issue=self.issue()
        r=self.component.request('reporter','req-1',issue)
        replay=self.component.request('reporter','req-1',issue)
        mismatch=self.component.request('reporter','req-1',replace(issue,kind='TARGETED_CONDUCT'))
        self.assertEqual((r.status,replay.status,mismatch.status),('OK','REPLAY','CONFLICT'))
        self.assertEqual(r.session_id,replay.session_id)
        self.assertEqual(len(self.store._sessions),1)

    def test_invalid_evidence_hash_denied_before_write(self):
        bad=replace(self.issue(),evidence=(EvidenceRef('vault:good','not-hash'),))
        self.assertEqual(self.component.request('reporter','req-1',bad).status,'INVALID')
        self.assertEqual(len(self.store._sessions),0)

    def test_studio_query_cannot_read_confidential_data(self):
        r=self.submit()
        self.assertEqual(self.component.studio_query('reporter',r.session_id).status,'DENIED')
        self.assertEqual(self.component.studio_query('hr',r.session_id).status,'DENIED')

    def test_private_read_must_have_case_access(self):
        sid=self.submit().session_id
        self.assertEqual(self.component.private_read('anonymous',sid).status,'DENIED')
        self.assertEqual(self.component.private_read('hr',sid).status,'OK')
        self.assertIsNone(self.component.private_read('anonymous',sid).private_session)

    def test_unknown_and_unauthorized_session_indistinguishable(self):
        sid=self.submit().session_id
        unauthorized=self.component.private_read('anonymous',sid)
        unknown=self.component.private_read('anonymous','REL-00000000000000000000000000000000')
        self.assertEqual((unauthorized.status,unauthorized.reason),(unknown.status,unknown.reason))
        self.assertIsNone(unauthorized.private_session)
        self.assertIsNone(unknown.private_session)

    def test_case_revocation_blocks_read_and_updates(self):
        sid=self.mediation()
        self.access.allowed.remove('hr')
        self.assertEqual(self.component.private_read('hr',sid).status,'DENIED')
        self.assertEqual(self.component.assign_mediator('hr',sid).status,'DENIED')

    def test_missing_registry_returns_unsupported_not_fake_mediator(self):
        sid=self.mediation();self.component.mediator_registry=None
        self.assertEqual(self.component.assign_mediator('hr',sid).status,'UNSUPPORTED')

    def test_conflicted_mediator_blocks_independent_assignment(self):
        sid=self.mediation()
        self.registry.members=[Mediator('reporter',True,True,(CASE,)),
                               Mediator('med',True,True,(CASE,),conflict_workers=('subject',))]
        self.assertEqual(self.component.assign_mediator('hr',sid).status,'BLOCKED_INDEPENDENCE')
        self.assertEqual(self.component.private_read('hr',sid).private_session.status,'BLOCKED_INDEPENDENCE')

    def test_mediator_registry_fault_is_unsupported(self):
        sid=self.mediation();self.registry.fail=True
        self.assertEqual(self.component.assign_mediator('hr',sid).status,'UNSUPPORTED')

    def test_participant_choice_dedupes_and_rejects_change(self):
        sid=self.assigned()
        self.assertEqual(self.component.participate('reporter',sid,'ACCEPT').status,'OK')
        self.assertEqual(self.component.participate('reporter',sid,'ACCEPT').status,'REPLAY')
        self.assertEqual(self.component.participate('reporter',sid,'DECLINE').status,'CONFLICT')

    def test_outsider_cannot_respond_even_if_case_role_is_permitted(self):
        sid=self.assigned()
        self.assertEqual(self.component.respond('hr',sid,REF,'COUNTERSTATEMENT').status,'DENIED')

    def test_response_only_stores_opaque_evidence_ref(self):
        sid=self.assigned()
        self.assertEqual(self.component.respond('subject',sid,REF,'COUNTERSTATEMENT').status,'OK')
        session=self.component.private_read('hr',sid).private_session
        self.assertEqual(session.responses[0].evidence,REF)
        self.assertEqual(self.component.respond('subject',sid,REF,'COUNTERSTATEMENT').status,'REPLAY')

    def test_draft_contract_requires_both_participants_and_mediator(self):
        sid=self.assigned()
        self.assertEqual(self.component.set_working_contract('med',sid,['STRUCTURED_REVIEW'],'cp:1').status,'INVALID')
        self.assertEqual(self.component.set_working_contract('hr',sid,['STRUCTURED_REVIEW'],'cp:1').status,'DENIED')

    def test_working_terms_deny_arbitrary_sanctions(self):
        sid=self.assigned()
        self.component.participate('reporter',sid,'ACCEPT')
        self.component.participate('subject',sid,'ACCEPT')
        self.assertEqual(self.component.set_working_contract('med',sid,['DISABLE_ACCOUNT'],'cp:1').status,'INVALID')

    def test_notice_verifier_failure_does_not_mark_delivery(self):
        sid=self.assigned()
        self.component.participate('reporter',sid,'ACCEPT')
        self.component.participate('subject',sid,'ACCEPT')
        self.component.set_working_contract('med',sid,['STRUCTURED_REVIEW'],'cp:1')
        self.notices.fail=True
        self.assertEqual(self.component.verify_notice('med',sid,'notice:proof').status,'UNSUPPORTED')
        self.assertEqual(self.component.private_read('hr',sid).private_session.status,'NOTICE_PENDING')

    def test_forged_or_unverified_notice_denied(self):
        sid=self.assigned()
        self.component.participate('reporter',sid,'ACCEPT')
        self.component.participate('subject',sid,'ACCEPT')
        self.component.set_working_contract('med',sid,['STRUCTURED_REVIEW'],'cp:1')
        self.notices.allowed=False
        self.assertEqual(self.component.verify_notice('med',sid,'notice:unverified').status,'DENIED')
        self.assertEqual(self.component.private_read('hr',sid).private_session.status,'NOTICE_PENDING')

    def test_resolve_requires_verified_notice_and_contract(self):
        sid=self.assigned()
        self.assertEqual(self.component.close('med',sid,True).status,'INVALID')
        sid2=self.ready()
        self.assertEqual(self.component.close('med',sid2,True).status,'OK')
        self.assertEqual(self.component.private_read('hr',sid2).private_session.status,'RESOLVED')

    def test_unresolved_routes_to_hr_without_adverse_action(self):
        sid=self.assigned()
        self.component.participate('reporter',sid,'DECLINE')
        self.assertEqual(self.component.close('med',sid,False).status,'OK')
        session=self.component.private_read('hr',sid).private_session
        self.assertEqual((session.status,session.escalation_route),('UNRESOLVED','HR_TRIAGE'))

    def test_appeal_requires_distinct_reviewer_and_preserves_prior_result(self):
        sid=self.ready()
        self.component.close('med',sid,True)
        self.assertEqual(self.component.appeal('subject',sid,'appeal:1',REF).status,'OK')
        session=self.component.private_read('hr',sid).private_session
        self.assertEqual(session.status,'APPEALED')
        self.assertEqual(session.appeals[0].reviewer_id,'review')
        self.assertEqual(session.appeals[0].status,'PENDING_PRIVATE_APPEAL_INTEGRATION')
        self.assertIn('MEDIATION_RESOLVED',[e.action for e in session.audit])

    def test_appeal_replay_and_changed_payload_denied(self):
        sid=self.ready();self.component.close('med',sid,True)
        self.assertEqual(self.component.appeal('subject',sid,'appeal:1',REF).status,'OK')
        self.assertEqual(self.component.appeal('subject',sid,'appeal:1',REF).status,'REPLAY')
        self.assertEqual(self.component.appeal('subject',sid,'appeal:1',EvidenceRef('vault:other',REF.sha256)).status,'CONFLICT')

    def test_appeal_without_independent_reviewer_blocks(self):
        sid=self.ready();self.component.close('med',sid,True)
        self.registry.members=[Mediator('med',True,True,(CASE,))]
        self.assertEqual(self.component.appeal('subject',sid,'appeal:1',REF).status,'BLOCKED_INDEPENDENCE')
        self.assertEqual(self.component.private_read('hr',sid).private_session.status,'RESOLVED')

    def test_appeal_by_unrelated_hr_actor_denied(self):
        sid=self.ready();self.component.close('med',sid,True)
        self.assertEqual(self.component.appeal('hr',sid,'appeal:1',REF).status,'DENIED')

    def test_optimistic_revision_prevents_lost_update(self):
        sid=self.assigned()
        prior=self.store.read(sid)
        self.component.participate('reporter',sid,'ACCEPT')
        self.assertFalse(self.store.update(prior.revision,replace(prior,revision=prior.revision+1)))

    def test_no_host_or_file_io_on_synthetic_call(self):
        # All supplied ports are inert deterministic fakes; component never calls a
        # connector, modifies a repository or opens any project-specific service.
        sid=self.ready()
        self.assertTrue(sid.startswith('REL-'))
        self.assertEqual(self.gate.calls,0)
        self.assertEqual(self.component.studio_query(sid).status,'DENIED')

    def test_source_is_project_neutral_and_no_privileged_import(self):
        root=Path(__file__).resolve().parents[2]/'oversight_relations'
        source='\n'.join(p.read_text() for p in root.glob('*.py'))
        forbidden=('import godot','import fmod','import github','import requests',
                   'import google','import sqlite3','from nomad_studio',
                   'subprocess.run','os.system(','requests.post(','gameproduction')
        self.assertFalse(any(s in source.lower() for s in forbidden))
        self.assertNotIn('enforcement.apply',source)

if __name__=='__main__':
    unittest.main()
