"""M integration rejects unverified submissions, foreign ownership and stale intent state."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from studio.app.errors import StudioError
from studio.app.integration_commands import IntegrationService, Submission, IntegrationRecord


class Actor:
    principal_id = 'owner'
    def require_scope(self, project_id):
        if project_id != 'p':
            raise StudioError('forbidden','wrong project')


class C:
    def __init__(self):
        self.intent = None
        self.canonical = 'base'
        self.called = []
    def load_intent(self, *, project_id, intent_id):
        self.called.append('lookup')
        return self.intent
    def canonical_revision(self, *, project_id):
        self.called.append('canonical')
        return self.canonical


class SubmissionCatalog:
    def __init__(self, obj=None):
        self.obj=obj
        self.reads=[]
    def load_submission(self, *, project_id, submission_id):
        self.reads.append((project_id,submission_id))
        return self.obj


class Remote:
    def __init__(self):self.reads=0
    def verify_exact(self, **kwargs):
        self.reads+=1
        return False


class QA:
    def verify(self,**kwargs):return False


def submission(project='p', group='wg', submitted=True, verified=True,
               handoff_group='wg',plan='plan',changes=()):
    h=SimpleNamespace(group=handoff_group,plan_sha256=plan,changes=changes)
    return Submission('sub',project,'shift',group,h,verified,submitted)


def build(tmp_path, obj=None):
    source=SubmissionCatalog(obj)
    c=C()
    remote=Remote()
    service=IntegrationService(roster=SimpleNamespace(paths={'wg':['src/wg/**']},plan_sha256='plan'),
        tree_root=tmp_path,submissions=source,state=c,qa=QA(),remote=remote)
    return service,source,c,remote


@pytest.mark.parametrize('obj', [None, submission(project='other'),
    submission(submitted=False),submission(verified=False)])
def test_staging_rejects_unverified_submissions_before_filesystem_change(tmp_path,obj):
    svc,source,state,remote=build(tmp_path,obj)
    with pytest.raises(StudioError) as exc:
        svc.stageIntegration(Actor(),'p',{'request_id':'req','submission_id':'sub','expected_revision_id':'base'})
    assert exc.value.code=='missing'
    assert state.called == []
    assert remote.reads == 0


@pytest.mark.parametrize('obj,code', [
    (submission(group='unknown'),'invalid'),
    (submission(handoff_group='other'),'invalid'),
    (submission(plan='outdated'),'invalid'),
    (submission(changes=[SimpleNamespace(path='src/foreign/code.py')]),'forbidden'),
])
def test_staging_rejects_foreign_ownership_and_plan_drift(tmp_path,obj,code):
    svc,source,state,remote=build(tmp_path,obj)
    with pytest.raises(StudioError) as exc:
        svc.stageIntegration(Actor(),'p',{'request_id':'req','submission_id':'sub','expected_revision_id':'base'})
    assert exc.value.code==code
    assert not state.called


def test_stale_canonical_pointer_refuses_stage(tmp_path):
    svc,source,state,remote=build(tmp_path,submission())
    state.canonical='more-recent'
    with pytest.raises(StudioError) as exc:
        svc.stageIntegration(Actor(),'p',{'request_id':'req','submission_id':'sub','expected_revision_id':'base'})
    assert exc.value.code=='stale_revision'
    assert state.called == ['lookup','canonical']


def test_inspection_requires_durable_intent(tmp_path):
    svc,source,state,remote=build(tmp_path)
    with pytest.raises(StudioError) as exc:
        svc.inspectIntegration(Actor(),'p','unknown')
    assert exc.value.code=='missing'
    state.intent=IntegrationRecord('intent','p','base','candidate','sub','staged')
    assert svc.inspectIntegration(Actor(),'p','intent') == {'intent_id':'intent','state':'staged'}


@pytest.mark.parametrize('state_value', ['published','rejected','unknown','reverted'])
def test_promotion_never_skips_qa_or_state_gate(tmp_path,state_value):
    svc,source,state,remote=build(tmp_path)
    state.intent=IntegrationRecord('intent','p','base','candidate','sub',state_value)
    with pytest.raises(StudioError) as exc:
        svc.promoteIntegration(Actor(),'p','intent',{'request_id':'r','expected_revision_id':'base','qa_review_ids':[]})
    assert exc.value.code=='conflict'
    assert remote.reads==0


def test_promotion_rejects_foreign_project_intent(tmp_path):
    svc,source,state,remote=build(tmp_path)
    state.intent=IntegrationRecord('intent','other','base','candidate','sub','staged')
    with pytest.raises(StudioError) as exc:
        svc.promoteIntegration(Actor(),'p','intent',{'request_id':'r','expected_revision_id':'base','qa_review_ids':[]})
    assert exc.value.code=='conflict'


def test_cross_project_actor_cannot_inspect(tmp_path):
    svc,source,state,remote=build(tmp_path)
    with pytest.raises(StudioError,match='wrong project'):
        svc.inspectIntegration(Actor(),'other','intent')