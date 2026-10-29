from nomad_studio.single_operator.capabilities.host import WorkerHostService, AgentExecutionService
from nomad_studio.trust.port_types import Decision,sha
from r9_helpers import SignedNative,Store,Journal

class NativeHost:
    def __init__(self,native):
        self.native=native;self.history=[];self.effects={};self.send_calls=0;self.fail=False
    def manifest_ref(self,provider):
        ref='native/manifest'
        self.native.sign(ref,'HOST_MANIFEST',observed_native=True,source='HOST_NATIVE',
            observed_at_us=200_000,capabilities={k:'SUPPORTED' for k in
            ('dispatch','checkpoint','same_session_reentry','fence','agent_execution','cancel')})
        return ref
    def activation_history(self,sid):return self.history
    def receipt_kind(self,ref):return self.native.receipts[ref]['purpose']
    def dispatch(self,task,key):
        self.send_calls+=1
        if self.fail: raise TimeoutError('provider reply dropped')
        ref='native/effect'
        self.native.sign(ref,'HOST_DISPATCH',observed_native=True,source='HOST_NATIVE',
            observed_at_us=300_000,request_digest=sha(task),effect_state='APPLIED',
            task_id=task['task']['task_id'],job_id=task['task']['task_id'],session_id=task['session_id'])
        self.effects[('HOST_DISPATCH',key)]=ref
    def lookup_effect(self,kind,key):return self.effects.get((kind,key))
    def checkpoint_ref(self,sid,jid,epoch):return None
    def fence_session(self,sid,epoch,key):
        payload={'session_id':sid,'session_epoch':epoch,'provider_id':'fixture/provider'}
        ref='native/fence'
        self.native.sign(ref,'HOST_FENCE',observed_native=True,source='HOST_NATIVE',
            observed_at_us=400_000,request_digest=sha(payload),effect_state='APPLIED',
            session_id=sid,session_epoch=epoch)
        self.effects[('HOST_FENCE',key)]=ref

class Authority:
    def verify_dispatch(self,task,principal,session):
        return {'authorized':True,'task_id':task['task_id'],'lease_epoch':3,'session_epoch':4}

def setup(production=True):
    native=SignedNative(production=production);store=Store();store.enroll('worker/1')
    journal=Journal();host=NativeHost(native)
    api=WorkerHostService(store=store,verifier=native.verifier,host=host,journal=journal,
                          task_authority=Authority(),clock_us=lambda:1_000_000)
    return native,store,host,journal,api

def session(native,api):
    native.sign('native/session','HOST_SESSION',observed_native=True,source='HOST_NATIVE',
        observed_at_us=200_000,session_id='session/1',session_epoch=4,
        authenticated_principal='worker/1',capability_digest='a'*64)
    return api.register_session('native/session')

def test_unsupported_and_fake_host_wake_do_not_certify_reentry():
    _,_,host,_,api=setup()
    assert api.observe_activation('session/1',['job/1','job/2']).status=='NOT_VERIFIED'
    native,_,host,_,api=setup(production=False)
    assert api.get_capability_manifest('fixture/provider').status=='NOT_VERIFIED'
    assert session(native,api).code=='EVIDENCE_INVALID'

def test_manifest_session_and_second_job_after_native_idle():
    native,store,host,_,api=setup()
    assert api.get_capability_manifest('fixture/provider').status=='OK'
    assert session(native,api).status=='OK'
    common={'session_id':'session/1','session_epoch':4,'authenticated_principal':'worker/1',
            'observed_native':True,'source':'HOST_NATIVE','project_id':'project/1',
            'feature_id':'feature/1','checkpoint_digest':'a'*64}
    native.sign('native/activation1','HOST_ACTIVATION',**common,observed_at_us=300_000,
                job_id='job/1',after_idle_ref=None)
    native.sign('native/activation2','HOST_ACTIVATION',**common,observed_at_us=500_000,
                job_id='job/2',after_idle_ref='native/idle')
    native.sign('native/idle','HOST_IDLE',observed_native=True,source='HOST_NATIVE',
                observed_at_us=400_000,session_id='session/1',session_epoch=4,
                authenticated_principal='worker/1')
    host.history=['native/activation1','native/idle','native/activation2']
    receipt=api.observe_activation('session/1',['job/1','job/2'])
    assert receipt.status=='OK'
    assert receipt.record['actual_host_observed'] is True
    assert receipt.record['job_id']=='job/2'
    assert store.db.execute('SELECT COUNT(*) FROM v1_host_activations').fetchone()[0]==2
    assert api.observe_activation('session/1',['job/1','job/2']).status=='OK'
    # Removing the native idle edge invalidates proof even if two activations exist.
    host.history=['native/activation1','native/activation2']
    assert api.observe_activation('session/1',['job/1','job/2']).code=='EVIDENCE_INVALID'

