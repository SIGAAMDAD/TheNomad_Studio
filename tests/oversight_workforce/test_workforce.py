import dataclasses
import hashlib
import unittest

from oversight_workforce import WorkforcePlanning, build_component
from oversight_workforce.contracts import (CapabilityEvidence, CoverageNeed, GrantClaim,
    Identity, Lifecycle, QualificationRequest, Result, ReviewItem, Reviewer, Role,
    TrainingEvidence)
from oversight_workforce.store import LocalJournal

NOW = 1_900_000_000
SHA = hashlib.sha256(b'objective source').hexdigest()
PERSONA = hashlib.sha256(b'persona').hexdigest()


def fixtures(action=Lifecycle.QUALIFY, role='artist', worker='w-1'):
    roles = {
        'artist': Role('artist', 'prod', frozenset({'read'}), frozenset({'art-tool'})),
        'lead': Role('lead', 'prod', frozenset({'read', 'approve'}), frozenset({'art-tool','review-tool'})),
        'unknown': None,
    }
    people = {
        'w-1': Identity('w-1', 'artist', 'prod', 4, 9, 3, PERSONA, frozenset({'read'}), True),
        'w-2': Identity('w-2', 'artist', 'prod', 1, 2, 3, PERSONA, frozenset({'read'}), False),
        'w-retired': Identity('w-retired', 'artist', 'prod', 8, 8, 3, PERSONA, frozenset({'read'}), False, True),
    }
    identity = people[worker]
    receipts = tuple(CapabilityEvidence('rc-'+cap, worker, role, identity.identity_epoch, cap, SHA, NOW-20, NOW+500)
                     for cap in roles[role].required_capabilities)
    training = (TrainingEvidence('tr-1', worker, role, 'eval-1', SHA, 'PASS', NOW-40, NOW+400),)
    grant = GrantClaim('grant-1', worker, identity.identity_epoch, action, role,
                       roles[role].privileges, 'policy-v1', NOW+600)
    request = QualificationRequest('request-1', worker, identity.identity_epoch, identity.assignment_epoch,
                                   identity.role_id, identity.persona_revision, identity.persona_digest,
                                   action, role, 'policy-v1', grant, receipts, training)
    deps = dict(identity_reader=people.get, role_reader=roles.get,
                grant_verifier=lambda g,i,n: True,
                capability_verifier=lambda c,n: True,
                training_verifier=lambda t,n: True,
                clock=lambda: NOW)
    return request, deps, roles, people


