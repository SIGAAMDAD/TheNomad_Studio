"""Full production APSW scoped transactions reject foreign-domain and stale writes."""
import pytest
apsw=pytest.importorskip('apsw')
from studio.storage.engine import ScopedStore, DomainBinding
from studio.storage.errors import Conflict, Forbidden, Invalid, Missing


def actor(project='p1'):
    return {'principal_id':'operator','issuer':'test',
            'allowed_project_ids':[project],'trusted_proof_ref':'bound'}


@pytest.fixture
def store(tmp_path):
    obj=ScopedStore(tmp_path/'scoped.sqlite',fresh=True)
    try:yield obj
    finally:obj.close()


def bound(store,table):
    return store.bind_domain(store._registry.tables[table].owner)


def begin(store,binding,project='p1',request='request'):
    return store.begin(actor=actor(project),project_id=project,request_id=request,domain_owner=binding)


@pytest.mark.parametrize('identity,project,request_id',[
    ({},'p1','r1'),(actor('p2'),'p1','r2'),(actor(),'','r3'),(actor(),'p1',''),
])
def test_invalid_identity_or_scope_never_begins_transaction(store,identity,project,request_id):
    domain=bound(store,'projects')
    with pytest.raises((Forbidden,Invalid)):
        store.begin(actor=identity,project_id=project,request_id=request_id,domain_owner=domain)
    assert store._conn.getautocommit()


def test_forged_or_late_domain_binding_rejected(store):
    domain=bound(store,'projects')
    forged=DomainBinding(domain.name,object())
    with pytest.raises(Forbidden):begin(store,forged)
    store.seal_bindings()
    with pytest.raises(Forbidden):store.bind_domain(domain.name)
    with begin(store,domain) as tx:
        assert tx.project_id=='p1'


def test_close_during_active_transaction_rejected_and_explicit_rollback(store):
    domain=bound(store,'projects')
    tx=begin(store,domain)
    with pytest.raises(Conflict,match='active transaction'):
        store.close()
    tx.rollback()
    with pytest.raises(Conflict,match='closed'):
        tx.commit()
    assert store._conn.getautocommit()


def test_nested_transaction_rejected_and_outer_transaction_survives(store):
    domain=bound(store,'projects')
    with begin(store,domain) as outer:
        with pytest.raises(Conflict,match='already open'):
            begin(store,domain,request='nested')
        assert outer.select_named('projects.count')==0
    assert store._conn.getautocommit()


def test_empty_project_list_and_disallowed_commands_fail_closed(store):
    domain=bound(store,'project_grants')
    with begin(store,domain) as tx:
        assert tx.select_named('project_grants.count')==0
        assert tx.select_named('project_grants.scan')==[]
        with pytest.raises(Forbidden):tx.select_named('projects.count')
        with pytest.raises(Forbidden):tx.execute_named('projects.insert',values={'name':'wrong'})
        with pytest.raises(Invalid):tx.select_named('project_grants.get')
        with pytest.raises(Invalid):tx.select_named('project_grants.scan',limit=0)
        with pytest.raises(Invalid):tx.select_named('project_grants.scan',limit=True)
        with pytest.raises(Invalid):tx.select_named('project_grants.scan',after=('bad',))
        with pytest.raises(Invalid):tx.select_named('project_grants.scan',filters={'not_a_column':1})
        with pytest.raises(Forbidden):tx.select_named('project_grants.count',filters={'project_id':'p2'})
        with pytest.raises(Invalid):tx.select_named('project_grants.sum',column='principal_id')


def test_scoped_write_rejects_cross_project_and_unknown_pk(store):
    domain=bound(store,'project_grants')
    with begin(store,domain) as tx:
        with pytest.raises(Forbidden):tx.execute_named('project_grants.insert',values={
            'project_id':'p2','principal_id':'p','grant_code':'member'})
        with pytest.raises(Invalid):tx.execute_named('project_grants.update',keys={},values={'grant_code':'member'})
        with pytest.raises(Invalid):tx.execute_named('project_grants.insert',values={'nonexistent_column':1})
        with pytest.raises((Missing,Conflict)):
            tx.execute_named('project_grants.delete',keys={
                'principal_id':'missing','grant_code':'member'})


def test_state_machine_cas_rejects_wrong_owner_and_generation(store):
    domain=bound(store,'project_grants')
    with begin(store,domain) as tx:
        with pytest.raises(Forbidden):
            tx.cas_lease(task_id='t',generation=1,old_status='active',new_status='released')
    other=bound(store,'leases')
    with begin(store,other) as tx:
        with pytest.raises(Forbidden):
            tx.cas_lease(task_id='t',generation=0,old_status='active',new_status='released')
        with pytest.raises(Forbidden):
            tx.cas_lease(task_id='t',generation=1,old_status='invalid',new_status='released')
        with pytest.raises(Conflict):
            tx.cas_lease(task_id='missing',generation=1,old_status='active',new_status='released')