def test_reentry_requires_distinct_jobs_and_same_session_and_epoch():
    native,store,host,_,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    assert api.observe_activation('session/1',['job/1','job/1']).code=='INVALID_INPUT'
    native.sign('native/other','HOST_ACTIVATION',observed_native=True,source='HOST_NATIVE',
        observed_at_us=300_000,session_id='other/session',session_epoch=4,
        authenticated_principal='worker/1',job_id='job/1',project_id='project/1',
        feature_id='feature/1',checkpoint_digest='a'*64,after_idle_ref=None)
    host.history=['native/other']
    assert api.observe_activation('session/1',['job/1','job/2']).code=='EVIDENCE_INVALID'

def task():
    return {'project_id':'project/1','feature_id':'feature/1','task_id':'job/1',
      'graph_revision':1,'cabal_id':'cabal/1','bible_pair_digest':'a'*64,
      'feature_contract_digest':'b'*64,'dependencies':[],'required_skills':[],
      'owned_resources':[],'input_digests':[],'outputs':[],'acceptance_ids':[],
      'budget_limit_units':10,'task_digest':'d'*64}

def test_unknown_external_result_never_resends_even_on_repeat_call():
    native,_,host,journal,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    host.fail=True
    first=api.dispatch_task(task(),'native/session','c'*64)
    assert first.status=='UNKNOWN_EXTERNAL' and first.code=='PROVIDER_UNKNOWN'
    assert host.send_calls==1
    second=api.dispatch_task(task(),'native/session','c'*64)
    assert second.status=='UNKNOWN_EXTERNAL'
    assert host.send_calls==1
    host.fail=False
    assert len(journal.unknown)==2
    assert api.dispatch_task(task(),'native/session','d'*64).code=='IDEMPOTENCY_CONFLICT'

def test_successful_dispatch_proves_native_readback_and_fence_epoch():
    native,store,host,journal,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    result=api.dispatch_task(task(),'native/session','c'*64)
    assert result.status=='ACCEPTED' and result.receipt_ref=='native/effect'
    assert api.dispatch_task(task(),'native/session','c'*64).status=='ACCEPTED'
    assert host.send_calls==1
    fence=api.fence_session('session/1',4)
    assert fence.status=='ACCEPTED'
    assert store.read_one('v1_host_sessions','session_id=?',('session/1',))['session_epoch']==5
    assert api.fence_session('session/1',4).code=='LEASE_FENCED'

def test_manifest_observed_unsupported_is_not_optimistic():
    native,store,host,journal,api=setup()
    def unsupported(_):
        ref='native/manifest'
        caps={k:'SUPPORTED' for k in ('dispatch','checkpoint','same_session_reentry',
                     'fence','agent_execution','cancel')}
        caps['dispatch']='UNSUPPORTED'
        native.sign(ref,'HOST_MANIFEST',observed_native=True,source='HOST_NATIVE',
                    observed_at_us=200_000,capabilities=caps)
        return ref
    host.manifest_ref=unsupported
    assert api.get_capability_manifest('fixture/provider').record['capabilities']['dispatch']=='UNSUPPORTED'
    session(native,api)
    assert api.dispatch_task(task(),'native/session','c'*64).status=='UNSUPPORTED'