class QualificationTests(unittest.TestCase):
    def test_positive_current_scope_with_dossier_and_expiry(self):
        q, deps, _, _ = fixtures()
        c = build_component(deps)
        outcome = c.recommend(q)
        self.assertEqual(outcome.status, Result.RECOMMENDED)
        self.assertEqual(outcome.valid_until, NOW+400)
        self.assertEqual(outcome.minimum_permissions, ('read',))
        d = c.dossier(q.worker_id)
        self.assertEqual(len(d.records), 1)
        self.assertEqual(d.records[0], outcome)

    def test_replay_idempotent_and_conflicting_replay_denied(self):
        q, deps, _, _ = fixtures()
        c = build_component(deps)
        first = c.recommend(q)
        self.assertEqual(c.recommend(q), first)
        tampered = dataclasses.replace(q, policy_revision='policy-v2')
        self.assertEqual(c.recommend(tampered).reason_code, 'DENIED_REPLAY_CONFLICT')
        self.assertEqual(len(c.dossier(q.worker_id).records), 1)

    def test_replay_rechecks_revocation_and_current_epoch(self):
        q, deps, _, people = fixtures()
        current={'authorized':True}
        deps['grant_verifier']=lambda g,i,n: current['authorized']
        c=build_component(deps)
        self.assertEqual(c.recommend(q).status,Result.RECOMMENDED)
        current['authorized']=False
        self.assertEqual(c.recommend(q).reason_code, 'DENIED_REPLAY_NOT_CURRENT')
        current['authorized']=True
        people['w-1']=dataclasses.replace(people['w-1'], identity_epoch=5)
        self.assertEqual(c.recommend(q).reason_code, 'DENIED_REPLAY_NOT_CURRENT')

    def test_forged_handover_recommendation_is_rejected(self):
        q, deps, _, _ = fixtures(action=Lifecycle.ROLE_CHANGE,role='lead')
        c=build_component(deps)
        d=c.recommend(q)
        forged=dataclasses.replace(d, recommendation_id='fake')
        self.assertEqual(c.handover(forged).status,Result.DENIED)
        self.assertEqual(c.handover(dataclasses.replace(d, valid_until=NOW+9999)).status,Result.DENIED)

    def test_wrong_identity(self):
        q, deps, _, _ = fixtures()
        q = dataclasses.replace(q, worker_id='imposter')
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'WRONG_IDENTITY')

    def test_stale_epoch(self):
        q, deps, _, _ = fixtures()
        q = dataclasses.replace(q, assignment_epoch=8)
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'STALE_EPOCH')

    def test_stale_persona(self):
        q, deps, _, _ = fixtures()
        q = dataclasses.replace(q, persona_revision=5)
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'STALE_PERSONA_BINDING')

    def test_missing_owner_grant(self):
        q, deps, _, _ = fixtures()
        self.assertEqual(build_component(deps).recommend(dataclasses.replace(q, grant=None)).reason_code, 'MISSING_OWNER_GRANT')

    def test_expired_owner_grant(self):
        q, deps, _, _ = fixtures()
        self.assertEqual(build_component(deps).recommend(dataclasses.replace(q,grant=dataclasses.replace(q.grant,expires_at=NOW-1))).reason_code,
                         'INVALID_OWNER_GRANT_BINDING')

    def test_revoked_grant_denied(self):
        q, deps, _, _ = fixtures()
        deps['grant_verifier'] = lambda g,i,n: False
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'OWNER_GRANT_NOT_AUTHENTICATED')

    def test_owner_verifier_missing_or_down(self):
        q, deps, _, _ = fixtures()
        deps.pop('grant_verifier')
        self.assertEqual(build_component(deps).recommend(q).status, Result.NOT_VERIFIED)
        deps['grant_verifier'] = lambda *args: 1/0
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'OWNER_GRANT_VERIFIER_FAILURE')

    def test_expired_capability(self):
        q, deps, _, _ = fixtures()
        receipts = (dataclasses.replace(q.capabilities[0], expires_at=NOW),)
        q = dataclasses.replace(q, capabilities=receipts)
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'INVALID_OR_EXPIRED_CAPABILITY')

    def test_expired_training(self):
        q, deps, _, _ = fixtures()
        q = dataclasses.replace(q, training=(dataclasses.replace(q.training[0], expires_at=NOW),))
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'INVALID_OR_EXPIRED_TRAINING')

    def test_self_assessed_training_denied(self):
        q, deps, _, _ = fixtures()
        q = dataclasses.replace(q, training=(dataclasses.replace(q.training[0], evaluator_id='w-1'),))
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'INVALID_OR_EXPIRED_TRAINING')

    def test_missing_training_insufficient(self):
        q, deps, _, _ = fixtures()
        q = dataclasses.replace(q,training=())
        self.assertEqual(build_component(deps).recommend(q).status, Result.INSUFFICIENT_EVIDENCE)

    def test_unknown_role_denied(self):
        q, deps, _, _ = fixtures()
        q = dataclasses.replace(q, target_role_id='unknown')
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'UNKNOWN_CANONICAL_ROLE')

    def test_sneak_role_change_under_qualify_denied(self):
        q, deps, _, _ = fixtures(action=Lifecycle.QUALIFY,role='lead')
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'ROLE_CHANGE_ACTION_REQUIRED')

    def test_role_change_with_exact_grant_and_handover(self):
        q, deps, _, people = fixtures(action=Lifecycle.ROLE_CHANGE,role='lead')
        c=build_component(deps)
        d=c.recommend(q)
        self.assertEqual(d.status, Result.RECOMMENDED)
        h=c.handover(d)
        self.assertEqual(h.status, Result.RECOMMENDED)
        self.assertTrue(h.fence_old_leases)
        self.assertEqual(h.proposed_next_assignment_epoch,10)
        self.assertEqual(h.proposed_next_persona_revision,4)
        self.assertEqual(people['w-1'].role_id,'artist')
        # A later Studio epoch invalidates any prior handover recommendation.
        people['w-1'] = dataclasses.replace(people['w-1'], assignment_epoch=10)
        self.assertEqual(c.handover(d).status, Result.DENIED)

    def test_grant_role_escalation_and_epoch_mismatch(self):
        q, deps, _, _ = fixtures(action=Lifecycle.ROLE_CHANGE,role='lead')
        forged = dataclasses.replace(q.grant, target_privileges=frozenset({'read'}))
        self.assertEqual(build_component(deps).recommend(dataclasses.replace(q, grant=forged)).reason_code,
                         'INVALID_OWNER_GRANT_BINDING')
        forged = dataclasses.replace(q.grant, identity_epoch=9)
        self.assertEqual(build_component(deps).recommend(dataclasses.replace(q, grant=forged)).reason_code,
                         'INVALID_OWNER_GRANT_BINDING')

    def test_retired_identity_reuse_denied(self):
        q, deps, _, _ = fixtures(worker='w-retired')
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'RETIRED_ID_REUSE')

    def test_inactive_existing_joiner_only(self):
        q, deps, _, _ = fixtures(worker='w-2',action=Lifecycle.JOINER)
        self.assertEqual(build_component(deps).recommend(q).status, Result.RECOMMENDED)
        q = dataclasses.replace(q, action=Lifecycle.QUALIFY, grant=dataclasses.replace(q.grant,action=Lifecycle.QUALIFY))
        self.assertEqual(build_component(deps).recommend(q).reason_code, 'INACTIVE_STUDIO_IDENTITY')

    def test_reinstatement_inactive_existing_id_only(self):
        q, deps, _, _ = fixtures(worker='w-2', action=Lifecycle.REINSTATEMENT)
        self.assertEqual(build_component(deps).recommend(q).status, Result.RECOMMENDED)
        q2, deps2, _, _ = fixtures(worker='w-1', action=Lifecycle.REINSTATEMENT)
        self.assertEqual(build_component(deps2).recommend(q2).reason_code, 'REINSTATEMENT_ALREADY_ACTIVE')

    def test_all_lifecycle_recommendations_never_mutate_registry(self):
        for action in Lifecycle:
            with self.subTest(action=action):
                worker='w-2' if action in (Lifecycle.JOINER, Lifecycle.REINSTATEMENT) else 'w-1'
                q, deps, _, people = fixtures(worker=worker,action=action)
                if action in (Lifecycle.TEMP_RESTRICTION, Lifecycle.RETIREMENT):
                    q=dataclasses.replace(q, reason_digest=SHA)
                original=people[worker]
                outcome=build_component(deps).recommend(q)
                self.assertEqual(outcome.status,Result.RECOMMENDED)
                self.assertEqual(people[worker],original)

    def test_restriction_requires_evidence_and_is_only_recommendation(self):
        q, deps, _, people = fixtures(action=Lifecycle.TEMP_RESTRICTION)
        c=build_component(deps)
        self.assertEqual(c.recommend(q).reason_code, 'MISSING_OBJECTIVE_REASON')
        q=dataclasses.replace(q,request_id='request-2',reason_digest=SHA)
        self.assertEqual(c.recommend(q).status,Result.RECOMMENDED)
        self.assertEqual(people['w-1'].privileges,frozenset({'read'}))

    def test_unconfigured_default_fails_closed(self):
        q, _, _, _ = fixtures()
        self.assertEqual(build_component({}).recommend(q).status, Result.UNSUPPORTED)

    def test_verifier_fault_cannot_become_success(self):
        q, deps, _, _ = fixtures()
        deps['training_verifier']=lambda *_: (_ for _ in ()).throw(RuntimeError('offline'))
        self.assertEqual(build_component(deps).recommend(q).reason_code,'TRAINING_PROVIDER_FAILURE')

    def test_no_effect_apis_or_project_specific_core(self):
        c=build_component({})
        self.assertFalse(any(hasattr(c, attr) for attr in ('admit_worker','write_registry','push_to_github',
                          'publish','game_build','fmod','godot','mutate_lease')))
        import pathlib
        root=pathlib.Path(__file__).parents[2]/'oversight_workforce'
        for f in root.glob('*.py'):
            content=f.read_text().lower()
            for token in ('import godot','import fmod','import gameproduction',
                          'github import','google_drive import','requests.post('):
                self.assertNotIn(token,content)


