from __future__ import annotations

from nomad_studio.budget.api import BudgetService
from tests.registry.support import SQLiteTestDriver, BUDGET


def test_budget_serial_ceiling_and_exact_replay():
    db=SQLiteTestDriver();db.seed()
    budget={'observed':True,'feature_id':'feature','project_id':'proj','budget_digest':BUDGET,
        'budget_limit_units':10,'cabal_id':'cabal','provider_id':'provider','reservation_epoch':1,
        'evidence_ref':'proof:budget'}
    measured={'observed':True,'reservation_id':'','provider_id':'provider','provider_receipt_ref':'charge:001',
              'measured_units':4,'is_final':False,'evidence_ref':'proof:billing'}
    service=BudgetService({'reader':db,'writer':db,'feature_budget_reader':lambda feature:budget,
        'provider_billing_reader':lambda reservation,claim:{**measured,'reservation_id':reservation},
        'clock_us':lambda:123456})
    reserved=service.reserve_budget('feature',7,BUDGET)
    assert reserved.allowed and reserved.status=='ALLOW' and reserved.code=='OK'
    assert service.reserve_budget('feature',7,BUDGET)==reserved
    assert service.reserve_budget('feature',4,BUDGET).code=='CAPACITY_LIMIT'
    assert service.reserve_budget('feature',0,BUDGET).code=='INVALID_INPUT'
    assert service.reserve_budget('feature',7,'0'*64).code=='EVIDENCE_INVALID'
    first=service.reconcile_spend(reserved.affected_ref,{'self_reported_units':9000})
    assert first.status=='OK' and first.record['measured_units_total']==4
    assert first.record['reserved_units']==3
    repeated=service.reconcile_spend(reserved.affected_ref,{'self_reported_units':0})
    assert repeated.status=='OK' and repeated.record['replay']
    measured['provider_receipt_ref']='charge:002'; measured['measured_units']=5
    assert service.reconcile_spend(reserved.affected_ref,{}).code=='CAPACITY_LIMIT'
    measured['measured_units']=2; measured['is_final']=True
    done=service.reconcile_spend(reserved.affected_ref,{})
    assert done.status=='OK' and done.record['measured_units_total']==6
    assert done.record['reserved_units']==0
    measured['provider_receipt_ref']='charge:003'; measured['measured_units']=1
    assert service.reconcile_spend(reserved.affected_ref,{}).code=='CAPACITY_LIMIT'
    # Released unused 1 unit; 6 measured. Can now reserve four more units.
    budget['reservation_epoch']=2
    new=service.reserve_budget('feature',4,BUDGET)
    assert new.allowed
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_budget_settlements').fetchone()[0])==2
    assert db.read(lambda c:c.execute('SELECT SUM(reserved_units+measured_units) FROM v1_budget_reservations').fetchone()[0])==10


def test_budget_cross_project_and_provider_spoof():
    db=SQLiteTestDriver();db.seed()
    budget={'observed':True,'feature_id':'feature','project_id':'evil','budget_digest':BUDGET,
        'budget_limit_units':10,'cabal_id':'cabal','provider_id':'provider','reservation_epoch':1,
        'evidence_ref':'proof:budget'}
    billing={'observed':True,'reservation_id':'','provider_id':'forged','provider_receipt_ref':'charge:bad',
        'measured_units':1,'is_final':True,'evidence_ref':'proof:billing'}
    svc=BudgetService({'reader':db,'writer':db,'feature_budget_reader':lambda x:budget,
        'provider_billing_reader':lambda r,x:{**billing,'reservation_id':r},'clock_us':lambda:10})
    assert svc.reserve_budget('feature',2,BUDGET).code=='WRONG_PROJECT'
    budget['project_id']='proj'
    res=svc.reserve_budget('feature',2,BUDGET)
    assert res.allowed
    assert svc.reconcile_spend(res.affected_ref,{}).code=='EVIDENCE_INVALID'
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_budget_settlements').fetchone()[0])==0
    assert BudgetService({'reader':db,'writer':db}).reconcile_spend(res.affected_ref,{}).status=='NOT_VERIFIED'
    assert BudgetService({'reader':db,'writer':db}).reserve_budget('feature',1,BUDGET).status=='UNKNOWN'


def test_receipt_collision_cross_reservation_conflicts():
    db=SQLiteTestDriver();db.seed()
    ctx={'observed':True,'feature_id':'feature','project_id':'proj','budget_digest':BUDGET,
        'budget_limit_units':10,'cabal_id':'cabal','provider_id':'provider','reservation_epoch':1,
        'evidence_ref':'proof:budget'}
    def observer(reservation,claim):
        return {'observed':True,'reservation_id':reservation,'provider_id':'provider',
                'provider_receipt_ref':'shared-receipt','measured_units':1,'is_final':True,
                'evidence_ref':'proof:billing'}
    svc=BudgetService({'reader':db,'writer':db,'feature_budget_reader':lambda x:ctx,
       'provider_billing_reader':observer,'clock_us':lambda:1})
    first=svc.reserve_budget('feature',3,BUDGET)
    ctx['reservation_epoch']=2
    second=svc.reserve_budget('feature',2,BUDGET)
    assert svc.reconcile_spend(first.affected_ref,{}).status=='OK'
    assert svc.reconcile_spend(second.affected_ref,{}).code=='IDEMPOTENCY_CONFLICT'


def test_unapproved_expanded_ceiling_cannot_override_frozen_active_contract():
    db=SQLiteTestDriver();db.seed()
    # Even if an injected observer incorrectly advertises a larger limit,
    # the active frozen feature contract caps the admitted reservation.
    context={'observed':True,'feature_id':'feature','project_id':'proj',
        'budget_digest':BUDGET,'budget_limit_units':100000,
        'cabal_id':'cabal','provider_id':'provider','reservation_epoch':1,
        'evidence_ref':'proof:budget'}
    svc=BudgetService({'reader':db,'writer':db,'feature_budget_reader':lambda _:context})
    denied=svc.reserve_budget('feature',11,BUDGET)
    assert denied.status=='DENY' and denied.code=='CAPACITY_LIMIT'
    assert db.read(lambda c:c.execute('SELECT COUNT(*) FROM v1_budget_reservations').fetchone()[0])==0
