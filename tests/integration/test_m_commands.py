from __future__ import annotations

from pathlib import Path
import pytest
from studio.app.errors import StudioError
from studio.app.intake import Roster, Handoff, Change, digest
from studio.app.integration import CanonicalTree
from studio.app.integration_commands import (Submission, IntegrationRecord, IntegrationService)
from studio.app.projects import Actor
from studio.app.verification import IndependentReceiptGate, RemoteTreeReadbackGate, QARecord

PACKET=Path(__import__('os').environ.get('STUDIO_CONTRIBUTION_PLAN', Path(__file__).resolve().parents[2] / 'tests/fixtures/source_contributions'))
ACTOR=Actor('operator','cockpit',frozenset({'P'}),'trusted')

class State:
    def __init__(self,root):
        self.canonical=CanonicalTree(root,'P').current_revision()
        self.record=None
    def canonical_revision(self,**kwargs):return self.canonical
    def record_stage(self,**kwargs):
        self.record=IntegrationRecord(kwargs['intent_id'], kwargs['project_id'],
                    kwargs['expected_revision_id'],kwargs['staged_revision_id'],kwargs['submission_id'],'staged')
        return self.record
    def load_intent(self,**kwargs):return self.record
    def cas_canonical_promotion(self,**kwargs):
        if self.canonical!=kwargs['expected_revision_id']:return False
        self.canonical=kwargs['new_revision_id'];return True

class Submissions:
    def __init__(self,submission):self.submission=submission
    def load_submission(self,**kwargs):return self.submission

class QA:
    def __init__(self):self.revision=None
    def verify(self,*,project_id,revision_id,submission_ids,authors,review_ids):
        return review_ids==('qa-exact',) and (self.revision is None or revision_id==self.revision)

class Remote:
    def __init__(self):self.valid=True
    def verify_exact(self,**kwargs):return self.valid


def make_service(tmp_path):
    roster=Roster.load(PACKET)
    state=State(tmp_path)
    h=Handoff('M',roster.plan_sha256,state.canonical,
              (Change('src/studio/app/new.py',None,digest(b'hello'),b'hello'),),())
    sub=Submission('submission','P','author-session','M',h,True,True)
    qa=QA();remote=Remote()
    service=IntegrationService(roster=roster,tree_root=tmp_path,
        submissions=Submissions(sub),state=state,qa=qa,remote=remote)
    return service,state,qa,remote


def test_stage_promote_and_reload_exact_revision(tmp_path):
    svc,state,qa,remote=make_service(tmp_path)
    staged=svc.stageIntegration(ACTOR,'P',{'request_id':'new','submission_id':'submission',
                                          'expected_revision_id':state.canonical})
    assert staged['state']=='staged'
    intent=staged['intent_id']
    assert svc.inspectIntegration(ACTOR,'P',intent)['state']=='staged'
    assert state.canonical==CanonicalTree(tmp_path,'P').current_revision()
    result=svc.promoteIntegration(ACTOR,'P',intent,{
        'request_id':'promote','expected_revision_id':state.canonical,'qa_review_ids':['qa-exact']})
    assert result['state']=='promoted'
    assert result['source_revision_id']==state.canonical
    assert CanonicalTree(tmp_path,'P').read_revision(state.canonical)['src/studio/app/new.py']==b'hello'


def test_wrong_actor_and_unverified_provider_rejected(tmp_path):
    svc,state,qa,remote=make_service(tmp_path)
    with pytest.raises(StudioError):
        svc.stageIntegration(Actor('bad','cockpit',frozenset(),'proof'),'P',
                             {'request_id':'new','submission_id':'submission','expected_revision_id':state.canonical})
    staged=svc.stageIntegration(ACTOR,'P',{'request_id':'new','submission_id':'submission',
                                          'expected_revision_id':state.canonical})
    remote.valid=False
    with pytest.raises(StudioError) as e:
        svc.promoteIntegration(ACTOR,'P',staged['intent_id'],{
            'request_id':'promote','expected_revision_id':state.canonical,'qa_review_ids':['qa-exact']})
    assert e.value.code=='unknown_effect'
    assert state.canonical==CanonicalTree(tmp_path,'P').current_revision()


def test_cross_domain_submission_rejected(tmp_path):
    svc,state,qa,remote=make_service(tmp_path)
    sub=svc.submissions.submission
    invalid=Handoff('M',sub.handoff.plan_sha256,sub.handoff.base_revision_id,
                    (Change('src/studio/governance/attack.py',None,digest(b'x'),b'x'),),())
    svc.submissions.submission=Submission('submission','P','author-session','M',invalid,True,True)
    with pytest.raises(StudioError) as e:
        svc.stageIntegration(ACTOR,'P',{'request_id':'new','submission_id':'submission',
                                        'expected_revision_id':state.canonical})
    assert e.value.code=='forbidden'


def test_stage_same_idempotency_key_reuses_stage(tmp_path):
    svc,state,qa,remote=make_service(tmp_path)
    request={'request_id':'same','submission_id':'submission','expected_revision_id':state.canonical}
    first=svc.stageIntegration(ACTOR,'P',request)
    replay=svc.stageIntegration(ACTOR,'P',request)
    assert first==replay


def test_replayed_stage_after_canonical_changes_is_not_restaged(tmp_path):
    svc,state,qa,remote=make_service(tmp_path)
    request={'request_id':'same','submission_id':'submission','expected_revision_id':state.canonical}
    first=svc.stageIntegration(ACTOR,'P',request)
    svc.promoteIntegration(ACTOR,'P',first['intent_id'],{
        'request_id':'promotion','expected_revision_id':state.canonical,'qa_review_ids':['qa-exact']})
    # Immutable original stage can still be inspected even after pointer advances.
    assert svc.stageIntegration(ACTOR,'P',request)['intent_id']==first['intent_id']
