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

class FakeCanonical:
    def __init__(self,tmp_path):
        self.candidate=SimpleNamespace(project_id='p',expected_revision_id='base',revision_id='candidate',
            submission_ids=('sub',),author_ids=('shift',),tree_digest='tree',tree={'src/wg/x.txt':b'value'})
        self.stages=tmp_path/'stages'
        self.stages.mkdir(parents=True,exist_ok=True)
        (self.stages/'intent').mkdir(exist_ok=True)
        self.current='base';self.promotions=[];self.recovered=[]
    def current_revision(self):return self.current
    def stage(self,*,intent_id,project_id,submissions):
        assert project_id=='p' and submissions[0][0]=='sub'
        return self.candidate
    def inspect(self,intent_id):
        assert intent_id
        return self.candidate
    def promote(self,**kw):
        self.promotions.append(kw)
        return self.candidate.revision_id
    def read_revision(self,revision):
        assert revision=='candidate';return self.candidate.tree
    def recover_local_pointer(self,revision,tree):
        self.recovered.append((revision,tree))


class ConnectedC(C):
    def __init__(self):
        super().__init__()
        self.cas=True;self.record=[]
    def record_stage(self,**kwargs):
        self.record.append(kwargs)
        self.intent=IntegrationRecord(kwargs['intent_id'],'p',kwargs['expected_revision_id'],
                                      kwargs['staged_revision_id'],'sub','staged')
        return self.intent
    def cas_canonical_promotion(self,**kwargs):
        return self.cas


class ConfigurableQA:
    def __init__(self,accepted=False):self.accepted=accepted;self.calls=[]
    def verify(self,**kwargs):self.calls.append(kwargs);return self.accepted


class ConfigurableRemote(Remote):
    def verify_exact(self,**kwargs):
        self.reads+=1
        return getattr(self,'accepted',False)


def connected(tmp_path):
    original=submission(changes=(SimpleNamespace(path='src/wg/x.txt'),))
    svc,source,old,remote=build(tmp_path,original)
    c=ConnectedC();svc.state=c
    tree=FakeCanonical(tmp_path);svc._tree=lambda project:tree
    qa=ConfigurableQA();svc.qa=qa
    remote=ConfigurableRemote();svc.remote=remote
    return svc,c,tree,qa,remote


def test_stage_persists_exact_verified_source_identity_and_replays(tmp_path):
    svc,state,tree,qa,remote=connected(tmp_path)
    request={'request_id':'r','submission_id':'sub','expected_revision_id':'base'}
    result=svc.stageIntegration(Actor(),'p',request)
    assert result['state']=='staged'
    assert state.record[0]['staged_revision_id']=='candidate'
    assert svc.stageIntegration(Actor(),'p',request)==result
    state.intent=IntegrationRecord(result['intent_id'],'p','base','tampered','sub','staged')
    with pytest.raises(StudioError,match='stage cache'):
        svc.stageIntegration(Actor(),'p',request)


def test_promotion_needs_exact_canonical_qa_remote_and_cas(tmp_path):
    svc,state,tree,qa,remote=connected(tmp_path)
    staged=svc.stageIntegration(Actor(),'p',{'request_id':'stage','submission_id':'sub','expected_revision_id':'base'})
    intent=staged['intent_id']
    request={'request_id':'promote','expected_revision_id':'base','qa_review_ids':['review']}
    with pytest.raises(StudioError) as err:svc.promoteIntegration(Actor(),'p',intent,request)
    assert err.value.code=='qa_failed'
    qa.accepted=True
    with pytest.raises(StudioError) as err:svc.promoteIntegration(Actor(),'p',intent,request)
    assert err.value.code=='unknown_effect'
    remote.accepted=True
    state.cas=False
    with pytest.raises(StudioError) as err:svc.promoteIntegration(Actor(),'p',intent,request)
    assert err.value.code=='stale_revision'
    state.cas=True
    assert svc.promoteIntegration(Actor(),'p',intent,request)=={'source_revision_id':'candidate','state':'promoted'}
    assert len(tree.promotions)==1


def test_canonical_mismatch_and_corrupt_intent_are_rejected_before_QA(tmp_path):
    svc,state,tree,qa,remote=connected(tmp_path)
    result=svc.stageIntegration(Actor(),'p',{'request_id':'stage','submission_id':'sub','expected_revision_id':'base'})
    req={'request_id':'promote','expected_revision_id':'stale','qa_review_ids':['review']}
    with pytest.raises(StudioError) as exc:svc.promoteIntegration(Actor(),'p',result['intent_id'],req)
    assert exc.value.code=='stale_revision'
    state.intent=IntegrationRecord(result['intent_id'],'p','base','tampered','sub','staged')
    with pytest.raises(StudioError) as exc:svc.promoteIntegration(Actor(),'p',result['intent_id'],req)
    assert exc.value.code=='invalid'
    assert qa.calls==[]


def test_recovery_requires_verified_bytes_for_local_mirror(tmp_path):
    svc,state,tree,qa,remote=connected(tmp_path)
    state.canonical='candidate'
    tree.current='base'
    with pytest.raises(StudioError) as exc:svc.reconcileLocalMirror(project_id='p')
    assert exc.value.code=='unknown_effect'
    remote.accepted=True
    assert svc.reconcileLocalMirror(project_id='p')=='candidate'
    assert tree.recovered==[('candidate',tree.candidate.tree)]
    tree.current='candidate'
    assert svc.reconcileLocalMirror(project_id='p')=='candidate'