from __future__ import annotations

import pytest
from studio.app.errors import StudioError
from studio.app.projects import Actor
from studio.app.releases import (EffectRecord, ReleaseRecord, ReleaseCoordinator,
                                 RecoveryCoordinator)

OWNER=Actor('owner','trusted',frozenset({'p1'}),'proof',True)
USER=Actor('user','trusted',frozenset({'p1'}),'proof')
SHA='a'*64


class Evidence:
    def __init__(self):
        self.qa=True;self.approval=True;self.artifacts=True;self.snapshot=True
    def verified_source_qa_ci(self,**kwargs):return self.qa
    def verified_canon_snapshot(self,**kwargs):return self.snapshot
    def verified_artifacts(self,**kwargs):return self.artifacts
    def verified_release_digest(self,**kwargs):return True
    def approved_exact_effect(self,**kwargs):return self.approval


class State:
    def __init__(self):
        self.release=None; self.effect=EffectRecord('effect','p1','r1',SHA,'prepared')
        self.observed=False; self.unknown=False
    def prepare_release(self,**kwargs):
        self.release=ReleaseRecord(kwargs['project_id'],'r1',kwargs['source_revision_id'],
                                   kwargs['snapshot_id'],kwargs['release_digest'],'prepared')
        return self.release
    def get_release(self,**kwargs):return self.release
    def list_releases(self,**kwargs):return [self.release] if self.release else []
    def prepare_publication(self,**kwargs):
        if self.unknown:
            return EffectRecord('effect','p1','r1',SHA,'unknown')
        return self.effect
    def observe_publication(self,**kwargs):self.observed=True
    def mark_publication_unknown(self,**kwargs):self.unknown=True


class Publication:
    def __init__(self):self.remote=None;self.publishes=0;self.fail=False
    def observe_exact(self,**kwargs):return self.remote
    def publish(self,**kwargs):
        self.publishes+=1
        if self.fail:raise TimeoutError('provider timeout')
        self.remote=kwargs['effect_digest']


def prepare(service):
    return service.prepareRelease(USER,'p1',{
        'request_id':'r','source_revision_id':'source','snapshot_id':'canon',
        'artifact_ids':['artifact'],'release_digest':SHA})


def publish(service):
    return service.publishRelease(USER,'p1','r1',{
        'request_id':'p','source_revision_id':'source','snapshot_id':'canon',
        'effect_digest':SHA,'decision_id':'owner-approval'})


def test_prepared_release_must_have_qa_and_artifact_proof():
    state=State();evidence=Evidence();pub=Publication()
    service=ReleaseCoordinator(state,evidence,pub)
    evidence.qa=False
    with pytest.raises(StudioError) as e:prepare(service)
    assert e.value.code=='qa_failed'
    evidence.qa=True; evidence.artifacts=False
    with pytest.raises(StudioError):prepare(service)
    evidence.artifacts=True
    assert prepare(service)=={'release_id':'r1','state':'prepared'}
    assert service.listReleases(USER,'p1')['releases'][0]['state']=='prepared'


def test_publish_requires_exact_owner_decision_and_readback():
    state=State();evidence=Evidence();pub=Publication()
    service=ReleaseCoordinator(state,evidence,pub)
    prepare(service);evidence.approval=False
    with pytest.raises(StudioError) as e:publish(service)
    assert e.value.code=='requires_owner'
    assert pub.publishes==0
    evidence.approval=True
    assert publish(service)=={'release_id':'r1','state':'published'}
    assert pub.publishes==1 and state.observed


def test_timeout_marks_unknown_and_never_blindly_retries():
    state=State();evidence=Evidence();pub=Publication();pub.fail=True
    service=ReleaseCoordinator(state,evidence,pub);prepare(service)
    with pytest.raises(StudioError) as e:publish(service)
    assert e.value.code=='unknown_effect'
    assert state.unknown
    pub.fail=False
    with pytest.raises(StudioError) as e:publish(service)
    assert e.value.code=='unknown_effect'
    assert pub.publishes==1
    pub.remote=SHA
    assert publish(service)['state']=='published'
    assert pub.publishes==1


def test_wrong_remote_release_digest_refuses_pub():
    state=State();evidence=Evidence();pub=Publication()
    pub.remote='b'*64
    service=ReleaseCoordinator(state,evidence,pub);prepare(service)
    with pytest.raises(StudioError) as e:publish(service)
    assert e.value.code=='conflict'
    assert pub.publishes==0


class RecoveryState:
    def __init__(self):self.revision=1;self.verified=True;self.outstanding=[]
    def project_revision(self,**kwargs):return self.revision
    def backup_is_verified(self,**kwargs):return self.verified
    def outstanding_uncertain_effects(self,**kwargs):return self.outstanding
    def request_restore(self,**kwargs):return ('rec-1','requested')
    def recovery_state(self,**kwargs):return 'requested'


def test_restore_rejects_stale_unknown_and_unverified():
    state=RecoveryState();service=RecoveryCoordinator(state)
    request={'request_id':'x','backup_digest':SHA,'expected_revision':1}
    with pytest.raises(StudioError) as e:service.requestRestore(USER,'p1',request)
    assert e.value.code=='requires_owner'
    state.verified=False
    with pytest.raises(StudioError):service.requestRestore(OWNER,'p1',request)
    state.verified=True;state.outstanding=['unknown-effect']
    with pytest.raises(StudioError) as e:service.requestRestore(OWNER,'p1',request)
    assert e.value.code=='unknown_effect'
    assert service.observeRecovery(USER,'p1')['outstanding_effect_ids']==['unknown-effect']
    state.outstanding=[];state.revision=2
    with pytest.raises(StudioError) as e:service.requestRestore(OWNER,'p1',request)
    assert e.value.code=='stale_revision'
    state.revision=1
    assert service.requestRestore(OWNER,'p1',request)['state']=='requested'