def test_host_activation_rejects_mixed_features_even_with_native_idle():
    native,store,host,journal,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    common={'session_id':'session/1','session_epoch':4,'authenticated_principal':'worker/1',
            'observed_native':True,'source':'HOST_NATIVE','project_id':'project/1',
            'checkpoint_digest':'a'*64}
    native.sign('native/a1','HOST_ACTIVATION',**common,observed_at_us=300_000,
                feature_id='feature/one',job_id='job/one',after_idle_ref=None)
    native.sign('native/idle','HOST_IDLE',observed_native=True,source='HOST_NATIVE',
                observed_at_us=400_000,session_id='session/1',session_epoch=4,
                authenticated_principal='worker/1')
    native.sign('native/a2','HOST_ACTIVATION',**common,observed_at_us=500_000,
                feature_id='feature/two',job_id='job/two',after_idle_ref='native/idle')
    host.history=['native/a1','native/idle','native/a2']
    assert api.observe_activation('session/1',['job/one','job/two']).code=='WRONG_FEATURE'

class AgentAuthority:
    def validate_agent_job(self,spec,needed):return True
    def session_for_job(self,job,epoch):return 'session/1'
    def authorize_cancel(self,job,epoch):return True

def test_native_cancel_is_job_scoped_and_does_not_fence_shared_session():
    native,store,host,journal,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    def native_cancel(job,epoch,key):
        payload={'session_id':'session/1','session_epoch':4,'job_id':job,
                 'attempt_epoch':epoch,'provider_id':'fixture/provider'}
        ref='native/cancel'
        native.sign(ref,'HOST_CANCEL',observed_native=True,source='HOST_NATIVE',
            observed_at_us=400_000,request_digest=sha(payload),effect_state='APPLIED',
            **{k:v for k,v in payload.items() if k!='provider_id'})
        host.effects[('HOST_CANCEL',key)]=ref
    host.cancel_job=native_cancel
    agent=AgentExecutionService(api,authority=AgentAuthority())
    assert agent.request_agent_cancel('job/1',3).status=='ACCEPTED'
    assert store.read_one('v1_host_sessions','session_id=?',('session/1',))['session_epoch']==4
    assert agent.request_agent_cancel('job/1',3).status=='ACCEPTED'

def test_revoked_session_cannot_reactivate_from_replayed_native_receipt():
    native,store,host,journal,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    store.db.execute("UPDATE v1_host_sessions SET observed_real=0 WHERE session_id='session/1'")
    store.db.commit()
    assert api.register_session('native/session').code=='LEASE_FENCED'

def test_agent_start_requires_native_agent_capability_and_current_task_authority():
    native,store,host,journal,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    agent=AgentExecutionService(api,authority=AgentAuthority())
    spec={'task':task(),'host_session_ref':'native/session'}
    assert agent.start_agent_job(spec,'c'*64,[]).status=='ACCEPTED'
    assert agent.start_agent_job(spec,'c'*64,[]).status=='ACCEPTED'
    assert host.send_calls==1
    assert agent.start_agent_job(spec,'c'*64,['unobserved_capability']).status=='NOT_VERIFIED'


def test_checkpoint_requires_native_receipt_and_exact_job_epoch():
    native,store,host,journal,api=setup()
    api.get_capability_manifest('fixture/provider');session(native,api)
    assert api.read_checkpoint('session/1','job/1',3).status=='NOT_VERIFIED'
    def checkpoint(session_id,job_id,epoch):
        ref='native/checkpoint'
        native.sign(ref,'HOST_CHECKPOINT',observed_native=True,source='HOST_NATIVE',
            observed_at_us=500_000,session_id=session_id,job_id=job_id,
            attempt_epoch=epoch,authenticated_principal='worker/1',checkpoint_digest='d'*64)
        return ref
    host.checkpoint_ref=checkpoint
    assert api.read_checkpoint('session/1','job/1',3).record['checkpoint_digest']=='d'*64
    # Readback for another job or epoch is refused, never reused as its receipt.
    def stale(session_id,job_id,epoch):return 'native/checkpoint'
    host.checkpoint_ref=stale
    assert api.read_checkpoint('session/1','job/other',3).code=='EVIDENCE_INVALID'
