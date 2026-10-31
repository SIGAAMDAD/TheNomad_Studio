"""F business rules: budget, consumption, closure and external-delivery evidence."""
from datetime import datetime, timezone
import pytest
from studio.operations.core import Operations, Scope, DomainError, required, amount, page_limit, is_sha256


class Tx:
    def __init__(self):
        self.rows={}
        self.changes=[]
        self.events=[]
        self.affected={}
    def select_named(self,name,parameters):
        row=self.rows.get(name)
        return row if isinstance(row,list) else ([row] if row is not None else [])
    def execute_named(self,name,parameters):
        self.changes.append((name,parameters))
        return self.affected.get(name,1)
    def record_event(self,**kwargs):self.events.append(kwargs)

class Runner:
    def __init__(self):self.tx=Tx();self.writes=[]
    def query(self,scope,fn):return fn(self.tx)
    def command(self,scope,*,owner,request_id,body_digest,fn):
        self.writes.append((owner,request_id,body_digest))
        return fn(self.tx)

class Usage:
    def __init__(self,valid=True):self.valid=valid;self.calls=0
    def verify_usage(self,**kwargs):self.calls+=1;return self.valid

class Closure:
    def __init__(self,valid=True):self.valid=valid;self.calls=0
    def verify_closure(self,**kwargs):self.calls+=1;return self.valid


def scope():return Scope('p','owner','signed',frozenset({'p'}))

def expect(code,run):
    with pytest.raises(DomainError) as exc:run()
    assert exc.value.code==code

@pytest.mark.parametrize('bad',(None,'', '  ',2, 'x'*513))
def test_required_field(bad):expect('invalid',lambda:required(bad,'field'))

@pytest.mark.parametrize('bad',(-1,2**63,True,'4',1.2,None))
def test_amount_must_fit_signed_64bit(bad):expect('invalid',lambda:amount(bad,'amount'))

@pytest.mark.parametrize('bad',(0,201,True,None,1.2))
def test_page_limit_validation(bad):expect('invalid',lambda:page_limit(bad))

@pytest.mark.parametrize('bad',('',None,'A'*64,'x'*63))
def test_sha_digest_rejects_forgery(bad):expect('invalid',lambda:is_sha256(bad))


def test_scope_must_be_authenticated_for_reads_and_writes():
    runner=Runner();svc=Operations(runner)
    bad=Scope('p','owner','',frozenset({'p'}))
    expect('invalid',lambda:svc.get_budgets(bad))
    bad=Scope('p','owner','signed',frozenset({'other'}))
    expect('forbidden',lambda:svc.get_budgets(bad))
    assert runner.writes == []


def test_budget_initial_configuration_and_read():
    runner=Runner();runner.tx.rows['f.budget.revision']={'revision':0}
    svc=Operations(runner)
    assert svc.configure_budget(scope(),budget_id='cpu',limit_amount=50,expected_revision=0,request_id='r') == {'budget_id':'cpu','limit_amount':50}
    assert runner.tx.changes[0][0]=='f.budget.insert'
    assert runner.tx.events[0]['event_type']=='budget.configured'
    runner.tx.rows['f.budget.list']=[{'budget_id':'cpu','limit_amount':50,'reserved_amount':0,'spent_amount':0}]
    assert svc.get_budgets(scope())['budgets'][0]['limit_amount']==50


def test_budget_reconfigure_enforces_revision_and_spend():
    runner=Runner();tx=runner.tx;svc=Operations(runner)
    tx.rows['f.budget.revision']={'revision':2}
    expect('stale_revision',lambda:svc.configure_budget(scope(),budget_id='cpu',limit_amount=30,expected_revision=1,request_id='r'))
    tx.rows['f.budget.one']={'spent_amount':20,'reserved_amount':20,'limit_amount':50}
    expect('conflict',lambda:svc.configure_budget(scope(),budget_id='cpu',limit_amount=30,expected_revision=2,request_id='r2'))
    tx.affected['f.budget.cas_limit']=0
    expect('stale_revision',lambda:svc.configure_budget(scope(),budget_id='cpu',limit_amount=60,expected_revision=2,request_id='r3'))
    tx.affected.clear()
    assert svc.configure_budget(scope(),budget_id='cpu',limit_amount=60,expected_revision=2,request_id='r4')['limit_amount']==60


def test_reserve_capacity_and_duplicate_rejections():
    runner=Runner();svc=Operations(runner)
    assert svc.reserve(scope(),reservation_id='res',budget_id='cpu',operation_id='build',value=4,request_id='r')['state']=='reserved'
    assert any(x[0]=='f.reservation.insert' for x in runner.tx.changes)
    runner.tx.rows['f.reservation.one']={'state':'reserved'}
    expect('conflict',lambda:svc.reserve(scope(),reservation_id='res',budget_id='cpu',operation_id='build',value=4,request_id='r2'))
    runner.tx.rows['f.reservation.one']=None
    runner.tx.affected['f.budget.reserve']=0
    expect('conflict',lambda:svc.reserve(scope(),reservation_id='res',budget_id='cpu',operation_id='build',value=4,request_id='r3'))


def test_reservation_lookup_filters_project():
    runner=Runner();svc=Operations(runner)
    runner.tx.rows['f.reservation.by_operation']=[{'reservation_id':'res','project_id':'p'}]
    assert svc.reservations_for_operation(scope(),operation_id='build') == [{'reservation_id':'res','project_id':'p'}]