class WorkforcePlanningTests(unittest.TestCase):
    def setUp(self):
        self.planner=WorkforcePlanning()
        self.item=ReviewItem('intake-1','accused','reporter','credential-abuse',SHA,NOW,7,'security')

    def test_intake_deduplicates_and_replay_conflicts(self):
        self.assertEqual(self.planner.intake(self.item),'RECORDED')
        self.assertEqual(self.planner.intake(self.item),'DUPLICATE')
        self.assertEqual(self.planner.intake(dataclasses.replace(self.item,priority=6)),'DENIED_REPLAY_CONFLICT')
        self.assertEqual(len(self.planner.backlog()),1)

    def test_review_never_assigned_to_subject_or_reporter(self):
        reviewers=[Reviewer('accused',frozenset({'security'}),NOW+50,True,0,3),
                   Reviewer('reporter',frozenset({'security'}),NOW+50,True,0,3)]
        d=self.planner.suggest_review(self.item,reviewers,NOW)
        self.assertEqual(d.status,Result.BLOCKED_INDEPENDENCE)
        self.assertIsNone(d.reviewer_id)

    def test_independent_reviewer_and_appeal_conflict(self):
        reviewers=[Reviewer('staff-b',frozenset({'security'}),NOW+50,True,0,2),
                   Reviewer('staff-c',frozenset({'security'}),NOW+50,True,1,2)]
        d=self.planner.suggest_review(self.item,reviewers,NOW)
        self.assertEqual(d.status,Result.REVIEW_RECOMMENDED)
        self.assertEqual(d.reviewer_id,'staff-b')
        d=self.planner.suggest_review(self.item,reviewers,NOW,prior_review_authors=['staff-b'])
        self.assertEqual(d.reviewer_id,'staff-c')

    def test_expired_training_capacity_and_conflicts_block(self):
        reviewers=[Reviewer('r-1',frozenset({'security'}),NOW,True,0,2),
                   Reviewer('r-2',frozenset({'security'}),NOW+50,True,2,2),
                   Reviewer('r-3',frozenset({'security'}),NOW+50,True,0,2,frozenset({'accused'}))]
        self.assertEqual(self.planner.suggest_review(self.item,reviewers,NOW).status, Result.BLOCKED_INDEPENDENCE)

    def test_coverage_uses_supplied_target_not_implicit_threshold(self):
        _, _, _, people = fixtures()
        result=self.planner.coverage(list(people.values()),[CoverageNeed('artist','prod',5)])
        self.assertEqual((result[0].required,result[0].available,result[0].deficit),(5,1,4))
        self.assertRaises(ValueError,self.planner.coverage,list(people.values()),[CoverageNeed('artist','prod',-1)])

    def test_schedule_needs_policy_and_deduplicates(self):
        _, _, _, people=fixtures()
        self.assertEqual(self.planner.schedule(list(people.values()),None).status,Result.NEEDS_POLICY)
        self.assertEqual(self.planner.schedule([people['w-1'],people['w-1']],5,'owner-policy').status,Result.DENIED)
        self.assertEqual(self.planner.schedule(list(people.values()),1,'owner-policy').worker_ids,('w-1',))

    def test_no_confidential_case_narrative_ingested(self):
        item=dataclasses.replace(self.item,category='private narrative with spaces')
        self.assertEqual(self.planner.intake(item),'DENIED_INVALID_INTAKE')
        self.assertEqual(self.planner.backlog(),())


if __name__=='__main__': unittest.main()