def test_consume_requires_independent_proof_before_storage_write():
    runner=Runner();svc=Operations(runner)
    args=dict(reservation_id='res',usage_id='usage',category='build',provider='github',provider_ref='run',
              actual_amount=2,evidence_digest='a'*64,request_id='r')
    expect('capability_unavailable',lambda:svc.consume(scope(),**args))
    usage=Usage(False);svc=Operations(runner,usage_verifier=usage)
    expect('unknown_effect',lambda:svc.consume(scope(),**args))
    assert usage.calls==1 and runner.writes==[]
    expect('invalid',lambda:svc.consume(scope(),**{**args,'category':'model-inference'}))


def test_verified_consumption_is_bounded_to_reservation_and_unique_run():
    runner=Runner();tx=runner.tx;svc=Operations(runner,usage_verifier=Usage())
    args=dict(reservation_id='res',usage_id='usage',category='build',provider='github',provider_ref='run',
              actual_amount=2,evidence_digest='a'*64,request_id='r')
    expect('missing',lambda:svc.consume(scope(),**args))
    tx.rows['f.reservation.one']={'state':'released','amount':3,'budget_id':'cpu'}
    expect('conflict',lambda:svc.consume(scope(),**args))
    tx.rows['f.reservation.one']['state']='reserved'
    expect('conflict',lambda:svc.consume(scope(),**{**args,'actual_amount':4}))
    tx.rows['f.usage.by_provider']={'usage_id':'other'}
    expect('conflict',lambda:svc.consume(scope(),**args))
    tx.rows['f.usage.by_provider']=None
    tx.rows['f.usage.by_id']={'usage_id':'usage'}
    expect('conflict',lambda:svc.consume(scope(),**args))
    tx.rows['f.usage.by_id']=None
    tx.affected['f.budget.consume']=0
    expect('conflict',lambda:svc.consume(scope(),**args))
    tx.affected.clear()
    tx.affected['f.reservation.consume']=0
    expect('conflict',lambda:svc.consume(scope(),**args))
    tx.affected.clear()
    assert svc.consume(scope(),**args)=={'usage_id':'usage','state':'consumed','charged_amount':2,'released_amount':1}
    assert any(n=='f.usage.insert' for n,_ in tx.changes)


def test_closure_proof_is_checked_outside_atomic_write():
    runner=Runner();svc=Operations(runner)
    args=dict(reservation_id='res',request_id='r',reason='canceled')
    expect('missing',lambda:svc.release(scope(),**args))
    runner.tx.rows['f.reservation.one']={'state':'consumed','operation_id':'build','budget_id':'cpu','amount':8}
    expect('conflict',lambda:svc.release(scope(),**args))
    runner.tx.rows['f.reservation.one']['state']='reserved'
    expect('capability_unavailable',lambda:svc.release(scope(),**args))
    closure=Closure(False);svc=Operations(runner,closure_verifier=closure)
    expect('unknown_effect',lambda:svc.release(scope(),**args))
    assert not runner.writes and closure.calls==1
    closure.valid=True
    runner.tx.affected['f.budget.release']=0
    expect('conflict',lambda:svc.release(scope(),**args))
    runner.tx.affected.clear()
    runner.tx.affected['f.reservation.release']=0
    expect('conflict',lambda:svc.release(scope(),**args))
    runner.tx.affected.clear()
    assert svc.release(scope(),**args)=={'reservation_id':'res','state':'released'}


def test_notification_requires_evidence_for_sent_and_cas_for_ack():
    runner=Runner();tx=runner.tx;svc=Operations(runner)
    assert svc.emit_notification(scope(),notification_id='n',kind='failure',subject_id='t',request_id='create')['delivery_state']=='queued'
    tx.rows['f.notification.one']={'delivery_state':'queued'}
    expect('conflict',lambda:svc.emit_notification(scope(),notification_id='n',kind='failure',subject_id='t',request_id='duplicate'))
    tx.rows['f.notification.list']=[{'notification_id':'n','kind':'failure','delivery_state':'queued'}]
    assert svc.list_notifications(scope())['notifications'][0]['notification_id']=='n'
    expect('invalid',lambda:svc.ack_notification(scope(),notification_id='n',expected_state='invalid',request_id='a'))
    expect('invalid',lambda:svc.ack_notification(scope(),notification_id='n',expected_state='queued',request_id='a',observed_at=datetime(2026,1,1)))
    tx.affected['f.notification.ack']=0
    expect('stale_revision',lambda:svc.ack_notification(scope(),notification_id='n',expected_state='queued',request_id='a'))
    tx.affected.clear()
    assert svc.ack_notification(scope(),notification_id='n',expected_state='queued',request_id='a')['acknowledged']
    expect('conflict',lambda:svc.mark_delivery(scope(),kind='notification',record_id='n',new_state='queued',expected_state='queued',request_id='m'))
    expect('unknown_effect',lambda:svc.mark_delivery(scope(),kind='notification',record_id='n',new_state='sent',expected_state='queued',request_id='m'))
    tx.rows['f.effect.observed']={'effect_id':'observed'}
    assert svc.mark_delivery(scope(),kind='notification',record_id='n',new_state='sent',expected_state='queued',request_id='m')['delivery_state']=='sent'
    tx.rows['f.report.list']=[{'report_id':'r','delivery_state':'queued'}]
    assert svc.list_reports(scope())['reports'][0]['report_id']=='r'